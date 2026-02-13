"""Inheco ODTC thermocycler implementation.

Preferred: use ODTCThermocycler (owns connection params and dimensions):

  tc = ODTCThermocycler(
    name="odtc1",
    odtc_ip="192.168.1.100",
    variant=384,
    child_location=Coordinate.zero(),
  )

Alternative: use generic Thermocycler with ODTCBackend (e.g. for custom backend):

  backend = ODTCBackend(odtc_ip="192.168.1.100", variant=384)
  tc = Thermocycler(
    name="odtc1",
    size_x=147,
    size_y=298,
    size_z=130,
    backend=backend,
    child_location=...,
  )

Variant accepts 96, 384 or device codes (960000, 384000). Use tc.run_protocol(protocol,
block_max_volume) for in-memory protocols; tc.run_stored_protocol("my_pcr") for
stored-by-name (ODTC only).
"""

from .odtc_backend import CommandExecution, MethodExecution, ODTCBackend
from .odtc_model import ODTC_DIMENSIONS, ProtocolList, StoredProtocol, normalize_variant
from .odtc_thermocycler import ODTCThermocycler

__all__ = [
  "CommandExecution",
  "MethodExecution",
  "ODTCBackend",
  "ODTC_DIMENSIONS",
  "ODTCThermocycler",
  "ProtocolList",
  "StoredProtocol",
  "normalize_variant",
]
