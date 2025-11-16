from pylabrobot.arms.precise_flex.precise_flex_backend import PreciseFlexBackend


class PreciseFlex400Backend(PreciseFlexBackend):
  """Backend for the PreciseFlex 400 robotic arm."""

  def __init__(self, host: str, port: int = 10100, timeout=20) -> None:
    super().__init__("pf3400", host, port, timeout)
