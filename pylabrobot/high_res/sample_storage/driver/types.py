from dataclasses import dataclass
from typing import Dict, Optional, Tuple

try:
  from typing import Literal
except ImportError:  # pragma: no cover
  from typing_extensions import Literal  # type: ignore


# State of a single TundraStore door, as reported by ``doorstatus``.
DoorState = Literal["open", "closed", "opening", "closing", "unknown"]
DOOR_STATES: Tuple[DoorState, ...] = ("open", "closed", "opening", "closing", "unknown")

# State of a transfer nest, as reported by ``neststatus``.
NestState = Literal["clear", "occupied", "unknown"]
NEST_STATES: Tuple[NestState, ...] = ("clear", "occupied", "unknown")


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
