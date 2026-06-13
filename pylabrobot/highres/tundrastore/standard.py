from dataclasses import dataclass
from typing import Dict, Optional

from .constants import DoorState, NestState


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
class DoorStatus:
  """Parsed output of the ``doorstatus`` command, keyed by door name."""

  doors: Dict[str, DoorState]

  @property
  def all_closed(self) -> bool:
    return all(state is DoorState.CLOSED for state in self.doors.values())


@dataclass
class NestStatus:
  """Parsed output of the ``neststatus`` command, keyed by nest number."""

  nests: Dict[int, NestState]


@dataclass
class StackerDimensions:
  """One stacker's geometry, from ``getstackerdimensions``."""

  stacker: int
  zero_offset: float
  slot_height: float
  slot_count: int
