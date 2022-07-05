from abc import ABCMeta
import logging
import typing

from pyhamilton import utils

from .resource import Resource
from .coordinate import Coordinate


logger = logging.getLogger(__name__)


class CarrierSite(metaclass=ABCMeta):
  """ A single site within a carrier. """

  def __init__(
    self,
    location: Coordinate,
    width: float,
    height: float,
    resource: typing.Optional[Resource] = None
  ):
    """ Initialize a new CarrierSite.

    The location of the site is given by the `location` parameter. The location of the resource
    will be updated to be the absolute location of the resource.

    Args:
      location: The location of the site.
      width: The width of the site.
      height: The height of the site.
      resource: The resource that is stored in the site.
    """

    self.location = location
    self.width = width
    self.height = height

    # Update the coordinate to be relative to the carrier.
    if resource is not None:
      resource.location += self.location
    self.resource = resource

  def serialize(self) -> dict:
    """ Serialize the site to a dict. """

    return {
      "location": self.location.serialize(),
      "width": self.width,
      "height": self.height,
      "resource": (self.resource.serialize() if self.resource else None)
    }

  def __repr__(self) -> str:
    """ Return a string representation of the site. """

    return f"CarrierSite(location={self.location.__repr__()}, width={self.width}, " + \
           f"height={self.height}, resource={self.resource})"


class Carrier(Resource, metaclass=ABCMeta):
  """ Abstract base resource for carriers.

  It is recommended to always use a resource carrier to store resources, because this ensures the
  location of the resources can be calculated precisely.

  It is important to use the `__getitem__` and `__setitem__` methods to access the resources,
  because this ensures that the location of the resources is updated to be within the carrier and
  that the appropriate callbacks are called.

  Examples:
    Creating a `TipCarrier` and assigning one set of tips at location 0 (the bottom):

    >>> tip_car = TIP_CAR_480_A00(name='tip carrier')
    >>> tip_car[0] = STF_L(name='tips_1')

    Getting the tips:

    >>> tip_car[0]

    STF_L(name='tips_1')

    Deleting the tips:

    >>> del tip_car[0]

    Alternative way to delete the tips:

    >>> tip_car[0] = None

  Attributes:
    capacity: The maximum number of items that can be stored in this carrier.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: typing.List[CarrierSite],
    category: str = "carrier",
    resource_assigned_callback: typing.Optional[typing.Callable] = None,
    resource_unassigned_callback: typing.Optional[typing.Callable] = None,
    check_can_assign_resource_callback: typing.Optional[typing.Callable] = None,
  ):
    super().__init__(name, size_x, size_y, size_z, category=category)
    self.capacity = len(sites)
    self._sites = sites
    self._resource_assigned_callback = resource_assigned_callback
    self._resource_unassigned_callback = resource_unassigned_callback
    self._check_can_assign_resource_callback = check_can_assign_resource_callback

  def set_check_can_assign_resource_callback(self, callback: typing.Callable):
    """ Called when a resource is about to be assigned to the robot.

    This callback will be called before the resource is assigned to the robot. If the callback
    returns `False`, the resource will not be assigned to the robot and a `ValueError` will be
    raised. This is useful for checking if the resource can be assigned to the robot. Note that the
    callback will also be called for all resources that were assigned to this carrier before this
    callback is assigned.

    Args:
      resource: The resource that is about to be assigned to the robot.
    """

    self._check_can_assign_resource_callback = callback
    for site in self._sites:
      if site.resource is not None:
        error = callback(site.resource)
        if error is not None:
          raise ValueError(f"A resource with name '{site.resource.name}' cannot be assigned to the "
                    "liquid handler.")

  def set_resource_assigned_callback(self, callback: typing.Callable):
    """ Set the callback function that is called when a subresource is assigned to a carrier.

    When subresources have already be assigned, this callback will be called at the end of this
    method for all of them.
    """

    self._resource_assigned_callback = callback
    for site in self._sites:
      if site.resource is not None:
        callback(site.resource)

  def set_resource_unassigned_callback(self, callback: typing.Callable):
    """ Set the callback function that is called when a subresource is unassigned from carrier. """
    self._resource_unassigned_callback = callback

  def get_items(self) -> typing.List[typing.Optional[Resource]]:
    """ Get all items, using self.__getitem__ (so that the location is within this carrier). """
    return [self[k] for k in range(self.capacity)]

  def __getitem__(self, key: int) -> typing.Optional[Resource]:
    """ Get the key'th item in this carrier.

    Returns:
      The resource, if it exists at that location, where the location is updated to be within this
        carrier.
    """

    utils.assert_clamp(key, 0, self.capacity - 1, "key", KeyError)
    return self._sites[key].resource

  def __setitem__(self, key: int, subresource: typing.Optional[Resource]):
    utils.assert_clamp(key, 0, self.capacity - 1, "key", KeyError)

    if subresource is None: # `self[k] = None` is equal to `del self[k]`
      del self[key]
      return

    # Warn if overriding a site.
    if self._sites[key].resource is not None:
      logger.warning("Overriding resource %s with %s.", self._sites[key].resource, subresource)
    # Check if item with name is not set yet.
    if subresource.name in [s.name if s is not None else None for s in self.get_items()]:
      raise ValueError(f"Subresource with name {subresource.name} already set.")

    # Update the location of the resource to be relative to the carrier.
    subresource.location += self._sites[key].location

    if self._check_can_assign_resource_callback is not None:
      error = self._check_can_assign_resource_callback(subresource)
      if error is not None:
        raise ValueError(f"A resource with name '{subresource.name}' cannot be assigned to the "
                          "liquid handler.")

    self._sites[key].resource = subresource

    if self._resource_assigned_callback is not None:
      self._resource_assigned_callback(subresource)

  def __delitem__(self, key: int):
    utils.assert_clamp(key, 0, self.capacity - 1, "key", KeyError)

    resource = self._sites[key].resource
    self._sites[key].resource = None

    if self._resource_unassigned_callback is not None:
      self._resource_unassigned_callback(resource)

  def has_resource(self, resource_name: Resource) -> bool:
    """ Check if the given resource is stored in this carrier. """
    resource_names = [r.name for r in self.get_items() if r is not None]
    return resource_name in resource_names

  def get_resource_by_name(self, name: str) -> typing.Optional[Resource]:
    """ Get the resource with the given name. """
    for site in self._sites:
      if site.resource is not None and site.resource.name == name:
        return site.resource
    return None

  def serialize(self):
    return dict(
      **super().serialize(),
      sites=[
        dict(
          site_id=site_id,
          site=site.serialize()
        ) for site_id, site in enumerate(self._sites)]
    )


class PlateCarrier(Carrier, metaclass=ABCMeta):
  """ Abstract base class for plate carriers. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: typing.List[Coordinate]
  ):
    super().__init__(name, size_x, size_y, size_z, sites, category="plate_carrier")


class TipCarrier(Carrier, metaclass=ABCMeta):
  """ Abstract base class for tip carriers. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sites: typing.List[Coordinate]
  ):
    super().__init__(name, size_x, size_y, size_z, sites, category="tip_carrier")
