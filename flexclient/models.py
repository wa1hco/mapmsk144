"""Data structures for FlexRadio discovery and VITA packets."""

from dataclasses import dataclass, field
import numpy as np

@dataclass
class FlexRadio:
    ip: str
    port: int
    model: str = ""
    serial: str = ""
    version: str = ""
    gui_client_handles: list[str] = field(default_factory=list)
    gui_client_ids: list[str] = field(default_factory=list)

@dataclass
class VitaPacket:
    """Unpacked VITA-49 IQ data packet."""
    stream_id:      int
    timestamp_int:  int         # integer seconds (GPS epoch or Unix)
    timestamp_frac: int         # fractional timestamp (picoseconds typically)
    sequence:       int
    samples:        np.ndarray  # complex64 array, I+jQ

