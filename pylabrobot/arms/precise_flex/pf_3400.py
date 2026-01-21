from pylabrobot.arms.precise_flex.precise_flex_backend import PreciseFlexBackend


class PreciseFlex3400Backend(PreciseFlexBackend):
  """Backend for the PreciseFlex 3400 robotic arm."""

  def __init__(self, host: str, port: int = 10100, has_rail: bool = False, timeout=20) -> None:
    super().__init__(host=host, port=port, has_rail=has_rail, timeout=timeout)
