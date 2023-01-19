""" Data structures for the standard form of liquid handling. """

from __future__ import annotations

from abc import ABC, ABCMeta
import enum
from typing import List, Optional, TYPE_CHECKING

from pylabrobot.default import Defaultable, Default, is_not_default
from pylabrobot.liquid_handling.liquid_classes.abstract import LiquidClass
from pylabrobot.resources.coordinate import Coordinate
if TYPE_CHECKING:
  from pylabrobot.resources import Container, Plate, Resource, TipRack
  from pylabrobot.resources.tip import Tip
  from pylabrobot.resources.tip_rack import TipSpot


class PipettingOp(ABC):
  """ Some atomic pipetting operation. """

  def __init__(self, resource: Resource, offset: Defaultable[Coordinate] = Default):
    self.resource = resource
    self.offset = offset

  def get_absolute_location(self) -> Coordinate:
    """ Returns the absolute location of the resource. """
    if is_not_default(self.offset):
      return self.resource.get_absolute_location() + self.offset
    return self.resource.get_absolute_location()

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
      "resource_name": self.resource.name,
      "offset": self.offset.serialize() if is_not_default(self.offset) else "default"
    }


class TipOp(PipettingOp, metaclass=ABCMeta):
  """ Abstract base class for tip operations. """

  def __init__(
    self,
    resource: Resource,
    tip: Tip,
    offset: Defaultable[Coordinate] = Default
  ):
    super().__init__(resource, offset)
    self.tip = tip

  def __eq__(self, other: object) -> bool:
    return (
      super().__eq__(other) and
      isinstance(other, TipOp) and
      self.tip == other.tip
    )

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "tip": self.tip.serialize()
    }


class Pickup(TipOp):
  """ A pickup operation. """

  def __init__(
    self,
    resource: TipSpot,
    tip: Tip,
    offset: Defaultable[Coordinate] = Default
  ):
    super().__init__(resource=resource, offset=offset, tip=tip)
    self.resource: TipSpot = resource # fix type

  @classmethod
  def deserialize(cls, data: dict, tip: Tip, resource: TipSpot) -> Pickup:
    assert resource.name == data["resource_name"] # TODO: why does this exist?
    # to prevent circular import (tip->vol tracker->standard->tip) we need to get tip from caller
    return Pickup(
      resource=resource,
      offset=Default if data["offset"] == "default" else Coordinate.deserialize(data["offset"]),
      tip=tip
    )


class Drop(TipOp):
  """ A drop operation. """

  @classmethod
  def deserialize(cls, data: dict, tip: Tip, resource: Resource) -> Drop:
    assert resource.name == data["resource_name"] # TODO: why does this exist?
    # to prevent circular import (tip->vol tracker->standard->tip) we need to get tip from caller
    return Drop(
      resource=resource,
      offset=Default if data["offset"] == "default" else Coordinate.deserialize(data["offset"]),
      tip=tip
    )


class TipRackOp(PipettingOp, metaclass=ABCMeta):
  """ Abstract base class for tip rack operations. """

  def __init__(
    self,
    resource: TipRack,
    offset: Defaultable[Coordinate] = Default
  ):
    super().__init__(resource=resource, offset=offset)
    self.resource: TipRack = resource # fix type


class PickupTipRack(TipRackOp):
  """ A pickup operation for an entire tip rack. """

  def __init__(
    self,
    resource: TipRack,
    offset: Defaultable[Coordinate] = Default
  ):
    super().__init__(resource=resource, offset=offset)

  @classmethod
  def deserialize(cls, data: dict, resource: TipRack) -> PickupTipRack:
    assert resource.name == data["resource_name"]
    return PickupTipRack(
      resource=resource,
      offset=Default if data["offset"] == "default" else Coordinate.deserialize(data["offset"])
    )


class DropTipRack(TipRackOp):
  """ A drop operation for an entire tip rack. """

  def __init__(
    self,
    resource: TipRack,
    offset: Defaultable[Coordinate] = Default
  ):
    super().__init__(resource=resource, offset=offset)

  @classmethod
  def deserialize(cls, data: dict, resource: TipRack) -> DropTipRack:
    assert resource.name == data["resource_name"]
    return DropTipRack(
      resource=resource,
      offset=Default if data["offset"] == "default" else Coordinate.deserialize(data["offset"])
    )


class LiquidHandlingOp(PipettingOp, metaclass=ABCMeta):
  """ Abstract base class for liquid handling operations. """

  def __init__(
    self,
    resource: Resource,
    volume: float,
    flow_rate: Defaultable[float] = Default,
    offset: Defaultable[Coordinate] = Default,
    liquid_height: Defaultable[float] = Default,
    blow_out_air_volume: float = 0,
    liquid_class: LiquidClass = LiquidClass.WATER
  ):
    """ Initialize the operation.

    Args:
      resource: The resource that will be used in the operation.
      volume: The volume of the liquid that is being handled. In ul.
      flow_rate: The flow rate. None is default for the Machine. In ul/s.
      offset: The offset in the z direction. In mm.
      liquid_height: The height of the liquid in the well. In mm.
    """

    super().__init__(resource, offset)

    self.volume = volume
    self.flow_rate = flow_rate
    self.liquid_height = liquid_height
    self.blow_out_air_volume = blow_out_air_volume
    self.liquid_class = liquid_class

  def __eq__(self, other: object) -> bool:
    return super().__eq__(other) and (
      isinstance(other, LiquidHandlingOp) and # TODO: does this mean that asp == disp?
      self.resource == other.resource and
      self.volume == other.volume and
      self.flow_rate == other.flow_rate and
      self.offset == other.offset and
      self.liquid_height == other.liquid_height and
      self.blow_out_air_volume == other.blow_out_air_volume and
      self.liquid_class == other.liquid_class
    )

  def __hash__(self) -> int:
    return hash((self.resource, self.volume, self.flow_rate, self.offset, self.liquid_height))

  def __repr__(self) -> str:
    return (
      f"{self.__class__.__name__}(resource={repr(self.resource)}, volume={repr(self.volume)}, "
      f"flow_rate={self.flow_rate}, offset={self.offset}, liquid_height={self.liquid_height}, "
      f"blow_out_air_volume={self.blow_out_air_volume}, liquid_class={self.liquid_class})"
    )

  def serialize(self) -> dict:
    """ Serialize the operation.

    Returns:
      The serialized operation.
    """

    return {
      **super().serialize(),
      "volume": self.volume,
      "flow_rate": self.flow_rate if is_not_default(self.flow_rate) else "default",
      "liquid_height": self.liquid_height if is_not_default(self.liquid_height) else "default",
      "blow_out_air_volume": self.blow_out_air_volume,
      "liquid_class": self.liquid_class.name
    }


class Aspiration(LiquidHandlingOp):
  """ Aspiration contains information about an aspiration.

  This class is be used by
  :meth:`pyhamilton.liquid_handling.liquid_handler.LiquidHandler.aspirate` to store information
  about the aspiration for each individual channel.
  """

  def __init__(
    self,
    resource: Container,
    volume: float,
    tip: Tip,
    flow_rate: Defaultable[float] = Default,
    offset: Defaultable[Coordinate] = Default,
    liquid_height: Defaultable[float] = Default,
    blow_out_air_volume: float = 0,
    liquid_class: LiquidClass = LiquidClass.WATER
  ):
    """ Initialize an aspiration operation.

    Args:
      resource: The resource that will be used in the operation.
      volume: The volume of the liquid that is being handled. In ul.
      tip: The tip that is being used in the operation.
      flow_rate: The flow rate. None is default for the Machine. In ul/s.
      offset: The offset in the z direction. In mm.
      liquid_height: The height of the liquid in the well. In mm.
    """

    super().__init__(
      resource=resource,
      volume=volume,
      flow_rate=flow_rate,
      offset=offset,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
      liquid_class=liquid_class
    )

    self.resource: Container = resource # fix type
    self.tip = tip

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "tip": self.tip.serialize(),
    }

  @classmethod
  def deserialize(cls, data: dict, resource: Container, tip: Tip) -> Aspiration:
    assert resource.name == data["resource_name"]
    return Aspiration(
      resource=resource,
      volume=data["volume"],
      flow_rate=Default if data["flow_rate"] == "default" else data["flow_rate"],
      offset=Default if data["offset"] == "default" else Coordinate.deserialize(data["offset"]),
      liquid_height=Default if data["liquid_height"] == "default" else data["liquid_height"],
      blow_out_air_volume=data["blow_out_air_volume"],
      tip=tip,
      liquid_class=LiquidClass[data["liquid_class"]]
    )


class Dispense(LiquidHandlingOp):
  """ Dispense contains information about an dispense.

  This class is be used by
  :meth:`pyhamilton.liquid_handling.liquid_handler.LiquidHandler.aspirate` to store information
  about the dispense for each individual channel.
  """

  def __init__(
    self,
    resource: Container,
    volume: float,
    tip: Tip,
    flow_rate: Defaultable[float] = Default,
    offset: Defaultable[Coordinate] = Default,
    liquid_height: Defaultable[float] = Default,
    blow_out_air_volume: float = 0,
    liquid_class: LiquidClass = LiquidClass.WATER
  ):
    """ Initialize a dispense operation.

    Args:
      resource: The resource that will be used in the operation.
      volume: The volume of the liquid that is being handled. In ul.
      tip: The tip that is being used in the operation.
      flow_rate: The flow rate. None is default for the Machine. In ul/s.
      offset: The offset in the z direction. In mm.
      liquid_height: The height of the liquid in the well. In mm.
    """

    super().__init__(
      resource=resource,
      volume=volume,
      flow_rate=flow_rate,
      offset=offset,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
      liquid_class=liquid_class
    )

    self.resource: Container = resource # fix type
    self.tip = tip

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "tip": self.tip.serialize(),
    }

  @classmethod
  def deserialize(cls, data: dict, resource: Container, tip: Tip) -> Dispense:
    assert resource.name == data["resource_name"]
    return Dispense(
      resource=resource,
      volume=data["volume"],
      flow_rate=Default if data["flow_rate"] == "default" else data["flow_rate"],
      offset=Default if data["offset"] == "default" else Coordinate.deserialize(data["offset"]),
      liquid_height=Default if data["liquid_height"] == "default" \
        else data["liquid_height"],
      blow_out_air_volume=data["blow_out_air_volume"],
      tip=tip,
      liquid_class=LiquidClass[data["liquid_class"]]
    )


class AspirationPlate(LiquidHandlingOp):
  """ AspirationPlate contains information about an aspiration from a plate (in a single movement).

  This class is be used by
  :meth:`pyhamilton.liquid_handling.liquid_handler.LiquidHandler.aspirate_plate`.
  """

  def __init__(
    self,
    resource: Plate,
    volume: float,
    tips: List[Tip],
    flow_rate: Defaultable[float] = Default,
    offset: Defaultable[Coordinate] = Default,
    liquid_height: Defaultable[float] = Default,
    blow_out_air_volume: float = 0,
    liquid_class: LiquidClass = LiquidClass.WATER
  ):
    """ Initialize an aspiration plate operation.

    Args:
      resource: The resource that will be used in the operation.
      volume: The volume of the liquid that is being handled. In ul. Per tip.
      tips: The tips that are being used in the operation.
      flow_rate: The flow rate. None is default for the Machine. In ul/s. For all tips.
      offset: The offset in the z direction. In mm. For all tips.
      liquid_height: The height of the liquid in the well. In mm. For all tips.
    """

    super().__init__(
      resource=resource,
      volume=volume,
      flow_rate=flow_rate,
      offset=offset,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
      liquid_class=liquid_class
    )

    self.resource: Plate = resource # fix type
    self.tips = tips

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "tips": [tip.serialize() for tip in self.tips]
    }

  @classmethod
  def deserialize(cls, data: dict, resource: Plate, tips: List[Tip]) -> AspirationPlate:
    assert resource.name == data["resource_name"]
    return AspirationPlate(
      resource=resource,
      volume=data["volume"],
      flow_rate=Default if data["flow_rate"] == "default" else data["flow_rate"],
      offset=Default if data["offset"] == "default" else Coordinate.deserialize(data["offset"]),
      liquid_height=Default if data["liquid_height"] == "default" else data["liquid_height"],
      blow_out_air_volume=data["blow_out_air_volume"],
      tips=tips,
      liquid_class=LiquidClass[data["liquid_class"]]
    )


class DispensePlate(LiquidHandlingOp):
  """ DispensePlate contains information about an aspiration from a plate (in a single movement).

  This class is be used by
  :meth:`pyhamilton.liquid_handling.liquid_handler.LiquidHandler.dispense_plate`.
  """

  def __init__(
    self,
    resource: Plate,
    volume: float,
    tips: List[Tip],
    flow_rate: Defaultable[float] = Default,
    offset: Defaultable[Coordinate] = Default,
    liquid_height: Defaultable[float] = Default,
    blow_out_air_volume: float = 0,
    liquid_class: LiquidClass = LiquidClass.WATER
  ):
    """ Initialize an dispense plate operation.

    Args:
      resource: The resource that will be used in the operation.
      volume: The volume of the liquid that is being handled. In ul. Per tip.
      tips: The tips that are being used in the operation.
      flow_rate: The flow rate. None is default for the Machine. In ul/s. For all tips.
      offset: The offset in the z direction. In mm. For all tips.
      liquid_height: The height of the liquid in the well. In mm. For all tips.
    """

    super().__init__(
      resource=resource,
      volume=volume,
      flow_rate=flow_rate,
      offset=offset,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
      liquid_class=liquid_class
    )

    self.resource: Plate = resource # fix type
    self.tips = tips

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "tips": [tip.serialize() for tip in self.tips]
    }

  @classmethod
  def deserialize(cls, data: dict, resource: Plate, tips: List[Tip]) -> AspirationPlate:
    assert resource.name == data["resource_name"]
    return AspirationPlate(
      resource=resource,
      volume=data["volume"],
      flow_rate=Default if data["flow_rate"] == "default" else data["flow_rate"],
      offset=Default if data["offset"] == "default" else Coordinate.deserialize(data["offset"]),
      liquid_height=Default if data["liquid_height"] == "default" else data["liquid_height"],
      blow_out_air_volume=data["blow_out_air_volume"],
      tips=tips,
      liquid_class=LiquidClass[data["liquid_class"]]
    )


class GripDirection(enum.Enum):
  """ A direction from which to grab the resource. """
  FRONT = enum.auto()
  BACK = enum.auto()
  LEFT = enum.auto()
  RIGHT = enum.auto()


class Move():
  """ A move operation. """

  def __init__(
    self,
    resource: Resource,
    to: Coordinate,
    intermediate_locations: Optional[List[Coordinate]] = None,
    resource_offset: Coordinate = Coordinate.zero(),
    to_offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: float = 0,
    get_direction: GripDirection = GripDirection.FRONT,
    put_direction: GripDirection = GripDirection.FRONT
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
    self.intermediate_locations = (intermediate_locations if intermediate_locations is not None
                                   else [])
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
      self.intermediate_locations == other.intermediate_locations and
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
      "intermediate_locations": [location.serialize() for location in self.intermediate_locations],
      "resource_offset": self.resource_offset.serialize(),
      "to_offset": self.to_offset.serialize(),
      "pickup_distance_from_top": self.pickup_distance_from_top,
      "get_direction": self.get_direction.name,
      "put_direction": self.put_direction.name,
    }

  @classmethod
  def deserialize(cls, data: dict) -> Move:
    return Move(
      resource=Resource.deserialize(data["resource"]),
      to=Coordinate.deserialize(data["to"]),
      intermediate_locations=
        [Coordinate.deserialize(location) for location in data["intermediate_locations"]],
      resource_offset=Coordinate.deserialize(data["resource_offset"]),
      to_offset=Coordinate.deserialize(data["to_offset"]),
      pickup_distance_from_top=data["pickup_distance_from_top"],
      get_direction=GripDirection[data["get_direction"]],
      put_direction=GripDirection[data["put_direction"]]
    )

  def get_absolute_from_location(self) -> Coordinate:
    """ Returns the absolute location of the resource. """

    return self.resource.get_absolute_location() + self.resource_offset

  def get_absolute_to_location(self) -> Coordinate:
    """ Returns the absolute location of the resource. """

    return self.to + self.to_offset
