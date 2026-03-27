import warnings
from typing import Optional

from pylabrobot.machines.machine import Machine
from pylabrobot.resources import Resource, Rotation
from pylabrobot.scales.scale_backend import ScaleBackend


class Scale(Resource, Machine):
  """Base class for a scale"""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: ScaleBackend,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    Machine.__init__(self, backend=backend)
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
    )
    self.backend: ScaleBackend = backend  # fix type

  async def zero(self, **backend_kwargs) -> None:
    """Calibrate the scale's zero point to the current load.

    Establishes the baseline "empty" reading. Unlike tare, this
    does not account for container weight - it simply resets what
    the scale considers zero. Does not restore scale capacity.
    """
    await self.backend.zero(**backend_kwargs)

  async def tare(self, **backend_kwargs) -> None:
    """Reset the displayed weight to zero, storing the current
    weight as the tare value.

    Use this to measure only the weight of material added after
    taring (e.g. ignoring a container).
    Note: taring does not restore scale capacity.
    """
    await self.backend.tare(**backend_kwargs)

  async def get_weight(self, **backend_kwargs) -> float:
    """Deprecated: use :meth:`read_weight` instead."""
    warnings.warn(
      "scale.get_weight() is deprecated and will be removed in 2026-06. "
      "Use scale.read_weight() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.backend.read_weight(**backend_kwargs)

  async def read_weight(self, **backend_kwargs) -> float:
    """Read the current weight in grams.

    The scale may take a moment to stabilize after loading.
    Use the ``timeout`` backend kwarg to control stability
    behavior: ``"stable"`` waits for a settled reading, ``0`` returns
    immediately, or pass a number of seconds to wait at most that long.
    """
    return await self.backend.read_weight(**backend_kwargs)
