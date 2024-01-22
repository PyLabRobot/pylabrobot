""" Data structures for the standard form of liquid handling. """

from __future__ import annotations

from dataclasses import dataclass, field
import enum
from typing import List, Optional, Union, Tuple, TYPE_CHECKING

from pylabrobot.resources.liquid import Liquid
from pylabrobot.resources.coordinate import Coordinate
if TYPE_CHECKING:
  from pylabrobot.resources import Container, Plate, Resource, TipRack
  from pylabrobot.resources.tip import Tip
  from pylabrobot.resources.tip_rack import TipSpot


@dataclass
class Pickup:
  """ A pickup operation. """
  resource: TipSpot
  offset: Optional[Coordinate]
  tip: Tip # TODO: perhaps we can remove this, because the tip spot has the tip?


@dataclass
class Drop:
  """ A drop operation. """
  resource: Resource
  offset: Optional[Coordinate]
  tip: Tip


@dataclass
class PickupTipRack:
  """ A pickup operation for an entire tip rack. """

  resource: TipRack
  offset: Optional[Coordinate]


@dataclass
class DropTipRack:
  """ A drop operation for an entire tip rack. """

  resource: TipRack
  offset: Optional[Coordinate]


@dataclass
class Aspiration:
  """ Aspiration contains information about an aspiration. """

  resource: Container
  offset: Optional[Coordinate]
  tip: Tip
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: float
  liquids: List[Tuple[Optional[Liquid], float]]


@dataclass
class Dispense:
  """ Dispense contains information about an dispense. """

  resource: Container
  offset: Optional[Coordinate]
  tip: Tip
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: float
  liquids: List[Tuple[Optional[Liquid], float]]


@dataclass
class AspirationPlate:
  """ Contains information about an aspiration from a plate (in a single movement). """

  resource: Plate
  offset: Optional[Coordinate]
  tips: List[Tip]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: float
  liquids: List[List[Tuple[Optional[Liquid], float]]]


@dataclass
class DispensePlate:
  """ Contains information about an aspiration from a plate (in a single movement). """

  resource: Plate
  offset: Optional[Coordinate]
  tips: List[Tip]
  volume: float
  flow_rate: Optional[float]
  liquid_height: Optional[float]
  blow_out_air_volume: float
  liquids: List[List[Tuple[Optional[Liquid], float]]]


class GripDirection(enum.Enum):
  """ A direction from which to grab the resource. """
  FRONT = enum.auto()
  BACK = enum.auto()
  LEFT = enum.auto()
  RIGHT = enum.auto()


@dataclass
class Move:
  """ A move operation.

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



PipettingOp = Union[
  Pickup, Drop, Aspiration, Dispense, AspirationPlate, DispensePlate, PickupTipRack, DropTipRack]
