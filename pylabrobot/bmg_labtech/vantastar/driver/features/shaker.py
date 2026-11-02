"""The shaker: moves the plate to mix its wells (IdleMoveCommand)."""

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
  from ..master import VANTAstarDriver

ShakePattern = Literal["orbital", "linear", "double_orbital"]

# IdleMoveCommand modes for the VANTAstar's shaking patterns.
_SHAKE_PATTERNS = {"orbital": 5, "double_orbital": 7, "linear": 8}
# Shakes this long or longer pause 1 s every 59 s, as Voyager sends them.
_SHAKE_LONG = 300
_SHAKE_ON_OFF = (59, 1)


class Shaker:
  """The shaker (IdleMoveCommand).

  Reached as ``driver.shaker``.
  """

  def __init__(self, driver: "VANTAstarDriver"):
    """
    Args:
      driver: the driver to send commands through.
    """
    self._driver = driver

  @staticmethod
  def build_shake(pattern: ShakePattern, speed: int, duration: int) -> bytes:
    """IdleMoveCommand fields: mode (the pattern), speed code (rpm / 100 - 1), duration (s), off and on times.

    Raises:
      ValueError: if the pattern is unknown, the speed not a positive multiple of 100 rpm, or the
        duration not 1-65535 s.
    """
    if pattern not in _SHAKE_PATTERNS:
      raise ValueError(f"shake pattern must be one of {sorted(_SHAKE_PATTERNS)}, got {pattern!r}")
    if speed < 100 or speed % 100 != 0:
      raise ValueError(f"shake speed must be a positive multiple of 100 rpm, got {speed}")
    if not 1 <= duration <= 0xFFFF:
      raise ValueError(f"shake duration must be 1-65535 s, got {duration}")
    on, off = _SHAKE_ON_OFF if duration >= _SHAKE_LONG else (0, 0)
    return (
      bytes([_SHAKE_PATTERNS[pattern], speed // 100 - 1])
      + duration.to_bytes(2, "big")
      + off.to_bytes(2, "big")
      + on.to_bytes(2, "big")
    )

  async def shake(self, pattern: ShakePattern, speed: int, duration: int) -> None:
    """Shake the plate and wait until the shaking is done.

    Like Voyager, first moves the plate in when the drawer is out or the motors are off.

    Args:
      pattern: "orbital", "linear" or "double_orbital".
      speed: rpm, a multiple of 100; the tested reader reports 700 rpm as its maximum for every
        pattern (not tested above).
      duration: seconds.

    Raises:
      ValueError: if an argument is invalid (``build_shake``).
      CommandRefusedError: if the reader refuses the shake.
    """
    body = self.build_shake(pattern, speed, duration)
    status = await self._driver.request_status()
    if status.drawer_open or not status.motors_enabled:
      await self._driver.drawer.close()
    await self._driver._idle_move(body)
    await self._driver._wait_until_ready(duration + 60.0, self._driver.default_poll_interval)
