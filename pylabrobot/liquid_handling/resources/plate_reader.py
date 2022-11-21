""" Defines PlateReader class. """

from typing import cast

from pylabrobot.liquid_handling.resources.abstract import Coordinate, Resource, Plate


class NoPlateError(Exception):
  pass


class PlateReader(Resource):
  """ A base for plate readers. """

  def __init__(self, name: str):
    super().__init__(name=name, size_x=0, size_y=0, size_z=0, category="plate_reader")

  def assign_child_resource(self, resource):
    if len(self.children) >= 1:
      raise ValueError("There already is a plate in the plate reader.")
    if not isinstance(resource, Plate):
      raise ValueError("The resource must be a plate.")
    super().assign_child_resource(resource, location=Coordinate.zero())

  def get_plate(self) -> Plate:
    if len(self.children) == 0:
      raise NoPlateError("There is no plate in the plate reader.")
    return cast(Plate, self.children[0])
