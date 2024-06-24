import enum
from typing import Callable, Optional, Union

from .container import Container

class TroughBottomType(enum.Enum):
  """ Enum for the type of bottom of a trough. """

  FLAT = "flat"
  U = "U"
  V = "V"
  UNKNOWN = "unknown"

class Trough(Container):
  """ A trough is a container, particularly useful for multichannel liquid handling operations. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    max_volume: float,
    material_z_thickness: float = 0,
    through_base_to_container_base: float = 0,
    category: Optional[str] = "trough",
    model: Optional[str] = None,
    bottom_type: Union[TroughBottomType, str] = TroughBottomType.UNKNOWN,
    compute_volume_from_height: Optional[Callable[[float], float]] = None,
    compute_height_from_volume: Optional[Callable[[float], float]] = None,
  ):

    if isinstance(bottom_type, str):
      bottom_type = TroughBottomType(bottom_type)

    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      max_volume=max_volume,
      category=category,
      model=model
    )
    self.material_z_thickness = material_z_thickness
    self.through_base_to_container_base = through_base_to_container_base
    self.bottom_type = bottom_type
    self._compute_volume_from_height = compute_volume_from_height
    self._compute_height_from_volume = compute_height_from_volume

  def compute_volume_from_height(self, height: float) -> float:
    """ Compute the volume of liquid in a trough from the height of the liquid relative to the
    bottom of the trough. """

    if self._compute_volume_from_height is None:
      raise NotImplementedError("compute_volume_from_height not implemented.")

    return self._compute_volume_from_height(height)

  def compute_height_from_volume(self, liquid_volume: float) -> float:
    """ Compute the height of liquid in a trough relative to the trough's bottom
    from the volume of the liquid.
    """

    if self._compute_height_from_volume is None:
      raise NotImplementedError("compute_height_from_volume not implemented.")

    return self._compute_height_from_volume(liquid_volume)

