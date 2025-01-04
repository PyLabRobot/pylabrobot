from typing import Optional
from pylabrobot.resources.resource import Resource


class Filter(Resource):
  """Filter for plates for use in filtering cells before flow cytometry."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    category: str = "filter",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category, model=model
    )
