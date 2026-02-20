"""High-level Flex DAXIQ client orchestration."""

import queue
import time
from typing import Optional

from .common import FLEX_TCP_PORT, VITA_UDP_PORT, _pick_udp_listen_port, log
from .discovery import discover
from .models import FlexRadio, VitaPacket
from .setup import DAXIQSetup
from .tcp_client import FlexTCPClient
from .vita import VITAReceiver

class FlexDAXIQ:
    """
    High-level interface: discover radio, connect, start DAXIQ stream,
    deliver IQ sample blocks via a queue.
    """

    def __init__(self, radio_ip: Optional[str] = None,
                 center_freq_mhz: float = 14.0,
                 sample_rate: int = 96000,
                 dax_channel: int = 1,
                 listen_port: int = VITA_UDP_PORT,
                 bind_client_id: Optional[str] = None,
                 bind_client_handle: Optional[str] = None):
        self.radio_ip        = radio_ip
        self.center_freq_mhz = center_freq_mhz
        self.sample_rate     = sample_rate
        self.dax_channel     = dax_channel
        self.listen_port     = listen_port
        self.bind_client_id = bind_client_id or bind_client_handle
        self.sample_queue    = queue.Queue(maxsize=500)
        self._tcp            = None
        self._dax_setup      = None
        self._vita           = None

    def _log_bound_context_diagnostics(self):
        """Log bound-context diagnostics via supported list commands."""

        try:
            slice_list_resp = self._tcp.send_command("slice list")
            log.info(f"slice list response payload: {slice_list_resp!r}")
            visible_slice_labels = []
            for raw_line in slice_list_resp.splitlines():
                line = raw_line.strip()
                if line:
                    first_token = line.split()[0]
                    try:
                        slice_num = int(first_token)
                    except ValueError:
                        continue

                    if 0 <= slice_num < 26:
                        visible_slice_labels.append(chr(ord('A') + slice_num))
                    else:
                        visible_slice_labels.append(str(slice_num))

            if visible_slice_labels:
                log.info(f"Visible slices: {', '.join(visible_slice_labels)}")
        except RuntimeError as e:
            log.warning(f"slice list failed: {e}")

    def _request_client_status(self):
        """Ask radio to publish client status lines so GUI client UUIDs can be discovered."""
        list_lines, parsed_count = self._tcp.refresh_client_list()
        if list_lines > 0:
            log.info(f"Client list lines={list_lines}, parsed_gui_client_ids={parsed_count}")
        for cmd in ("sub client all", "sub client"):
            try:
                self._tcp.send_command(cmd)
                log.debug(f"Subscribed for client status with command: {cmd}")
                return
            except RuntimeError:
                continue
        log.debug("Client status subscription command not accepted by radio")

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

        self._request_client_status()
        time.sleep(0.5)

        gui_clients = self._tcp.get_gui_clients()
        if gui_clients:
            log.info("GUI clients discovered:")
            for idx, item in enumerate(gui_clients):
                log.info(
                    "  [%d] station=%s program=%s host=%s ip=%s handle=%s client_id=%s",
                    idx,
                    item.get("station", "n/a") or "n/a",
                    item.get("program", "n/a") or "n/a",
                    item.get("host", "n/a") or "n/a",
                    item.get("ip", "n/a") or "n/a",
                    item.get("handle", "n/a") or "n/a",
                    item.get("client_id", "n/a") or "n/a",
                )
        else:
            log.info("GUI clients discovered: none with client_id yet")

        if radio.gui_client_ids:
            log.info(f"Discovery advertised GUI client_ids: {', '.join(radio.gui_client_ids)}")

        bind_client_id = self.bind_client_id
        if not bind_client_id:
            status_ids = self._tcp.get_gui_client_ids()
            if status_ids:
                bind_client_id = status_ids[0]
            elif radio.gui_client_ids:
                bind_client_id = radio.gui_client_ids[0]
            else:
                log.info("No GUI client_id available for auto-bind")

        if bind_client_id:
            bind_cmd = f"client bind client_id={bind_client_id}"
            log.info(f"Sending bind command: {bind_cmd}")
            try:
                self._tcp.send_command(bind_cmd)
                log.info(f"Bound to GUI client_id {bind_client_id}")
                self._log_bound_context_diagnostics()
            except RuntimeError as e:
                log.warning(f"Could not bind to GUI client_id {bind_client_id}: {e}")
                log.warning(f"Bind command failed: {bind_cmd}")

        selected_port = _pick_udp_listen_port()
        log.info(f"Using UDP:{selected_port} for DAXIQ stream")
        self.listen_port = selected_port

        self._dax_setup = DAXIQSetup(
            self._tcp,
            self.sample_rate,
            self.dax_channel,
            self.listen_port,
        )
        time.sleep(0.5)  # let status burst populate existing pan/slice state

        # Setup DAXIQ
        stream_id = self._dax_setup.setup(self.center_freq_mhz)

        # Start VITA receiver
        self._vita = VITAReceiver(
            listen_port=self.listen_port,
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

