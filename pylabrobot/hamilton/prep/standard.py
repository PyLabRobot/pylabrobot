"""Operation types for Hamilton Prep liquid handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple, Union

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


# ---------------------------------------------------------------------------
# 8-head operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Head8TipPickup:
  """Pick up tips with the 8MPH head.

  ``tip_spots[i]`` is the tip spot for active channel ``use_channels[i]``.
  """

  tip_spots: List[TipSpot]
  use_channels: Tuple[int, ...]
  offset: Coordinate
  tips: Sequence[Optional[Tip]]


@dataclass(frozen=True)
class Head8TipDrop:
  """Drop tips with the 8MPH head.

  ``resources[i]`` is the destination (TipSpot or Trash) for active channel ``use_channels[i]``.
  ``tips[i]`` carries the tip geometry so the backend can compute drop heights.
  """

  resources: List[Union[TipSpot, Trash]]
  use_channels: Tuple[int, ...]
  offset: Coordinate
  tips: Sequence[Optional[Tip]]


@dataclass(frozen=True)
class Head8AspirationWells:
  """Aspirate from an explicit list of wells using the 8MPH head.

  ``wells[i]`` is the well for active channel ``use_channels[i]``.
  Duplicate well entries are valid (e.g. 2 probes in one 24-well well).
  """

  wells: List[Well]
  use_channels: Tuple[int, ...]
  offset: Coordinate
  tips: Sequence[Optional[Tip]]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  mix: Optional[Mix]


@dataclass(frozen=True)
class Head8DispenseWells:
  """Dispense to an explicit list of wells using the 8MPH head.

  ``wells[i]`` is the well for active channel ``use_channels[i]``.
  """

  wells: List[Well]
  use_channels: Tuple[int, ...]
  offset: Coordinate
  tips: Sequence[Optional[Tip]]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  mix: Optional[Mix]


@dataclass(frozen=True)
class Head8AspirationContainer:
  """Aspirate from a single container (trough) using the 8MPH head."""

  container: Container
  use_channels: Tuple[int, ...]
  offset: Coordinate
  tips: Sequence[Optional[Tip]]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  mix: Optional[Mix]


@dataclass(frozen=True)
class Head8DispenseContainer:
  """Dispense to a single container (trough) using the 8MPH head."""

  container: Container
  use_channels: Tuple[int, ...]
  offset: Coordinate
  tips: Sequence[Optional[Tip]]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  mix: Optional[Mix]
