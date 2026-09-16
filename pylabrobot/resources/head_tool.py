from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Any, Optional, Tuple, cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.serializer import serialize


def _without_names(value: object) -> Any:
  """`value` with every name dropped, and hashable: dicts become sorted tuples, lists tuples."""
  if isinstance(value, dict):
    return tuple(sorted((k, _without_names(v)) for k, v in value.items() if k != "name"))
  if isinstance(value, list):
    return tuple(_without_names(v) for v in value)
  return value


class HeadTool(Resource, metaclass=ABCMeta):
  """Something a channel picks up and carries on its end: a tip, a needle, a gripper tool.

  Every head tool is mounted the same way. It has a collar with an orifice the channel moves into,
  the channel reaches `fitting_depth` into that collar, and the tool's working point - the end of a
  tip, the grip line of a gripper tool - lies `total_length` below the top of the collar.

  The size of a head tool is its physical envelope, with `size_z` running from its lowest point to
  the top of its collar.

  A head tool is a resource, so it is identified by its name. Two tools that are the same kind of
  tool - interchangeable for a backend, which declares one tool type for both - have equal
  :meth:`kind`, whatever their names.

  Attributes:
    fitting_depth: the overlap between the tool and the channel, in mm
  """

  def __init__(
    self,
    name: Optional[str],
    size_x: float,
    size_y: float,
    size_z: float,
    fitting_depth: float,
    collar_height: Optional[float] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
    pick_up_location: Optional[Coordinate] = None,
  ):
    """Initialize a head tool.

    Args:
      name: the tool's name. A tool created without one can be named once, before it is used.
      size_x: size of the tool's envelope in the x direction, in mm.
      size_y: size of the tool's envelope in the y direction, in mm.
      size_z: size of the tool's envelope in the z direction, in mm.
      fitting_depth: the overlap between the tool and the channel, in mm.
      collar_height: the height of the collar the channel pushes into, in mm.
      category: the category of the tool.
      model: the model of the tool.
      pick_up_location: the centre of the top of the orifice the channel moves into, relative to
        the tool's left front bottom corner. Defaults to the centre of the top of the tool's
        envelope, which is where it is for a tool whose collar is centred on it.
    """

    super().__init__(
      name=name or "",
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )
    self._is_named = name is not None
    self.fitting_depth = fitting_depth
    self._collar_height = collar_height
    self.pick_up_location = (
      pick_up_location
      if pick_up_location is not None
      else Coordinate(x=size_x / 2, y=size_y / 2, z=size_z)
    )

  @property
  def name(self) -> str:
    """Get the name of this tool."""
    return self._name

  @name.setter
  def name(self, name: str) -> None:
    """Name a tool that was created without a name.

    Raises:
      AttributeError: If the tool already has a name. Like any resource, a named tool keeps its name.
    """
    if self._is_named:
      raise AttributeError(
        f"cannot rename {self._name!r} to {name!r}: a resource's name is its identifier and is fixed "
        "once it is set."
      )
    self._name = name
    self._is_named = True

  @property
  def is_named(self) -> bool:
    """Whether this tool has a name."""
    return self._is_named

  @property
  def collar_height(self) -> float:
    """Return collar_height, raising if it is None."""
    if self._collar_height is None:
      raise ValueError(f"collar_height is not defined for this tool: {self!r}")
    return self._collar_height

  @property
  @abstractmethod
  def total_length(self) -> float:
    """Distance from the top of the tool's collar to its working point, in mm."""

  @property
  def extension(self) -> float:
    """How far the working point sits below the end of the channel carrying the tool, in mm."""
    return self.total_length - self.fitting_depth

  def kind(self) -> Tuple[object, ...]:
    """What this tool is, as opposed to which one it is.

    Everything the tool says about itself except its name, so two tools of the same kind are one
    kind whatever they are called and wherever they are: a backend that has to declare a tool to a
    machine declares one per kind. A vendor that states more about its tools says more here too,
    without having to be asked for it separately.
    """
    return cast(Tuple[object, ...], _without_names(self.serialize()))

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "fitting_depth": self.fitting_depth,
      "collar_height": self._collar_height,
      "pick_up_location": serialize(self.pick_up_location),
    }
