from typing import Optional

from pylabrobot.resources.container import Container


class Tube(Container):
  """ Tube container, like Eppendorf tubes. """

  def __init__(self, name: str, size_x: float, size_y: float, size_z: float,
    category: str = "tube", max_volume: Optional[float] = None, model: Optional[str] = None):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      max_volume=max_volume,
      model=model
    )
