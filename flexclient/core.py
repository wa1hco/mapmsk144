"""Public Flex client API and CLI entrypoint."""

import time

from .common import (
    DISCOVERY_PORT,
    FLEX_TCP_PORT,
    VITA_UDP_PORT,
    VITA_TYPE_IF_DATA,
    VITA_TYPE_EXT_DATA,
    SAMPLE_RATES,
    SMARTSDR_STATUS_MESSAGES,
    _format_status_detail,
    _maybe_log_unmapped_status_code,
    _pick_udp_listen_port,
    log,
)
from .models import FlexRadio, VitaPacket
from .discovery import discover, _parse_discovery, _format_discovery_summary
from .tcp_client import FlexTCPClient
from .setup import DAXIQSetup, _extract_key, _parse_freq_to_mhz
from .vita import VITAReceiver
from .client import FlexDAXIQ

__all__ = [
    "DISCOVERY_PORT",
    "FLEX_TCP_PORT",
    "VITA_UDP_PORT",
    "VITA_TYPE_IF_DATA",
    "VITA_TYPE_EXT_DATA",
    "SAMPLE_RATES",
    "SMARTSDR_STATUS_MESSAGES",
    "_format_status_detail",
    "_maybe_log_unmapped_status_code",
    "_pick_udp_listen_port",
    "FlexRadio",
    "VitaPacket",
    "discover",
    "_parse_discovery",
    "_format_discovery_summary",
    "FlexTCPClient",
    "DAXIQSetup",
    "_extract_key",
    "_parse_freq_to_mhz",
    "VITAReceiver",
    "FlexDAXIQ",
]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FlexRadio DAXIQ test receiver")
    parser.add_argument("--ip",   default=None,  help="Radio IP (auto-discover if omitted)")
    parser.add_argument("--freq", default=50.260, type=float, help="Center freq MHz")
    parser.add_argument("--rate", default=96000,  type=int,   help="Sample rate Hz")
    parser.add_argument("--bind-client-id", default=None, help="GUI client UUID for client bind client_id=<uuid>")
    parser.add_argument("--bind-client", default=None, help="Deprecated alias of --bind-client-id")
    parser.add_argument("--secs", default=5,      type=int,   help="Seconds to run")
    args = parser.parse_args()

    client = FlexDAXIQ(
        radio_ip=args.ip,
        center_freq_mhz=args.freq,
        sample_rate=args.rate,
        bind_client_id=args.bind_client_id or args.bind_client,
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
