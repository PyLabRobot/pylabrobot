from abc import ABCMeta
from typing import Optional

from .resource import Resource
from .volume_tracker import VolumeTracker


class Container(Resource, metaclass=ABCMeta):
  """ A container is an abstract base class for a resource that can hold liquid. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    max_volume: Optional[float] = None,
    category: Optional[str] = None,
    model: Optional[str] = None
  ):
    """ Create a new container.

    Args:
      max_volume: Maximum volume of the container. If `None`, will be inferred from resource size.
    """

    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      model=model)
    self.max_volume = max_volume or (size_x * size_y * size_z)
    self.tracker = VolumeTracker(max_volume=self.max_volume)

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "max_volume": self.max_volume
    }
