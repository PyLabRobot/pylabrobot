""" Data structures for the standard form of liquid handling. """

from __future__ import annotations

from dataclasses import dataclass, field
import enum
from typing import List, Optional, Union, Tuple, TYPE_CHECKING

from pylabrobot.resources.liquid import Liquid
from pylabrobot.resources.coordinate import Coordinate
if TYPE_CHECKING:
  from pylabrobot.resources import Container, Resource, TipRack, Trash, Well
  from pylabrobot.resources.tip import Tip
  from pylabrobot.resources.tip_rack import TipSpot


@dataclass(frozen=True)
class Pickup:
  resource: TipSpot
  offset: Coordinate
  tip: Tip # TODO: perhaps we can remove this, because the tip spot has the tip?


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
class Aspiration:
  resource: Container
  offset: Coordinate
  tip: Tip
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  liquids: List[Tuple[Optional[Liquid], float]]


@dataclass(frozen=True)
class Dispense:
  resource: Container
  offset: Coordinate
  tip: Tip
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  liquids: List[Tuple[Optional[Liquid], float]]


@dataclass(frozen=True)
class AspirationPlate:
  wells: List[Well]
  offset: Coordinate
  tips: List[Tip]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  liquids: List[List[Tuple[Optional[Liquid], float]]]


@dataclass(frozen=True)
class DispensePlate:
  wells: List[Well]
  offset: Coordinate
  tips: List[Tip]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  liquids: List[List[Tuple[Optional[Liquid], float]]]

@dataclass(frozen=True)
class AspirationContainer:
  container: Container
  offset: Coordinate
  tips: List[Tip]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: Optional[float]
  liquids: List[List[Tuple[Optional[Liquid], float]]]


@dataclass(frozen=True)
class DispenseContainer:
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
class Move:
  """
  Attributes:
    resource: The resource to move.
    destination: The destination of the move.
    resource_offset: The offset of the resource.
    destination_offset: The offset of the destination.
    pickup_distance_from_top: The distance from the top of the resource to pick up from.
    get_direction: The direction from which to grab the resource.
    put_direction: The direction from which to put the resource.
  """

  resource: Resource
  destination: Coordinate
  intermediate_locations: List[Coordinate] = field(default_factory=list)
  resource_offset: Coordinate = field(default_factory=Coordinate.zero)
  destination_offset: Coordinate = field(default_factory=Coordinate.zero)
  pickup_distance_from_top: float = 0
  get_direction: GripDirection = GripDirection.FRONT
  put_direction: GripDirection = GripDirection.FRONT

  @property
  def rotation(self) -> int:
    if self.get_direction == self.put_direction:
      return 0
    if (self.get_direction, self.put_direction) in (
        (GripDirection.FRONT, GripDirection.RIGHT),
        (GripDirection.RIGHT, GripDirection.BACK),
        (GripDirection.BACK, GripDirection.LEFT),
        (GripDirection.LEFT, GripDirection.FRONT),
    ):
      return 90
    if (self.get_direction, self.put_direction) in (
        (GripDirection.FRONT, GripDirection.BACK),
        (GripDirection.BACK, GripDirection.FRONT),
        (GripDirection.LEFT, GripDirection.RIGHT),
        (GripDirection.RIGHT, GripDirection.LEFT),
    ):
      return 180
    if (self.get_direction, self.put_direction) in (
        (GripDirection.RIGHT, GripDirection.FRONT),
        (GripDirection.BACK, GripDirection.RIGHT),
        (GripDirection.LEFT, GripDirection.BACK),
        (GripDirection.FRONT, GripDirection.LEFT),
    ):
      return 270
    raise ValueError(f"Invalid grip directions: {self.get_direction}, {self.put_direction}")

PipettingOp = Union[Pickup, Drop, Aspiration, Dispense]
