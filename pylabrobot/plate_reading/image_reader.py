from typing import Optional

from pylabrobot.plate_reading.backend import ImageReaderBackend
from pylabrobot.plate_reading.imager import Imager
from pylabrobot.plate_reading.plate_reader import PlateReader


class ImageReader(PlateReader, Imager):
  """Microscope which is also a plate reader"""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: ImageReaderBackend,
    category: str = "heating_shaking",
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      backend=backend,
      category=category,
      model=model,
    )
    self.backend: ImageReaderBackend = backend  # fix type
