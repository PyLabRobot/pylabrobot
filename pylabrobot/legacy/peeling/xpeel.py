"""Legacy. Use pylabrobot.azenta.XPeel instead."""

from pylabrobot.legacy.peeling.peeler import Peeler
from pylabrobot.legacy.peeling.xpeel_backend import XPeelBackend


def xpeel(port: str) -> Peeler:
  """Legacy. Use pylabrobot.azenta.XPeel instead."""
  return Peeler(
    backend=XPeelBackend(port=port),
  )
