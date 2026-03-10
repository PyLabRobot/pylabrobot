"""Legacy. Use pylabrobot.capabilities.weighing.ScaleBackend instead."""

import warnings

from pylabrobot.capabilities.weighing.backend import ScaleBackend as _NewScaleBackend


class ScaleBackend(_NewScaleBackend):
  """Legacy. Use pylabrobot.capabilities.weighing.ScaleBackend instead."""

  async def get_weight(self) -> float:
    """Deprecated: Use read_weight() instead."""
    warnings.warn(
      "get_weight() is deprecated. Use read_weight() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.read_weight()
