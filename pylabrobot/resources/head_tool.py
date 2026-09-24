from typing import Any, Callable, Optional, Tuple, cast

from pylabrobot.serializer import serialize

from .coordinate import Coordinate
from .resource import Resource


def _without_names(value: object) -> Any:
  """`value` with every name dropped, and hashable: dicts become sorted tuples, lists tuples."""
  if isinstance(value, dict):
    return tuple(sorted((k, _without_names(v)) for k, v in value.items() if k != "name"))
  if isinstance(value, list):
    return tuple(_without_names(v) for v in value)
  return value


def move_tool(tool: Resource, assign: Callable[[], None]) -> None:
  """Move a tool to where `assign` puts it, off whatever held it, or leave it where it was.

  A name is checked against the whole tree before an assignment detaches what it moves, so a tool
  still held elsewhere in the same tree has to be let go of first. If the assignment then fails,
  the tool goes back where it was rather than belonging to nothing.

  Args:
    tool: the tool to move.
    assign: assigns it to its new parent.
  """
  parent, location = tool.parent, tool.location
  if parent is not None:
    parent.unassign_child_resource(tool)
  try:
    assign()
  except BaseException:
    if parent is not None and tool.parent is None:
      parent.assign_child_resource(tool, location=location)
    raise


def release_named_tool(root: Resource, name: str, keep: Optional[Resource] = None) -> None:
  """Let go of a tool of this name held anywhere in a tree, so one of that name can be put elsewhere.

  Loading state brings its own copy of a tool that may still be held somewhere else in the tree, on
  a channel say, and a name is unique in a tree.

  Args:
    root: the tree.
    name: the tool's name.
    keep: a tool to leave where it is, if it is the one found: the holder's own.
  """
  if not root.has_resource(name):
    return
  held = root.get_resource(name)
  if held is not keep and isinstance(held, HeadTool) and held.parent is not None:
    held.parent.unassign_child_resource(held)


class HeadTool(Resource):
  """A tool that a liquid handling channel picks up and carries.

  Attributes:
    fitting_depth: the overlap between the tool and the channel, in mm.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    fitting_depth: float,
    category: Optional[str] = None,
    model: Optional[str] = None,
    pick_up_location: Optional[Coordinate] = None,
  ):
    """Initialize a tool with an optional pickup location relative to its origin."""
    if not isinstance(name, str):
      raise TypeError("HeadTool name must be a string.")
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )
    self.fitting_depth = fitting_depth
    self.pick_up_location = pick_up_location

  def __eq__(self, other: object) -> bool:
    """Compare resource fields and the tool's fit and pickup location."""
    return (
      isinstance(other, HeadTool)
      and super().__eq__(other)
      and self.fitting_depth == other.fitting_depth
      and self.pick_up_location == other.pick_up_location
    )

  def serialize(self) -> dict:
    """Serialize the tool's resource fields, fitting depth, and pickup location."""
    return {
      **super().serialize(),
      "fitting_depth": self.fitting_depth,
      "pick_up_location": serialize(self.pick_up_location),
    }

  def kind(self) -> Tuple[object, ...]:
    """The tool definition without its name or holder-dependent location."""
    data = self.serialize()
    data.pop("location", None)
    data.pop("parent_name", None)
    return cast(Tuple[object, ...], _without_names(data))
