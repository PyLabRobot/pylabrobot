"""Inheco ODTC thermocycler implementation.

  backend = ODTCBackend(odtc_ip="192.168.1.100", variant=384)
  tc = Thermocycler(
    name="odtc1",
    size_x=156.5,
    size_y=248,
    size_z=124.3,
    backend=backend,
    child_location=...,
  )

Variant accepts 96 or 384 (device codes like 960000 also accepted and normalized).
Use tc.run_protocol(protocol, block_max_volume) for in-memory protocols;
tc.run_stored_protocol("my_pcr") for stored-by-name (ODTC only).
"""

from .odtc_backend import ODTCBackend
from .odtc_model import (
  ODTC_DIMENSIONS,
  ODTCProgress,
  ODTCProtocol,
  ODTCVariant,
  normalize_variant,
)

__all__ = [
  "ODTCBackend",
  "ODTC_DIMENSIONS",
  "ODTCProgress",
  "ODTCProtocol",
  "ODTCVariant",
  "normalize_variant",
]
