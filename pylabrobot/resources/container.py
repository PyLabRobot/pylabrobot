import marshal
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
    material_z_thickness: Optional[float] = None,
    max_volume: Optional[float] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
    compute_volume_from_height: Optional[Callable[[float], float]] = None,
    compute_height_from_volume: Optional[Callable[[float], float]] = None,
  ):
    """ Create a new container.

    Args:
      material_z_thickness: Container cavity base to the (outer) base of the container object. If
        `None`, certain operations may not be supported.
      max_volume: Maximum volume of the container. If `None`, will be inferred from resource size.
    """

    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      model=model)
    self._material_z_thickness = material_z_thickness
    self.max_volume = max_volume or (size_x * size_y * size_z)
    self.tracker = VolumeTracker(max_volume=self.max_volume)
    self._compute_volume_from_height = compute_volume_from_height
    self._compute_height_from_volume = compute_height_from_volume

  @property
  def material_z_thickness(self) -> float:
    if self._material_z_thickness is None:
      raise NotImplementedError(
        f"The current operation is not supported for resource named '{self.name}' of type "
        f"'{self.__class__.__name__}' because material_z_thickness is not defined.")
    return self._material_z_thickness

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "max_volume": self.max_volume,
      "material_z_thickness": self._material_z_thickness,
      "compute_volume_from_height": marshal.dumps(self._compute_volume_from_height.__code__)
        if self._compute_volume_from_height is not None else None,
      "compute_height_from_volume": marshal.dumps(self._compute_height_from_volume.__code__)
        if self._compute_height_from_volume is not None else None,
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
