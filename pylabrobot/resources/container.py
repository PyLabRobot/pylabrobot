from typing import Any, Callable, Dict, Optional

from .resource import Resource
from .volume_tracker import VolumeTracker


class Container(Resource):
  """ A container is an abstract base class for a resource that can hold liquid. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    material_z_thickness: float = 0,
    max_volume: Optional[float] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
    compute_volume_from_height: Optional[Callable[[float], float]] = None,
    compute_height_from_volume: Optional[Callable[[float], float]] = None,
  ):
    """ Create a new container.

    Args:
      max_volume: Maximum volume of the container. If `None`, will be inferred from resource size.
    """

    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      model=model)
    self.material_z_thickness = material_z_thickness
    self.max_volume = max_volume or (size_x * size_y * size_z)
    self.tracker = VolumeTracker(max_volume=self.max_volume)
    self._compute_volume_from_height = compute_volume_from_height
    self._compute_height_from_volume = compute_height_from_volume

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "max_volume": self.max_volume
    }

  def serialize_state(self) -> Dict[str, Any]:
    return self.tracker.serialize()

  def load_state(self, state: Dict[str, Any]):
    self.tracker.load_state(state)

  def compute_volume_from_height(self, height: float) -> float:
    """ Compute the volume of liquid in a container from the height of the liquid relative to the
    bottom of the container. """

    if self._compute_volume_from_height is None:
      raise NotImplementedError(f"compute_volume_from_height not implemented for {self.name}.")

    return self._compute_volume_from_height(height)

  def compute_height_from_volume(self, liquid_volume: float) -> float:
    """ Compute the height of liquid in a container relative to the container's bottom
    from the volume of the liquid.  """

    if self._compute_height_from_volume is None:
      raise NotImplementedError(f"compute_height_from_volume not implemented for {self.name}.")

    return self._compute_height_from_volume(liquid_volume)
