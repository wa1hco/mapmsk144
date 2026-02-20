"""VITA-49 UDP receiver for Flex DAXIQ streams."""

import socket
import threading
import queue
import struct
from typing import Optional

import numpy as np

from .common import VITA_UDP_PORT, log
from .models import VitaPacket

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

