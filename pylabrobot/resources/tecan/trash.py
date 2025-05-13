from typing import Optional

from pylabrobot.resources.tecan.tecan_resource import TecanResource
from pylabrobot.resources.trash import Trash


class TecanTrash(Trash, TecanResource):
  """Base class for Tecan diti tip trash containers."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    category: str = "tecan_trash",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )


def Trash_Container(name: str) -> TecanTrash:
  """Tecan trash container."""
  return TecanTrash(
    name=name,
    size_x=25.0,
    size_y=390.0,
    size_z=140.0,
    model="Trash_Container",
  )


def Trash_Waste(name: str) -> TecanTrash:
  """Tecan waste container."""
  return TecanTrash(
    name=name,
    size_x=12.0,
    size_y=100.0,
    size_z=140.0,
    model="Trash_Waste",
  )
