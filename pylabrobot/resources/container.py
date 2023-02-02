from abc import ABCMeta
from typing import Optional

from .resource import Resource
from .volume_tracker import ContainerVolumeTracker


class Container(Resource, metaclass=ABCMeta):
  """ A container is an abstract base class for a resource that can hold liquid. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    max_volume: float,
    volume: float = 0,
    category: Optional[str] = None,
    model: Optional[str] = None
  ):
    """ Create a new container.

    Args:
      volume: Initial volume of the container.
      max_volume: Maximum volume of the container.
    """

    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category,
      model=model)
    self.max_volume = max_volume
    self.tracker = ContainerVolumeTracker(max_volume=max_volume)
    self.tracker.set_used_volume(volume)

  def serialize(self):
    return {
      **super().serialize(),
      "volume": self.tracker.get_used_volume(),
      "max_volume": self.max_volume
    }
