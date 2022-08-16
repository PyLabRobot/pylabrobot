""" Data structures for the standard form of liquid handling. """

from __future__ import annotations

from abc import ABC

from pylabrobot.liquid_handling.liquid_classes import (
  LiquidClass,
  StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol
)
from pylabrobot.liquid_handling.resources import Resource


class LiquidHandlingOp(ABC):
  """ Abstract base class for liquid handling operations.

  Attributes:
    resource: The resource that will be used in the operation.
    volume: The volume of the liquid that is being handled.
    liquid_class: The liquid class of the liquid that is being handled.
  """

  def __init__(
    self,
    resource: Resource,
    volume: float,
    liquid_class: LiquidClass = StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol
  ):
    """ Initialize the operation.

    Args:
      resource: The resource that will be used in the operation.
      volume: The volume of the liquid that is being handled.
      liquid_class: The liquid class of the liquid that is being handled.
    """

    self.resource = resource
    self.volume = volume
    self.liquid_class = liquid_class

  def __eq__(self, other: LiquidHandlingOp) -> bool:
    return (
      isinstance(other, LiquidHandlingOp) and
      self.resource == other.resource and
      self.volume == other.volume and
      self.liquid_class == other.liquid_class
    )

  def __hash__(self) -> int:
    return hash((self.resource, self.volume, self.liquid_class))

  def __repr__(self) -> str:
    return (
      f"{self.__class__.__name__}(resource={repr(self.resource)}, volume={repr(self.volume)}, "
      f"liquid_class={repr(self.liquid_class)})"
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
      "liquid_class": self.liquid_class.serialize()
    }


class Aspiration(LiquidHandlingOp):
  """ Aspiration is a class that contains information about an aspiration.

  This class is be used by
  :meth:`pyhamilton.liquid_handling.liquid_handler.LiquidHandler.aspirate` to store information
  about the aspiration for each individual channel.
  """

  @classmethod
  def from_tuple(cls, tuple_):
    """ Create aspiration info from a tuple.

    The tuple should either be in the form (position, volume) or (position, volume, liquid_class).
    In the former case, the liquid class will be set to
    `StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol`. (TODO: link to liquid class
    in docs)

    Args:
      tuple: A tuple in the form (position, volume) or (position, volume, liquid_class)

    Returns:
      Aspiration object.

    Raises:
      ValueError if the tuple is not in the correct format.
    """

    raise NotImplementedError() # TODO: fix later

    if len(tuple_) == 2:
      position, volume = tuple_
      return cls(position, volume)
    elif len(tuple_) == 3:
      position, volume, liquid_class = tuple_
      return cls(position, volume, liquid_class)
    else:
      raise ValueError("Invalid tuple length")

  @classmethod
  def from_dict(cls, dict_):
    """ Create aspiration info from a dictionary.

    The dictionary should either be in the form {"position": position, "volume": volume} or
    {"position": position, "volume": volume, "liquid_class": liquid_class}. In the former case,
    the liquid class will be set to
    `StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol`.

    Args:
      dict: A dictionary in the form {"position": position, "volume": volume} or
        {"position": position, "volume": volume, "liquid_class": liquid_class}

    Returns:
      Aspiration object.

    Raises:
      ValueError: If the dictionary is invalid.
    """

    raise NotImplementedError() # TODO: fix later

    if "position" in dict_ and "volume" in dict_:
      position = dict_["position"]
      volume = dict_["volume"]
      return cls(
        position=position,
        volume=volume,
        liquid_class=dict_.get("liquid_class",
          StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol))

    raise ValueError("Invalid dictionary")


class Dispense(LiquidHandlingOp):
  """ Dispense is a class that contains information about an dispense.

  This class is be used by
  :meth:`pyhamilton.liquid_handling.liquid_handler.LiquidHandler.aspirate` to store information
  about the dispense for each individual channel.
  """

  @classmethod
  def from_tuple(cls, tuple_):
    """ Create dispense info from a tuple.

    The tuple should either be in the form (position, volume) or (position, volume, liquid_class).
    In the former case, the liquid class will be set to
    `StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol`. (TODO: link to liquid class
    in docs)

    Args:
      tuple: A tuple in the form (position, volume) or (position, volume, liquid_class)

    Returns:
      Dispense object.

    Raises:
      ValueError if the tuple is not in the correct format.
    """

    raise NotImplementedError() # TODO: fix later

    if len(tuple_) == 2:
      position, volume = tuple_
      return cls(position, volume)
    elif len(tuple_) == 3:
      position, volume, liquid_class = tuple_
      return cls(position, volume, liquid_class)
    else:
      raise ValueError("Invalid tuple length")

  @classmethod
  def from_dict(cls, dict):
    """ Create dispense info from a dictionary.

    The dictionary should either be in the form {"position": position, "volume": volume} or
    {"position": position, "volume": volume, "liquid_class": liquid_class}. In the former case,
    the liquid class will be set to
    `StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol`.

    Args:
      dict: A dictionary in the form {"position": position, "volume": volume} or
        {"position": position, "volume": volume, "liquid_class": liquid_class}

    Returns:
      Dispense object.

    Raises:
      ValueError: If the dictionary is invalid.
    """

    raise NotImplementedError() # TODO: fix later

    if "position" in dict and "volume" in dict:
      position = dict["position"]
      volume = dict["volume"]
      return cls(
        position=position,
        volume=volume,
        liquid_class=dict.get("liquid_class",
          StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol))

    raise ValueError("Invalid dictionary")
