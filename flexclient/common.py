"""Shared constants and diagnostics helpers for Flex SmartSDR client."""

import socket
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

DISCOVERY_PORT  = 4992          # UDP broadcast port for Flex discovery

FLEX_TCP_PORT   = 4992          # TCP command/control port

VITA_UDP_PORT   = 4991          # UDP port Flex sends IQ packets to (check your radio)

VITA_TYPE_IF_DATA   = 0x1       # IF data packet (IQ samples)

VITA_TYPE_EXT_DATA  = 0x3       # Extended data

SAMPLE_RATES = [24000, 48000, 96000, 192000]

SMARTSDR_STATUS_MESSAGES = {
    0x00000000: "Success",
    0x50000001: "Unable to get foundation receiver assignment",
    0x50000003: "License check failed, cannot create slice receiver",
    0x50000005: "Incorrect number or type of parameters",
    0x50000016: "Malformed command (parse error, e.g., frequency field)",
    0x5000002C: "Incorrect number of parameters",
    0x5000002D: "Bad field",
    0x50000063: "Operation not allowed (likely)",
    0x50001000: "Command handler rejection",
}

_UNMAPPED_STATUS_CODES_LOGGED: set[int] = set()

def _format_status_detail(status: int) -> str:
    hex_code = f"0x{status:08X}"
    message = SMARTSDR_STATUS_MESSAGES.get(status)
    if message:
        return f"{hex_code} ({message})"
    return f"{hex_code} (unmapped status code)"

def _maybe_log_unmapped_status_code(status: int):
    if status in SMARTSDR_STATUS_MESSAGES:
        return
    if status in _UNMAPPED_STATUS_CODES_LOGGED:
        return
    _UNMAPPED_STATUS_CODES_LOGGED.add(status)
    log.warning(
        "Encountered unmapped SmartSDR status code 0x%08X. "
        "Add it to SMARTSDR_STATUS_MESSAGES when meaning is confirmed.",
        status,
    )

def _pick_udp_listen_port() -> int:
    """Return a free ephemeral local UDP port."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()

