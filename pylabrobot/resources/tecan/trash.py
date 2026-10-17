from typing import Callable, Dict, List, Optional, Tuple

from pylabrobot.resources.coordinate import Coordinate
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
    material_z_thickness: float = 0,
    max_volume: float = float("inf"),
    compute_volume_from_height: Optional[Callable[[float], float]] = None,
    compute_height_from_volume: Optional[Callable[[float], float]] = None,
    height_volume_data: Optional[Dict[float, float]] = None,
    no_go_zones: Optional[List[Tuple[Coordinate, Coordinate]]] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
      material_z_thickness=material_z_thickness,
      max_volume=max_volume,
      compute_volume_from_height=compute_volume_from_height,
      compute_height_from_volume=compute_height_from_volume,
      height_volume_data=height_volume_data,
      no_go_zones=no_go_zones,
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
