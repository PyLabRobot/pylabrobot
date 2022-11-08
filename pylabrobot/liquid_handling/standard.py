""" Data structures for the standard form of liquid handling. """

from __future__ import annotations

from abc import ABC, ABCMeta
from typing import Optional

from pylabrobot.liquid_handling.resources import Coordinate, Resource


class PipettingOp(ABC):
  """ Some atomic pipetting operation. """

  def __init__(self, resource: Resource, offset: Coordinate = Coordinate.zero()):
    self.resource = resource
    self.offset = offset

  def get_absolute_location(self) -> Coordinate:
    """ Returns the absolute location of the resource. """
    return self.resource.get_absolute_location() + self.offset

  def __eq__(self, other: PipettingOp) -> bool:
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
    offset: Coordinate = Coordinate.zero()
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

  def __eq__(self, other: LiquidHandlingOp) -> bool:
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

  def get_corrected_volume(self) -> float:
    """ Get the corrected volume.

    The corrected volume is computed based on various properties of a liquid, as defined by the
    :class:`pylabrobot.liquid_handling.liquid_classes.LiquidClass` object.

    Returns:
      The corrected volume.
    """

    return self.liquid_class.compute_corrected_volume(self.volume)

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
