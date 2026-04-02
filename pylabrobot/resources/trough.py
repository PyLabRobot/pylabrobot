import enum
from typing import Callable, Dict, Optional, Union

from .container import Container


class TroughBottomType(enum.Enum):
  """Enum for the type of bottom of a trough."""

  FLAT = "flat"
  U = "U"
  V = "V"
  UNKNOWN = "unknown"


class Trough(Container):
  """A trough is a container, particularly useful for multichannel liquid handling operations."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    max_volume: float,
    material_z_thickness: Optional[float] = None,
    through_base_to_container_base: float = 0,
    category: Optional[str] = "trough",
    model: Optional[str] = None,
    bottom_type: Union[TroughBottomType, str] = TroughBottomType.UNKNOWN,
    compute_volume_from_height: Optional[Callable[[float], float]] = None,
    compute_height_from_volume: Optional[Callable[[float], float]] = None,
    height_volume_data: Optional[Dict[float, float]] = None,
    no_go_zones=None,
  ):
    if isinstance(bottom_type, str):
      bottom_type = TroughBottomType(bottom_type)

    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      material_z_thickness=material_z_thickness,
      max_volume=max_volume,
      category=category,
      model=model,
      compute_volume_from_height=compute_volume_from_height,
      compute_height_from_volume=compute_height_from_volume,
      height_volume_data=height_volume_data,
      no_go_zones=no_go_zones,
    )
    self.through_base_to_container_base = through_base_to_container_base
    self.bottom_type = bottom_type

  def serialize(self) -> dict:
    data = super().serialize()
    data["bottom_type"] = self.bottom_type.value
    if self.through_base_to_container_base != 0:
      data["through_base_to_container_base"] = self.through_base_to_container_base
    return data
