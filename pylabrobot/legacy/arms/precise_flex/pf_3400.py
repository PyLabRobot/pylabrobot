"""Legacy. Use pylabrobot.brooks.PreciseFlex3400Backend instead."""

from pylabrobot.brooks.precise_flex import PreciseFlex3400Backend as _NewBackend
from pylabrobot.legacy.arms.precise_flex.precise_flex_backend import PreciseFlexBackend


class PreciseFlex3400Backend(PreciseFlexBackend):
  """Legacy. Use pylabrobot.brooks.PreciseFlex3400Backend instead."""

  def __init__(self, host: str, port: int = 10100, has_rail: bool = False, timeout=20) -> None:
    super().__init__(host=host, port=port, has_rail=has_rail, timeout=timeout)
    self._new = _NewBackend(host=host, port=port, has_rail=has_rail, timeout=timeout)
