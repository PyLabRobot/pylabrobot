from typing import Optional

from .container import Container


class Trough(Container):
  """ A trough is a container, particularly useful for multichannel liquid handling operations. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    max_volume: float,
    category: Optional[str] = "trough",
    model: Optional[str] = None
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      max_volume=max_volume,
      category=category,
      model=model
    )
