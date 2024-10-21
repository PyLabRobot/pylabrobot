from typing import Any, Callable, Dict, Optional

from .resource import Resource
from .coordinate import Coordinate
from .volume_tracker import VolumeTracker

from pylabrobot.serializer import serialize


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
      "compute_volume_from_height": serialize(self._compute_volume_from_height),
      "compute_height_from_volume": serialize(self._compute_height_from_volume),
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

  def get_anchor(self, x: str, y: str, z: str) -> Coordinate:
    """ Get a relative location within the container. (Update to Resource superclass to
      include cavity_bottom)

    Args:
      x: `"l"`/`"left"`, `"c"`/`"center"`, or `"r"`/`"right"`
      y: `"b"`/`"back"`, `"c"`/`"center"`, or `"f"`/`"front"`
      z: `"t"`/`"top"`, `"c"`/`"center"`, `"b"`/`"bottom"`, or `"cb"`/`"cavity_bottom"`

    Returns:
      A relative location within the container, the anchor point wrt the left front bottom corner.
    """

    if z.lower() in {"cavity_bottom"}:
      # Reuse superclass Resource method but update z location based on
      # Container's additional information
      coordinate = super().get_anchor(x, y, z="bottom")
      x_, y_ = coordinate.x, coordinate.y

      if self._material_z_thickness is None:
        raise ValueError("Cavity bottom only implemented for containers with a defined" + \
                         f" material_z_thickness; you used {self.category}")
      z_ = self._material_z_thickness

      return Coordinate(x_, y_, z_)
    else:
      return super().get_anchor(x, y, z)

