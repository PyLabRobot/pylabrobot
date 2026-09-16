from __future__ import annotations

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


class HeadTool(Resource):
  """Something a channel picks up and carries: a tip, a needle, a gripper tool.

  The channel enters the tool's opening at `pick_up_location` and reaches `fitting_depth` into it.
  Tools that are interchangeable for a backend have equal :meth:`kind`, whatever their names.

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
      collar_height: the height of the tool's collar, in mm.
      category: the category of the tool.
      model: the model of the tool.
      pick_up_location: the centre of the top of the opening the channel enters, relative to the
        tool's left front bottom corner. Defaults to the centre of the tool's top.
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
  def has_collar_height(self) -> bool:
    """Whether this tool states the height of its collar."""
    return self._collar_height is not None

  def kind(self) -> Tuple[object, ...]:
    """The tool's serialized form without names: equal for tools of the same kind."""
    return cast(Tuple[object, ...], _without_names(self.serialize()))

  def serialize(self) -> dict:
    """Serialize the tool, without its location, which its holder determines."""
    data = {
      **super().serialize(),
      "fitting_depth": self.fitting_depth,
      "collar_height": self._collar_height,
      "pick_up_location": serialize(self.pick_up_location),
    }
    for held_by_the_holder in ("location", "parent_name"):
      data.pop(held_by_the_holder, None)
    return data
