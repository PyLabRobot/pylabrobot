from pylabrobot.peeling.xpeel_backend import PlatePeeler
from pylabrobot.peeling.peeler import Peeler


def xpeel(port: str) -> Peeler:
  return Peeler(
    backend=PlatePeeler(port=port),
  )