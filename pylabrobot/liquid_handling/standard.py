"""Data structures for the standard form of liquid handling."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple, Union

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.liquid import Liquid
from pylabrobot.resources.rotation import Rotation

if TYPE_CHECKING:
  from pylabrobot.resources import (
    Container,
    Resource,
    TipRack,
    Trash,
    Well,
  )
  from pylabrobot.resources.tip import Tip
  from pylabrobot.resources.tip_rack import TipSpot


@dataclass(frozen=True)
class Pickup:
  resource: TipSpot
  offset: Coordinate
  tip: Tip  # TODO: perhaps we can remove this, because the tip spot has the tip?


@dataclass(frozen=True)
class Drop:
  resource: Resource
  offset: Coordinate
  tip: Tip


@dataclass(frozen=True)
class PickupTipRack:
  resource: TipRack
  offset: Coordinate


@dataclass(frozen=True)
class DropTipRack:
  resource: Union[TipRack, Trash]
  offset: Coordinate


@dataclass(frozen=True)
class SingleChannelAspiration:
  resource: Container
  offset: Coordinate
  tip: Tip
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  liquids: List[Tuple[Optional[Liquid], float]]


@dataclass(frozen=True)
class SingleChannelDispense:
  resource: Container
  offset: Coordinate
  tip: Tip
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  liquids: List[Tuple[Optional[Liquid], float]]


@dataclass(frozen=True)
class MultiHeadAspirationPlate:
  wells: List[Well]
  offset: Coordinate
  tips: List[Tip]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  liquids: List[List[Tuple[Optional[Liquid], float]]]


@dataclass(frozen=True)
class MultiHeadDispensePlate:
  wells: List[Well]
  offset: Coordinate
  tips: List[Tip]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  liquids: List[List[Tuple[Optional[Liquid], float]]]


@dataclass(frozen=True)
class MultiHeadAspirationContainer:
  container: Container
  offset: Coordinate
  tips: List[Tip]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  liquids: List[List[Tuple[Optional[Liquid], float]]]


@dataclass(frozen=True)
class MultiHeadDispenseContainer:
  container: Container
  offset: Coordinate
  tips: List[Tip]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  liquids: List[List[Tuple[Optional[Liquid], float]]]


class GripDirection(enum.Enum):
  FRONT = enum.auto()
  BACK = enum.auto()
  LEFT = enum.auto()
  RIGHT = enum.auto()


@dataclass(frozen=True)
class ResourcePickup:
  resource: Resource
  offset: Coordinate
  pickup_distance_from_top: float
  direction: GripDirection


@dataclass(frozen=True)
class ResourceMove:
  """Moving a resource that was already picked up."""

  resource: Resource
  location: Coordinate
  gripped_direction: GripDirection


@dataclass(frozen=True)
class ResourceDrop:
  resource: Resource
  # Destination is the location of the lfb of `resource`
  destination: Coordinate
  destination_absolute_rotation: Rotation
  offset: Coordinate
  pickup_distance_from_top: float
  pickup_direction: GripDirection
  drop_direction: GripDirection
  rotation: float


PipettingOp = Union[Pickup, Drop, SingleChannelAspiration, SingleChannelDispense]
