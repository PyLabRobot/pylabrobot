from abc import ABCMeta
import copy
import logging
import typing

from .resource import Resource
from .coordinate import Coordinate


logger = logging.getLogger(__name__)


class Carrier(Resource, metaclass=ABCMeta):
  """
  ... carriers act as containers ...
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    site_positions: typing.List[Coordinate]
  ):
    super().__init__(name, size_x, size_y, size_z)
    self.capacity = len(site_positions)

    self._sites = {}
    for i, loc in enumerate(site_positions):
      self._sites[i] = {"loc": loc, "item": None}

  def get_items(self):
    """ Get all items, using self.__getitem__ (meaning the location is within this carrier). """
    return [self[k] for k in sorted(self._sites.keys())]

  def __getitem__(self, key: int):
    """ Get the key'th item in this carrier.

    Returns:
      A deep copy, where the location is updated to be within this carrier.
    """
    s = copy.deepcopy(self._sites[key])
    if s["item"] is not None:
      s["item"].location += self._sites[key]["loc"]
    return s["item"]

  def __setitem__(self, key: int, subresource: Resource):
    # Warn if overriding a site.
    if self._sites[key]["item"] is not None:
      logger.warning("Overriding resource %s with %s.", self._sites[key], subresource)
    # Check if item with name is not set yet.
    if subresource.name in [s.name if s is not None else None for s in self.get_items()]:
      raise ValueError(f"Subresource with name {subresource.name} already set.")
    self._sites[key]["item"] = subresource

  def __delitem__(self, key: int):
    self._sites[key]["item"] = None


class PlateCarrier(Carrier, metaclass=ABCMeta):
  pass


class TipCarrier(Carrier, metaclass=ABCMeta):
  pass
