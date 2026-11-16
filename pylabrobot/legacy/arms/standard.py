from dataclasses import dataclass

from pylabrobot.resources import Coordinate, Rotation


@dataclass
class CartesianCoords:
  location: Coordinate
  rotation: Rotation
