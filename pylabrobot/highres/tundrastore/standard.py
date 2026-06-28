import enum
from dataclasses import dataclass
from typing import Dict, Optional


class DoorState(enum.Enum):
  """State of a single TundraStore door, as reported by ``doorstatus``."""

  OPEN = "OPEN"
  CLOSED = "CLOSED"
  OPENING = "OPENING"
  CLOSING = "CLOSING"
  UNKNOWN = "UNKNOWN"


class NestState(enum.Enum):
  """State of a transfer nest, as reported by ``neststatus``."""

  CLEAR = "CLEAR"
  OCCUPIED = "OCCUPIED"
  UNKNOWN = "UNKNOWN"


@dataclass
class VersionInfo:
  """Parsed output of the ``version`` command."""

  product_name: Optional[str]
  serial_number: Optional[str]
  firmware_version: Optional[str]
  firmware_build: Optional[str]
  raw: Dict[str, str]


@dataclass
class EnvironmentParameter:
  """One row of ``environmentstatus`` (e.g. TEMP, RH, CO2, O2).

  The device reports ``NAME:current/setpoint/limit``. ``setpoint``/``limit`` are
  ``None`` for sensor-only channels (e.g. the gas tank pressures).
  """

  name: str
  current: float
  setpoint: Optional[float] = None
  limit: Optional[float] = None


@dataclass
class StackerDimensions:
  """One stacker's geometry, from ``getstackerdimensions``."""

  stacker: int
  zero_offset: float
  slot_height: float
  slot_count: int
