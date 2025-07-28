from pylabrobot.sealing.a4s_backend import A4SBackend
from pylabrobot.sealing.sealer import Sealer


def a4s(port: str) -> Sealer:
  # https://web.azenta.com/hubfs/azenta-files/resources/tech-drawings/TD-automated-roll-heat-sealer.pdf
  # 222 x 500 x 276 mm
  return Sealer(
    backend=A4SBackend(port=port),
  )
