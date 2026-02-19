"""
flex_client.py - Python client for FlexRadio SmartSDR DAXIQ stream
Handles discovery, TCP command/control, and UDP VITA-49 IQ packet reception.

Protocol reference: SmartSDR API documentation and kc2g-flex-tools/flexclient (Go)
"""

import socket
import threading
import queue
import struct
import time
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Callable

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

DISCOVERY_PORT  = 4992          # UDP broadcast port for Flex discovery
FLEX_TCP_PORT   = 4992          # TCP command/control port
VITA_UDP_PORT   = 4991          # UDP port Flex sends IQ packets to (check your radio)

# VITA-49 packet type nibbles
VITA_TYPE_IF_DATA   = 0x1       # IF data packet (IQ samples)
VITA_TYPE_EXT_DATA  = 0x3       # Extended data

# SmartSDR API sample rates available for DAXIQ
SAMPLE_RATES = [24000, 48000, 96000, 192000]

# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class FlexRadio:
    ip: str
    port: int
    model: str = ""
    serial: str = ""
    version: str = ""

@dataclass
class VitaPacket:
    """Unpacked VITA-49 IQ data packet."""
    stream_id:      int
    timestamp_int:  int         # integer seconds (GPS epoch or Unix)
    timestamp_frac: int         # fractional timestamp (picoseconds typically)
    sequence:       int
    samples:        np.ndarray  # complex64 array, I+jQ

# ─── Discovery ────────────────────────────────────────────────────────────────

def discover(timeout: float = 3.0) -> list[FlexRadio]:
    """
    Send UDP broadcast and listen for Flex radio responses.
    FlexRadio responds with a key=value status string.
    """
    radios = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # SO_REUSEPORT allows multiple processes to bind to the same port
    if hasattr(socket, 'SO_REUSEPORT'):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.settimeout(timeout)
    sock.bind(("", DISCOVERY_PORT))  # Bind to port 4992 to receive broadcasts

    log.info("Listening for FlexRadio discovery broadcasts...")

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
            log.debug(f"Received {len(data)} bytes from {addr[0]}")
            
            # FlexRadio sends VITA-49 formatted discovery packets
            # Parse VITA header to extract payload
            if len(data) < 8:
                log.debug(f"Packet too short: {len(data)} bytes")
                continue
            
            # Word 0: VITA header
            word0 = struct.unpack(">I", data[0:4])[0]
            has_class_id = (word0 >> 27) & 0x1
            log.debug(f"VITA header: 0x{word0:08x}, has_class_id={has_class_id}")
            
            # Word 1: stream ID
            offset = 8
            
            # Words 2-3: class ID (if present) - 8 bytes
            if has_class_id:
                if len(data) < offset + 8:
                    log.debug(f"Packet too short for class_id: {len(data)} bytes")
                    continue
                # Check for FlexRadio discovery packet (OUI=0x001c2d, PacketClass=0xffff)
                class_id = struct.unpack(">Q", data[offset:offset+8])[0]
                # VITA-49 Class ID structure (64 bits):
                # Bits 63-32: pad(8) + OUI(24) 
                # Bits 31-16: Information Class Code
                # Bits 15-0:  Packet Class Code
                oui = (class_id >> 32) & 0xFFFFFF
                packet_class = class_id & 0xFFFF
                log.debug(f"OUI=0x{oui:06x}, PacketClass=0x{packet_class:04x}")
                offset += 8
                
                if oui == 0x001c2d and packet_class == 0xffff:
                    # Extract payload (key=value string)
                    payload = data[offset:].decode("utf-8", errors="replace").rstrip('\x00')
                    log.info(f"Discovery response from {addr[0]}: {payload}")
                    radio = _parse_discovery(payload, addr[0])
                    if radio:
                        radios.append(radio)
                        # Stop after finding first radio to speed up single-radio setups
                        break
            else:
                log.debug("Packet has no class_id")
        except socket.timeout:
            break
        except Exception as e:
            log.warning(f"Discovery recv error: {e}", exc_info=True)

    sock.close()
    return radios

def _parse_discovery(msg: str, ip: str) -> Optional[FlexRadio]:
    """Parse SmartSDR discovery response into a FlexRadio object."""
    # Response format: key=value key=value ...
    # e.g. "radio ip=192.168.1.100 model=FLEX-6600 serial=... version=..."
    kv = {}
    for token in msg.split():
        if "=" in token:
            k, _, v = token.partition("=")
            kv[k.strip()] = v.strip()

    ip_addr = kv.get("ip", ip)
    model   = kv.get("model", "unknown")
    serial  = kv.get("serial", "")
    version = kv.get("version", "")

    return FlexRadio(ip=ip_addr, port=FLEX_TCP_PORT, model=model,
                     serial=serial, version=version)

# ─── TCP Command/Control ──────────────────────────────────────────────────────

class FlexTCPClient:
    """
    Manages the SmartSDR TCP command/control connection.
    Sends sequenced commands, receives status/response messages.
    """

    def __init__(self, radio: FlexRadio):
        self.radio      = radio
        self._sock      = None
        self._seq       = 1
        self._lock      = threading.Lock()
        self._responses = {}        # seq -> response string
        self._status_cb = None      # callback for unsolicited status messages
        self._running   = False
        self._recv_thread = None

    def connect(self):
        log.info(f"Connecting to {self.radio.ip}:{self.radio.port}")
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.connect((self.radio.ip, self.radio.port))
        self._sock.settimeout(None)
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        log.info("TCP connected")
    
    def get_local_ip(self) -> str:
        """Get the local IP address used for this connection."""
        if self._sock:
            return self._sock.getsockname()[0]
        return "0.0.0.0"

    def disconnect(self):
        self._running = False
        if self._sock:
            self._sock.close()

    def set_status_callback(self, cb: Callable[[str], None]):
        """Register callback for unsolicited status messages from the radio."""
        self._status_cb = cb

    def send_command(self, cmd: str, timeout: float = 5.0) -> str:
        """
        Send a sequenced command and wait for the response.
        Returns the response string (everything after the status code).
        Raises RuntimeError if command fails (non-zero status).
        """
        with self._lock:
            seq = self._seq
            self._seq += 1
            msg = f"C{seq}|{cmd}\n"
            ev = threading.Event()
            self._responses[seq] = (ev, None)

        log.debug(f"TX: {msg.strip()}")
        self._sock.sendall(msg.encode())

        ev.wait(timeout=timeout)
        with self._lock:
            _, result = self._responses.pop(seq, (None, None))
        
        if result is None:
            raise RuntimeError(f"Command timeout: {cmd}")
        
        status, response = result
        if status != 0:
            raise RuntimeError(f"Command failed (0x{status:08x}): {cmd} -> {response}")
        
        return response

    def _recv_loop(self):
        buf = b""
        while self._running:
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    log.warning("TCP connection closed by radio")
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._handle_line(line.decode("utf-8", errors="replace"))
            except Exception as e:
                if self._running:
                    log.error(f"TCP recv error: {e}")
                break

    def _handle_line(self, line: str):
        log.debug(f"RX: {line}")
        if line.startswith("R"):
            # Response to a sequenced command: R<seq>|<status>|<message>
            parts = line[1:].split("|", 2)
            if len(parts) >= 2:
                try:
                    seq = int(parts[0])
                    status = int(parts[1], 16)  # Status code in hex
                except ValueError:
                    return
                response = parts[2] if len(parts) > 2 else ""
                
                # Log errors
                if status != 0:
                    log.error(f"Command {seq} failed with status 0x{status:08x}: {response}")
                
                with self._lock:
                    if seq in self._responses:
                        ev, _ = self._responses[seq]
                        # Return tuple of (status, response)
                        self._responses[seq] = (ev, (status, response))
                        ev.set()
        elif line.startswith("S") or line.startswith("V"):
            # Unsolicited status or version message
            if self._status_cb:
                self._status_cb(line)

# ─── DAXIQ Setup ─────────────────────────────────────────────────────────────

class DAXIQSetup:
    """
    Handles the SmartSDR API calls needed to start a DAXIQ stream.
    Sequence: create panadapter -> create slice -> request daxiq -> subscribe
    """

    def __init__(self, tcp: FlexTCPClient, sample_rate: int = 96000,
                 dax_channel: int = 1):
        self.tcp         = tcp
        self.sample_rate = sample_rate
        self.dax_channel = dax_channel
        self.pan_id      = None
        self.slice_id    = None
        self.stream_id   = None
        self._old_status_cb = None
        self.slice_frequency_mhz = None  # Track actual slice frequency
        self.pan_frequency_mhz = None    # Track actual panadapter frequency
        self._created_pan = False        # Track if we created the panadapter
        
        # Set up persistent status callback
        self._old_status_cb = self.tcp._status_cb
        self.tcp._status_cb = self._status_monitor

    def setup(self, center_freq_mhz: float = 14.0) -> int:
        """
        Configure radio for DAXIQ and return the stream_id to filter on.
        Creates a new panadapter first, then attaches DAXIQ to it.
        If center_freq_mhz is provided, sets the panadapter frequency.
        """
        import time
        
        # Create a new panadapter for our exclusive use
        log.info(f"Creating panadapter for DAXIQ")
        try:
            # Calculate bandwidth in MHz (sample_rate in Hz)
            bandwidth_mhz = self.sample_rate / 1e6
            # Convert center frequency to Hz for the command
            freq_hz = int(center_freq_mhz * 1e6)
            resp = self.tcp.send_command(
                f"display pan create freq={freq_hz} ant=ANT1 rxant=ANT1 wide=0 loopa=0 loopb=0 band=0"
            )
            log.info(f"Panadapter create: {resp}")
            print(f"Panadapter create response: {resp}", flush=True)
            
            # Wait for panadapter status message
            time.sleep(0.3)
            
            if not self.pan_id:
                raise RuntimeError("Failed to get panadapter ID from status message")
            
            print(f"Created panadapter 0x{self.pan_id:08x}", flush=True)
            self._created_pan = True
            
            # Set bandwidth and other panadapter settings
            try:
                # Set bandwidth to match our sample rate
                resp = self.tcp.send_command(
                    f"display pansetting 0x{self.pan_id:08x} bandwidth={bandwidth_mhz:.6f}"
                )
                log.debug(f"Set panadapter bandwidth: {resp}")
            except RuntimeError as e:
                log.warning(f"Could not set panadapter bandwidth: {e}")
            
        except RuntimeError as e:
            log.warning(f"Could not create panadapter: {e}")
            print(f"WARNING: Could not create panadapter, will use existing one: {e}", flush=True)
            self.pan_id = None
        
        # Create DAXIQ stream - radio needs client IP to send UDP stream to
        # DAXIQ uses UDP port 4991 for I/Q data streaming
        client_ip = self.tcp.get_local_ip()
        log.info(f"Creating DAXIQ stream to {client_ip}:4991")
        
        # If we created a panadapter, try to assign DAXIQ to it
        if self._created_pan and self.pan_id:
            # First, remove any existing DAXIQ stream
            try:
                existing_streams = self.tcp.send_command("stream list")
                log.debug(f"Existing streams: {existing_streams}")
            except:
                pass
            
            # Try to assign DAXIQ channel to our panadapter
            try:
                resp = self.tcp.send_command(
                    f"dax iq set {self.dax_channel} pan=0x{self.pan_id:08x}"
                )
                log.info(f"Assigned DAXIQ channel {self.dax_channel} to panadapter 0x{self.pan_id:08x}: {resp}")
                print(f"DAXIQ assignment response: {resp}", flush=True)
                time.sleep(0.1)
            except RuntimeError as e:
                log.warning(f"Could not assign DAXIQ to panadapter (may be controlled by SmartSDR): {e}")
                print(f"WARNING: Could not assign DAXIQ to new panadapter: {e}", flush=True)
        
        resp = self.tcp.send_command(
            f"stream create daxiq={self.dax_channel} ip={client_ip} port=4991"
        )
        log.info(f"DAXIQ stream create: {resp}")
        print(f"Stream create response: {resp}", flush=True)
        self.stream_id = int(resp.strip(), 16) if resp.strip() else None
        if not self.stream_id:
            raise RuntimeError(f"Failed to create DAXIQ stream")
        print(f"DAXIQ stream created with ID: 0x{self.stream_id:08x}", flush=True)

        # Wait briefly for status message with slice/pan info
        time.sleep(0.2)  # Give radio time to send status

        # Subscribe to panadapter updates if we're in panadapter mode
        if self.pan_id:
            try:
                resp = self.tcp.send_command(f"sub pan 0x{self.pan_id:08x}")
                log.debug(f"Subscribed to panadapter: {resp}")
                print(f"Subscribed to panadapter 0x{self.pan_id:08x}", flush=True)
            except RuntimeError as e:
                log.warning(f"Could not subscribe to panadapter: {e}")

        # Try to configure the DAXIQ channel rate (may fail if already set in SmartSDR)
        try:
            resp = self.tcp.send_command(
                f"dax iq set {self.dax_channel} rate={self.sample_rate}"
            )
            log.debug(f"Set DAXIQ rate: {resp}")
        except RuntimeError as e:
            log.warning(f"Could not set DAXIQ rate (using SmartSDR default): {e}")
        
        # Set frequency based on mode (slice or panadapter)
        if center_freq_mhz is not None:
            if self.slice_id is not None and self.slice_id != 0:
                # Slice mode - set slice frequency
                try:
                    freq_hz = int(center_freq_mhz * 1e6)
                    resp = self.tcp.send_command(
                        f"slice set {self.slice_id} RF_frequency={freq_hz}"
                    )
                    log.info(f"Set slice {self.slice_id} frequency to {center_freq_mhz} MHz: {resp}")
                    print(f"Frequency command response: {resp}", flush=True)
                except RuntimeError as e:
                    log.warning(f"Could not set slice frequency: {e}")
                    print(f"ERROR setting frequency: {e}", flush=True)
            elif self.slice_id == 0 and self.pan_id is not None:
                # Panadapter mode - try different command formats
                try:
                    freq_hz = int(center_freq_mhz * 1e6)
                    # Try format 1: display pansetting with hex
                    resp = self.tcp.send_command(
                        f"display pansetting 0x{self.pan_id:08x} center={freq_hz}"
                    )
                    log.info(f"Set panadapter 0x{self.pan_id:08x} frequency to {center_freq_mhz} MHz: {resp}")
                    print(f"Panadapter frequency command response: {resp}", flush=True)
                except RuntimeError as e:
                    # Try format 2: display pan set without 0x prefix
                    try:
                        resp = self.tcp.send_command(
                            f"display pan set {self.pan_id:x} center={freq_hz}"
                        )
                        log.info(f"Set panadapter (alt format) frequency to {center_freq_mhz} MHz: {resp}")
                        print(f"Panadapter frequency command response (alt): {resp}", flush=True)
                    except RuntimeError as e2:
                        log.warning(f"Could not set panadapter frequency (tried both formats): {e}, {e2}")
                        print(f"ERROR setting panadapter frequency: {e}", flush=True)
                        print(f"Note: Panadapter frequency may be controlled by SmartSDR", flush=True)
            else:
                log.warning(f"DAXIQ channel {self.dax_channel} mode unknown (no slice or panadapter). Set frequency in SmartSDR.")
                print(f"WARNING: DAXIQ channel {self.dax_channel} not assigned!", flush=True)

        log.info(f"DAXIQ ready: stream_id=0x{self.stream_id:08x}")
        return self.stream_id
    
    def _status_monitor(self, line: str):
        """Monitor all status messages for stream and slice updates."""
        # Call old callback if it exists
        if self._old_status_cb:
            self._old_status_cb(line)
        
        # Monitor stream status for slice assignment
        if "|stream " in line and "dax_iq" in line:
            # Print full stream status for debugging
            print(f"Stream status: {line}", flush=True)
            
            # Parse the stream ID from the status message itself
            try:
                parts = line.split("|", 1)
                if len(parts) >= 2:
                    tokens = parts[1].split()
                    if len(tokens) >= 2 and tokens[0] == "stream":
                        msg_stream_id = int(tokens[1], 16)
                        
                        # If this is our stream (or we haven't set stream_id yet)
                        if self.stream_id is None or msg_stream_id == self.stream_id:
                            # Extract slice ID and panadapter ID
                            slice_id_str = _extract_key(parts[1], "slice")
                            pan_id_str = _extract_key(parts[1], "pan")
                            
                            if slice_id_str:
                                self.slice_id = int(slice_id_str, 16)
                                status = "ASSIGNED" if self.slice_id != 0 else "NOT ASSIGNED"
                                print(f"DAXIQ stream 0x{msg_stream_id:08x} slice status: 0x{self.slice_id:x} ({status})", flush=True)
                            
                            if pan_id_str:
                                self.pan_id = int(pan_id_str, 16)
                                print(f"DAXIQ stream 0x{msg_stream_id:08x} panadapter: 0x{self.pan_id:08x}", flush=True)
            except (ValueError, TypeError, IndexError) as e:
                log.debug(f"Error parsing stream status: {e}")
        
        # Monitor slice frequency updates
        if self.slice_id and self.slice_id != 0 and "|slice " in line:
            try:
                parts = line.split("|", 1)
                if len(parts) >= 2:
                    tokens = parts[1].split()
                    if len(tokens) >= 2 and tokens[0] == "slice":
                        slice_num = int(tokens[1])
                        if slice_num == self.slice_id:
                            # Extract RF_frequency
                            freq_str = _extract_key(parts[1], "RF_frequency")
                            if freq_str:
                                freq_hz = int(freq_str)
                                self.slice_frequency_mhz = freq_hz / 1e6
                                print(f"Slice {self.slice_id} frequency: {self.slice_frequency_mhz:.6f} MHz", flush=True)
            except (ValueError, TypeError, IndexError) as e:
                log.debug(f"Error parsing slice status: {e}")
        
        # Monitor panadapter frequency updates
        if "|display pan " in line:
            try:
                parts = line.split("|", 1)
                if len(parts) >= 2:
                    tokens = parts[1].split()
                    if len(tokens) >= 3 and tokens[0] == "display" and tokens[1] == "pan":
                        pan_id = int(tokens[2], 16)
                        
                        # If we don't have a pan_id yet, capture it
                        if self.pan_id is None:
                            self.pan_id = pan_id
                            print(f"Captured panadapter ID: 0x{self.pan_id:08x}", flush=True)
                        
                        # Monitor frequency updates for our panadapter
                        if self.pan_id and pan_id == self.pan_id:
                            # Extract center frequency
                            center_str = _extract_key(parts[1], "center")
                            if center_str:
                                center_mhz = _parse_freq_to_mhz(center_str)
                                if center_mhz is not None:
                                    self.pan_frequency_mhz = center_mhz
                                    print(f"Panadapter 0x{self.pan_id:08x} center frequency: {self.pan_frequency_mhz:.6f} MHz", flush=True)
            except (ValueError, TypeError, IndexError) as e:
                log.debug(f"Error parsing panadapter status: {e}")

    def teardown(self):
        """Clean up DAXIQ resources. Doesn't raise on errors during cleanup."""
        # Remove the stream
        if self.stream_id:
            try:
                self.tcp.send_command(f"stream remove 0x{self.stream_id:08x}")
            except Exception as e:
                log.warning(f"Failed to remove stream: {e}")
        
        # Remove the panadapter if we created it
        if self._created_pan and self.pan_id:
            try:
                self.tcp.send_command(f"display pan remove 0x{self.pan_id:08x}")
                log.info(f"Removed panadapter 0x{self.pan_id:08x}")
            except Exception as e:
                log.warning(f"Failed to remove panadapter: {e}")


def _extract_key(response: str, key: str) -> Optional[str]:
    """Extract a value from a key=value response string."""
    for token in response.split():
        if token.startswith(key + "="):
            return token.split("=", 1)[1]
    return None


def _parse_freq_to_mhz(freq_value: str) -> Optional[float]:
    """Parse SmartSDR frequency field that may be reported in Hz or MHz."""
    try:
        value = float(freq_value)
    except (TypeError, ValueError):
        return None

    return value / 1e6 if value >= 1e6 else value

# ─── VITA-49 UDP Receiver ─────────────────────────────────────────────────────

class VITAReceiver:
    """
    Receives VITA-49 UDP packets from the Flex and unpacks IQ samples.
    Delivers VitaPacket objects to a queue for downstream processing.
    """

    def __init__(self, listen_port: int = VITA_UDP_PORT,
                 stream_id: Optional[int] = None,
                 output_queue: Optional[queue.Queue] = None):
        self.listen_port  = listen_port
        self.filter_sid   = stream_id      # None = accept all streams
        self.out_q        = output_queue or queue.Queue(maxsize=200)
        self._sock        = None
        self._running     = False
        self._thread      = None
        self.packet_count  = 0
        self.drop_count    = 0
        self.missed_count  = 0
        self._last_seq     = {}   # stream_id -> last 4-bit sequence number

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self._sock.bind(("", self.listen_port))
        self._sock.settimeout(1.0)
        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()
        log.info(f"VITA receiver listening on UDP:{self.listen_port}")

    def stop(self):
        self._running = False
        if self._sock:
            self._sock.close()

    def _recv_loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65536)
                pkt = self._unpack(data)
                if pkt is None:
                    continue
                if self.filter_sid and pkt.stream_id != self.filter_sid:
                    continue
                self.packet_count += 1
                
                # Check for dropped packets via 4-bit sequence number
                sid = pkt.stream_id
                if sid in self._last_seq:
                    expected = (self._last_seq[sid] + 1) & 0xF
                    if pkt.sequence != expected:
                        missed = (pkt.sequence - expected) & 0xF
                        self.missed_count += missed
                        log.warning(f"Sequence gap on stream 0x{sid:08x}: "
                                    f"expected {expected}, got {pkt.sequence} "
                                    f"({missed} packets missed)")
                self._last_seq[sid] = pkt.sequence

                try:
                    self.out_q.put_nowait(pkt)
                except queue.Full:
                    self.drop_count += 1
                    log.warning(f"VITA queue full, dropping packet "
                                f"(total drops: {self.drop_count})")
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    log.error(f"VITA recv error: {e}")

    # Note: DAXIQ uses IEEE-754 32-bit floats (not fixed-point integers)

    def _unpack(self, data: bytes) -> Optional[VitaPacket]:
        if len(data) < 4:
            return None

        # ── Word 0: VITA-49 header (big-endian throughout) ──────────────────
        # Bits 31-28: packet type (0x1 = IF Data with stream id)
        # Bit  27:    class id present
        # Bit  26:    trailer present
        # Bits 25-24: reserved
        # Bits 23-22: TSI (integer timestamp type: 0=none,1=UTC,2=GPS,3=other)
        # Bits 21-20: TSF (fractional timestamp type: 0=none,1=sample count,
        #                   2=real time picoseconds, 3=free running)
        # Bits 19-16: packet sequence number (4-bit, wraps at 16)
        # Bits 15-0:  packet size in 32-bit words (including header)
        word0        = struct.unpack_from(">I", data, 0)[0]
        pkt_type     = (word0 >> 28) & 0xF
        has_class_id = (word0 >> 27) & 0x1
        has_trailer  = (word0 >> 26) & 0x1
        tsi          = (word0 >> 22) & 0x3
        tsf          = (word0 >> 20) & 0x3
        sequence     = (word0 >> 16) & 0xF
        pkt_size_words = word0 & 0xFFFF

        offset = 4

        # ── Word 1: Stream ID (always present for IF Data packets) ──────────
        if len(data) < offset + 4:
            return None
        stream_id = struct.unpack_from(">I", data, offset)[0]
        offset += 4

        # ── Words 2-3: Class ID (OUI + packet class, 8 bytes if present) ────
        # Upper 32 bits: pad(8) + OUI(24)
        # Lower 32 bits: information class code(16) + packet class code(16)
        class_id = None
        if has_class_id:
            if len(data) < offset + 8:
                return None
            class_id = struct.unpack_from(">Q", data, offset)[0]
            offset += 8

        # ── Integer timestamp (4 bytes if TSI != 0) ──────────────────────────
        timestamp_int = 0
        if tsi != 0:
            if len(data) < offset + 4:
                return None
            timestamp_int = struct.unpack_from(">I", data, offset)[0]
            offset += 4

        # ── Fractional timestamp (8 bytes if TSF != 0) ───────────────────────
        # For Flex: TSF=1 -> sample count, TSF=2 -> picoseconds real time
        timestamp_frac = 0
        if tsf != 0:
            if len(data) < offset + 8:
                return None
            timestamp_frac = struct.unpack_from(">Q", data, offset)[0]
            offset += 8

        # ── Payload: interleaved I/Q as IEEE-754 32-bit floats, big-endian ─
        # Trim trailer if present (1 word = 4 bytes at end of packet)
        payload_end = pkt_size_words * 4
        if has_trailer:
            payload_end -= 4
        payload = data[offset:payload_end]

        n_words = len(payload) // 4
        if n_words < 2:
            return None

        # Unpack as little-endian IEEE-754 32-bit floats (DAXIQ format: payload_endian=little)
        raw = np.frombuffer(payload[:n_words * 4], dtype="<f4")
        
        # FlexRadio DAXIQ sends float values that need no scaling
        # They are already in the correct range for direct FFT processing
        
        # Interleaved I, Q pairs -> complex64
        n_samples = n_words // 2
        samples = raw[0::2] + 1j * raw[1::2]
        samples = samples.astype(np.complex64)

        return VitaPacket(
            stream_id=stream_id,
            timestamp_int=timestamp_int,
            timestamp_frac=timestamp_frac,
            sequence=sequence,
            samples=samples
        )

# ─── Top-level convenience class ─────────────────────────────────────────────

class FlexDAXIQ:
    """
    High-level interface: discover radio, connect, start DAXIQ stream,
    deliver IQ sample blocks via a queue.
    """

    def __init__(self, radio_ip: Optional[str] = None,
                 center_freq_mhz: float = 14.0,
                 sample_rate: int = 96000,
                 dax_channel: int = 1):
        self.radio_ip        = radio_ip
        self.center_freq_mhz = center_freq_mhz
        self.sample_rate     = sample_rate
        self.dax_channel     = dax_channel
        self.sample_queue    = queue.Queue(maxsize=500)
        self._tcp            = None
        self._dax_setup      = None
        self._vita           = None

    def start(self):
        # Discover or use provided IP
        if self.radio_ip:
            radio = FlexRadio(ip=self.radio_ip, port=FLEX_TCP_PORT)
        else:
            radios = discover()
            if not radios:
                raise RuntimeError("No FlexRadio found on network")
            radio = radios[0]
            log.info(f"Found radio: {radio.model} at {radio.ip}")

        # Connect TCP
        self._tcp = FlexTCPClient(radio)
        self._tcp.connect()
        time.sleep(0.5)  # let the radio send its initial status burst

        # Setup DAXIQ
        self._dax_setup = DAXIQSetup(self._tcp, self.sample_rate, self.dax_channel)
        stream_id = self._dax_setup.setup(self.center_freq_mhz)

        # Start VITA receiver
        self._vita = VITAReceiver(
            listen_port=VITA_UDP_PORT,
            stream_id=stream_id,
            output_queue=self.sample_queue
        )
        self._vita.start()
        log.info("DAXIQ stream running")

    def stop(self):
        if self._vita:
            self._vita.stop()
        if self._dax_setup:
            self._dax_setup.teardown()
        if self._tcp:
            self._tcp.disconnect()

    def get_samples(self, timeout: float = 1.0) -> Optional[VitaPacket]:
        """Block until a packet arrives or timeout. Returns VitaPacket or None."""
        try:
            return self.sample_queue.get(timeout=timeout)
        except queue.Empty:
            return None

# ─── Quick test / verification ───────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FlexRadio DAXIQ test receiver")
    parser.add_argument("--ip",   default=None,  help="Radio IP (auto-discover if omitted)")
    parser.add_argument("--freq", default=50.260, type=float, help="Center freq MHz")
    parser.add_argument("--rate", default=96000,  type=int,   help="Sample rate Hz")
    parser.add_argument("--secs", default=5,      type=int,   help="Seconds to run")
    args = parser.parse_args()

    client = FlexDAXIQ(
        radio_ip=args.ip,
        center_freq_mhz=args.freq,
        sample_rate=args.rate
    )

    try:
        client.start()
        t_end = time.time() + args.secs
        total_samples = 0
        packet_count  = 0

        while time.time() < t_end:
            pkt = client.get_samples(timeout=0.5)
            if pkt:
                packet_count  += 1
                total_samples += len(pkt.samples)
                if packet_count % 50 == 0:
                    ts = pkt.timestamp_int + pkt.timestamp_frac * 1e-12
                    log.info(f"Packets: {packet_count}  Samples: {total_samples}  "
                             f"Last timestamp: {ts:.3f}  "
                             f"Stream: 0x{pkt.stream_id:08x}")

        log.info(f"Done. {packet_count} packets, {total_samples} samples, "
                 f"{client._vita.drop_count} drops")

    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        client.stop()
