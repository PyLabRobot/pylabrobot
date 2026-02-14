"""PlateWasher frontend class.

This module provides the user-facing API for plate washers.
"""

from __future__ import annotations

from pylabrobot.machines.machine import Machine
from pylabrobot.plate_washing.backend import PlateWasherBackend
from pylabrobot.resources import Resource


class PlateWasher(Resource, Machine):
  """Frontend class for plate washers.

  Plate washers are devices that automate the washing of microplates.
  This class provides setup/stop lifecycle management, with device-specific
  operations accessed directly on the backend.

  Example:
    >>> from pylabrobot.plate_washing import PlateWasher
    >>> from pylabrobot.plate_washing.biotek.el406 import BioTekEL406Backend
    >>> washer = PlateWasher(
    ...   name="washer",
    ...   size_x=200, size_y=200, size_z=100,
    ...   backend=BioTekEL406Backend()
    ... )
    >>> await washer.setup()
    >>> await washer.backend.manifold_prime(buffer="A", volume=1000)
    >>> await washer.stop()
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: PlateWasherBackend,
    category: str | None = None,
    model: str | None = None,
  ) -> None:
    """Initialize a PlateWasher.

    Args:
      name: Unique name for this plate washer.
      size_x: Width of the washer in millimeters.
      size_y: Depth of the washer in millimeters.
      size_z: Height of the washer in millimeters.
      backend: Backend implementation for hardware communication.
      category: Optional category string.
      model: Optional model string.
    """
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )
    Machine.__init__(self, backend=backend)
    self.backend: PlateWasherBackend = backend
