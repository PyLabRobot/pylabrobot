"""Legacy. Use pylabrobot.brooks.PreciseFlex400 instead."""

from pylabrobot.brooks.precise_flex import PreciseFlexArmBackend, PreciseFlexDriver
from pylabrobot.legacy.arms.precise_flex.precise_flex_backend import PreciseFlexBackend


class PreciseFlex400Backend(PreciseFlexBackend):
  """Legacy. Use pylabrobot.brooks.PreciseFlex400 instead."""

  def __init__(self, host: str, port: int = 10100, has_rail: bool = False, timeout=20) -> None:
    super().__init__(host=host, port=port, has_rail=has_rail, timeout=timeout)
    self._new_driver = PreciseFlexDriver(host=host, port=port, timeout=timeout)
    self._new_backend = PreciseFlexArmBackend(
      driver=self._new_driver, has_rail=has_rail, gripper_length=162.0, gripper_z_offset=0.0
    )
