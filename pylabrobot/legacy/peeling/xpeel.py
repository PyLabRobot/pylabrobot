from pylabrobot.legacy.peeling.peeler import Peeler
from pylabrobot.legacy.peeling.xpeel_backend import XPeelBackend


def xpeel(port: str) -> Peeler:
  return Peeler(
    backend=XPeelBackend(port=port),
  )
