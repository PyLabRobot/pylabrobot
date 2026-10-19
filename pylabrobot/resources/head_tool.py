from typing import Callable, Optional

from pylabrobot.serializer import serialize

from .coordinate import Coordinate
from .resource import Resource


def move_tool(tool: Resource, assign: Callable[[], None]) -> None:
  """Move a tool to where `assign` puts it, off whatever held it, or leave it where it was.

  If the assignment fails after releasing the tool, restore its original parent and location.

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


def release_tool(
  root: Resource,
  identifier: Optional[str],
  *,
  name: Optional[str] = None,
  keep: Optional[Resource] = None,
) -> None:
  """Release a tool with the saved identity before restoring it under another holder.

  Args:
    root: the tree.
    identifier: the saved tool identifier.
    name: a fallback for legacy state without identifiers; must be unambiguous.
    keep: a tool to leave where it is, if it is the one found: the holder's own.
  """
  if identifier is not None:
    if not root.has_resource_by_identifier(identifier):
      return
    held = root.get_resource_by_identifier(identifier)
  elif name is not None and root.has_resource(name):
    held = root.get_resource(name)
  else:
    return
  if held is not keep and isinstance(held, HeadTool) and held.parent is not None:
    held.parent.unassign_child_resource(held)


class HeadTool(Resource):
  """A tool that a liquid handling channel picks up and carries.

  Attributes:
    fitting_depth: the overlap between the tool and the channel, in mm.
    is_conductive: whether the tool conducts, which capacitive sensing through it needs. None
      when not known.
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
    is_conductive: Optional[bool] = None,
    *,
    identifier: Optional[str] = None,
    metadata: Optional[dict] = None,
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
      identifier=identifier,
      metadata=metadata,
    )
    self.fitting_depth = fitting_depth
    self.pick_up_location = pick_up_location
    self.is_conductive = is_conductive

  def serialize(self) -> dict:
    """Serialize the tool's resource fields, fitting depth, pickup location and conductivity."""
    return {
      **super().serialize(),
      "fitting_depth": self.fitting_depth,
      "pick_up_location": serialize(self.pick_up_location),
      "is_conductive": self.is_conductive,
    }
