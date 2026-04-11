"""Standard types for liquid handling operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Sequence, Union

from pylabrobot.resources import Coordinate

if TYPE_CHECKING:
  from pylabrobot.resources import Container, Tip, TipRack, TipSpot, Trash, Well


@dataclass(frozen=True)
class Mix:
  """Mix parameters for aspiration/dispense operations."""

  volume: float
  repetitions: int
  flow_rate: float


# ---------------------------------------------------------------------------
# Independent channel operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pickup:
  """Pick up a tip from a tip spot."""

  resource: TipSpot
  offset: Coordinate
  tip: Tip


@dataclass(frozen=True)
class TipDrop:
  """Drop a tip to a tip spot or trash."""

  resource: Union[TipSpot, Trash]
  offset: Coordinate
  tip: Tip


@dataclass(frozen=True)
class Aspiration:
  """Aspirate liquid from a container using an independent channel."""

  resource: Container
  offset: Coordinate
  tip: Tip
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  mix: Optional[Mix]


@dataclass(frozen=True)
class Dispense:
  """Dispense liquid to a container using an independent channel."""

  resource: Container
  offset: Coordinate
  tip: Tip
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  mix: Optional[Mix]


# ---------------------------------------------------------------------------
# 96-head operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PickupTipRack:
  """Pick up tips from a tip rack using the 96-head."""

  resource: TipRack
  offset: Coordinate
  tips: Sequence[Optional[Tip]]


@dataclass(frozen=True)
class DropTipRack:
  """Drop tips to a tip rack or trash using the 96-head."""

  resource: Union[TipRack, Trash]
  offset: Coordinate


@dataclass(frozen=True)
class MultiHeadAspirationPlate:
  """Aspirate from wells in a plate using the 96-head."""

  wells: List[Well]
  offset: Coordinate
  tips: Sequence[Optional[Tip]]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  mix: Optional[Mix]


@dataclass(frozen=True)
class MultiHeadDispensePlate:
  """Dispense to wells in a plate using the 96-head."""

  wells: List[Well]
  offset: Coordinate
  tips: Sequence[Optional[Tip]]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  mix: Optional[Mix]


@dataclass(frozen=True)
class MultiHeadAspirationContainer:
  """Aspirate from a single container (trough) using the 96-head."""

  container: Container
  offset: Coordinate
  tips: Sequence[Optional[Tip]]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  mix: Optional[Mix]


@dataclass(frozen=True)
class MultiHeadDispenseContainer:
  """Dispense to a single container (trough) using the 96-head."""

  container: Container
  offset: Coordinate
  tips: Sequence[Optional[Tip]]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  mix: Optional[Mix]
