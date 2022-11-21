""" Data structures for the standard form of liquid handling. """

from __future__ import annotations

from abc import ABC, ABCMeta
import enum
from typing import Optional

from pylabrobot.liquid_handling.resources import Coordinate, Resource, Tip


class PipettingOp(ABC):
  """ Some atomic pipetting operation. """

  def __init__(self, resource: Resource, offset: Coordinate = Coordinate.zero()):
    self.resource = resource
    self.offset = offset

  def get_absolute_location(self) -> Coordinate:
    """ Returns the absolute location of the resource. """
    return self.resource.get_absolute_location() + self.offset

  def __eq__(self, other: object) -> bool:
    return (
      isinstance(other, PipettingOp) and
      self.resource == other.resource and
      self.offset == other.offset
    )

  def __hash__(self) -> int:
    return hash((self.resource, self.offset))

  def __repr__(self) -> str:
    return f"{self.__class__.__name__}(tip={self.resource}, offset={self.offset})"

  def serialize(self) -> dict:
    return {
      "resource": self.resource.serialize(),
      "offset": self.offset.serialize()
    }


class TipOp(PipettingOp, metaclass=ABCMeta):
  """ Abstract base class for tip operations. """

  def __init__(self, resource: Tip, offset: Coordinate = Coordinate.zero()):
    super().__init__(resource, offset)
    self.resource: Tip = resource # fix type hint


class Pickup(TipOp):
  """ A pickup operation. """


class Discard(TipOp):
  """ A discard operation. """


class LiquidHandlingOp(PipettingOp, metaclass=ABCMeta):
  """ Abstract base class for liquid handling operations.

  Attributes:
    resource: The resource that will be used in the operation.
    volume: The volume of the liquid that is being handled.
    flow_rate: The flow rate with which to perform this operation.
    offset: The offset in the z direction.
  """

  def __init__(
    self,
    resource: Resource,
    volume: float,
    flow_rate: Optional[float] = None,
    offset: Coordinate = Coordinate.zero(),
  ):
    """ Initialize the operation.

    Args:
      resource: The resource that will be used in the operation.
      volume: The volume of the liquid that is being handled. In ul.
      flow_rate: The flow rate. None is default for the Machine. In ul/s.
      offset: The offset in the z direction. In mm.
    """

    super().__init__(resource, offset)

    self.volume = volume
    self.flow_rate = flow_rate

  def __eq__(self, other: object) -> bool:
    return super().__eq__(other) and (
      isinstance(other, LiquidHandlingOp) and
      self.resource == other.resource and
      self.volume == other.volume and
      self.flow_rate == other.flow_rate and
      self.offset == other.offset
    )

  def __hash__(self) -> int:
    return hash((self.resource, self.volume, self.flow_rate, self.offset))

  def __repr__(self) -> str:
    return (
      f"{self.__class__.__name__}(resource={repr(self.resource)}, volume={repr(self.volume)}, "
      f"flow_rate={self.flow_rate}, offset={self.offset})"
    )

  def serialize(self) -> dict:
    """ Serialize the operation.

    Returns:
      The serialized operation.
    """

    return {
      **super().serialize(),
      "volume": self.volume,
      "flow_rate": self.flow_rate,
    }


class Aspiration(LiquidHandlingOp):
  """ Aspiration is a class that contains information about an aspiration.

  This class is be used by
  :meth:`pyhamilton.liquid_handling.liquid_handler.LiquidHandler.aspirate` to store information
  about the aspiration for each individual channel.
  """

  pass


class Dispense(LiquidHandlingOp):
  """ Dispense is a class that contains information about an dispense.

  This class is be used by
  :meth:`pyhamilton.liquid_handling.liquid_handler.LiquidHandler.aspirate` to store information
  about the dispense for each individual channel.
  """

  pass


class Move():
  """ A move operation. """

  class Direction(enum.Enum):
    """ A direction from which to grab the resource. """
    FRONT = enum.auto()
    BACK = enum.auto()
    LEFT = enum.auto()
    RIGHT = enum.auto()

  def __init__(
    self,
    resource: Resource,
    to: Coordinate,
    resource_offset: Coordinate = Coordinate.zero(),
    to_offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: float = 0,
    get_direction: Move.Direction = Direction.FRONT,
    put_direction: Move.Direction = Direction.FRONT
  ):
    """ Initialize the move operation.

    Args:
      resource: The resource to move.
      to: The destination of the move.
      resource_offset: The offset of the resource.
      to_offset: The offset of the destination.
      pickup_distance_from_top: The distance from the top of the resource to pick up from.
      get_direction: The direction from which to grab the resource.
      put_direction: The direction from which to put the resource.
    """

    self.resource = resource
    self.to = to
    self.resource_offset = resource_offset
    self.to_offset = to_offset
    self.pickup_distance_from_top = pickup_distance_from_top
    self.get_direction = get_direction
    self.put_direction = put_direction

  def __eq__(self, other: object) -> bool:
    return (
      isinstance(other, Move) and
      self.resource == other.resource and
      self.to == other.to and
      self.resource_offset == other.resource_offset and
      self.to_offset == other.to_offset and
      self.pickup_distance_from_top == other.pickup_distance_from_top and
      self.get_direction == other.get_direction and
      self.put_direction == other.put_direction
    )

  def __hash__(self) -> int:
    return hash((self.resource, self.to))

  def __repr__(self) -> str:
    return f"{self.__class__.__name__}(resource={repr(self.resource)}, to={self.to})"

  def serialize(self) -> dict:
    return {
      "resource": self.resource.serialize(),
      "to": self.to.serialize(),
      "resource_offset": self.resource_offset.serialize(),
      "to_offset": self.to_offset.serialize(),
      "pickup_distance_from_top": self.pickup_distance_from_top,
      "get_direction": self.get_direction.name,
      "put_direction": self.put_direction.name,
    }

  def get_absolute_from_location(self) -> Coordinate:
    """ Returns the absolute location of the resource. """

    return self.resource.get_absolute_location() + self.resource_offset

  def get_absolute_to_location(self) -> Coordinate:
    """ Returns the absolute location of the resource. """

    return self.to + self.to_offset
