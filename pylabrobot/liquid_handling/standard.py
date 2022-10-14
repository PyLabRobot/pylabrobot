""" Data structures for the standard form of liquid handling. """

from __future__ import annotations

from abc import ABC
from typing import Optional

from pylabrobot.liquid_handling.resources import Resource


class LiquidHandlingOp(ABC):
  """ Abstract base class for liquid handling operations.

  Attributes:
    resource: The resource that will be used in the operation.
    volume: The volume of the liquid that is being handled.
    flow_rate: The flow rate with which to perform this operation.
    offset_z: The offset in the z direction.
  """

  def __init__(
    self,
    resource: Resource,
    volume: float,
    flow_rate: Optional[float] = None,
    offset_z: float = 0
  ):
    """ Initialize the operation.

    Args:
      resource: The resource that will be used in the operation.
      volume: The volume of the liquid that is being handled. In ul.
      flow_rate: The flow rate. None is default for the Machine. In ul/s.
      offset_z: The offset in the z direction. In mm.
    """

    self.resource = resource
    self.volume = volume
    self.flow_rate = flow_rate
    self.offset_z = offset_z

  def __eq__(self, other: LiquidHandlingOp) -> bool:
    return (
      isinstance(other, LiquidHandlingOp) and
      self.resource == other.resource and
      self.volume == other.volume and
      self.flow_rate == other.flow_rate and
      self.offset_z == other.offset_z
    )

  def __hash__(self) -> int:
    return hash((self.resource, self.volume, self.flow_rate, self.offset_z))

  def __repr__(self) -> str:
    return (
      f"{self.__class__.__name__}(resource={repr(self.resource)}, volume={repr(self.volume)}, "
      f"flow_rate={self.flow_rate}, offset_z={self.offset_z})"
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
      "resource": self.resource.serialize(),
      "volume": self.volume,
      "flow_rate": self.flow_rate,
      "offset_z": self.offset_z,
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
