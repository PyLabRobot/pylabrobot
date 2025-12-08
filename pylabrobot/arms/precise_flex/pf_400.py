from pylabrobot.arms.precise_flex.precise_flex_backend import PreciseFlexBackend


class PreciseFlex400Backend(PreciseFlexBackend):
  """Backend for the PreciseFlex 400 robotic arm."""

  def __init__(self, has_rail: bool = False, host: str, port: int = 10100, timeout=20) -> None:
    super().__init__(has_rail=has_rail, host=host, port=port, timeout=timeout)
