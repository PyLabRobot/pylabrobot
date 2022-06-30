from abc import ABCMeta, abstractmethod

from .coordinate import Coordinate


class Resource(object, metaclass=ABCMeta):
  """ Abstract base class for deck resources.

  Args:
    name: The name of the resource.
    size_x: The size of the resource in the x-direction.
    size_y: The size of the resource in the y-direction.
    size_z: The size of the resource in the z-direction.
    location: The location of the resource.
    category: The category of the resource, e.g. `tips`, `plate_carrier`, etc.
  """

  @abstractmethod
  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    location: Coordinate = Coordinate(None, None, None),
    category: str = None
  ):
    self.name = name
    self.size_x = size_x
    self.size_y = size_y
    self.size_z = size_z
    self.location = location
    self.category = category

  def serialize(self):
    """ Serialize this resource. """
    return dict(
      name=self.name,
      type=self.__class__.__name__,
      size_x=self.size_x,
      size_y=self.size_y,
      size_z=self.size_z,
      location=self.location.serialize(),
      category=self.category or "unknown"
    )
