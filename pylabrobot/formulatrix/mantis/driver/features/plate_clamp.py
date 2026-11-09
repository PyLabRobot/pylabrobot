"""The plate clamp: a solenoid on the LC3 board (board 12) that holds the plate on the deck."""

import asyncio
import logging
import struct
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from ..errors import (
  CLAMP_STATE_DESCRIPTIONS,
  FrameError,
  MantisConfigurationError,
  MantisTimeoutError,
  PlateClampError,
)
from ..framing import LC3_BOARD

if TYPE_CHECKING:
  from ..master import MantisDriver

logger = logging.getLogger(__name__)

# The clamp's states are in CLAMP_STATE_DESCRIPTIONS.
CLAMPING_DONE = 3
UNCLAMPING_DONE = 6
# The states the clamp reports a failure in; a wait ends on them at once.
CLAMPING_FAILED = (4, 8)
UNCLAMPING_FAILED = (7, 8)
# The waits for a state: the poll interval (s), the tries and each command's timeout (s).
CLAMP_STATE_POLL_INTERVAL = 0.1
CLAMP_WAIT_TRIES = 3
CLAMPING_TIMEOUT = 3.0
UNCLAMPING_TIMEOUT = 5.0


class PlateClamp:
  """The plate clamp. Locking waits until the device reports clamping done."""

  # Lock level when the caller names none: 1, the weakest.
  default_lock_level: int = 1

  def __init__(self, *, driver: "MantisDriver", levels: Optional[Dict[int, Tuple[float, int]]]):
    """
    Args:
      driver: the driver it sends through.
      levels: the unit's lock levels, level -> (PWM duty, timeout_ms), as declared; None when
        none are declared, and then ``lock`` refuses.
    """
    self._driver = driver
    self.levels = levels

  async def _unchecked_fw_do_plate_unclamping(self) -> None:
    """DoPlateUnclamping (board 12, command 31), sent once; its payload is fixed."""
    await self._driver._send_command(
      board=LC3_BOARD,
      command=31,
      payload=struct.pack("<ddI", 0.75, 0.0, 5000),
      attempts=1,
    )

  async def _unchecked_fw_do_plate_clamping(self, *, pwm: float, timeout_ms: int) -> None:
    """DoPlateClamping (board 12, command 30), sent once.

    Args:
      pwm: the solenoid's PWM duty.
      timeout_ms: the device's clamping timeout, in ms.
    """
    await self._driver._send_command(
      board=LC3_BOARD,
      command=30,
      payload=struct.pack("<ddI", 0.0, pwm, timeout_ms),
      attempts=1,
    )

  async def request_state(self, *, attempts: int = 3) -> int:
    """The clamp's state (GetMotorClampState); ``CLAMP_STATE_DESCRIPTIONS`` says what each means.

    Args:
      attempts: how often the read is sent before giving up.
    """
    reply = await self._driver._send_command(board=LC3_BOARD, command=33, attempts=attempts)
    if len(reply) < 2:
      raise FrameError(f"{self._driver.name}: a clamp state reply is 2 bytes, got {reply.hex()}")
    (state,) = struct.unpack_from("<H", reply)
    return int(state)

  async def unlock(self) -> None:
    """Release the clamp and wait until it reads unclamping done.

    Raises:
      PlateClampError: at once, if it reads a state in ``UNCLAMPING_FAILED``.
      MantisTimeoutError: if it does not read unclamping done within ``CLAMP_WAIT_TRIES`` times
        ``UNCLAMPING_TIMEOUT``, polled every ``CLAMP_STATE_POLL_INTERVAL``.
    """
    await self._unchecked_fw_do_plate_unclamping()
    state = await self.request_state()
    for _ in range(CLAMP_WAIT_TRIES * round(UNCLAMPING_TIMEOUT / CLAMP_STATE_POLL_INTERVAL)):
      if state == UNCLAMPING_DONE:
        return
      if state in UNCLAMPING_FAILED:
        raise PlateClampError(
          f"{self._driver.name}: the clamp did not release: "
          f"{CLAMP_STATE_DESCRIPTIONS[state]} (state {state})",
          state=state,
        )
      await asyncio.sleep(CLAMP_STATE_POLL_INTERVAL)
      state = await self.request_state()
    if state != UNCLAMPING_DONE:
      raise MantisTimeoutError(
        f"{self._driver.name}: the clamp did not release; last state {state}"
      )

  async def lock(self, *, level: Optional[int] = None) -> None:
    """Release the clamp, lock it at ``level`` and wait until it reads clamping done.

    If it does not, the clamp is released again before raising.

    Args:
      level: one of ``levels``, 1 the weakest. ``default_lock_level`` when None.

    Raises:
      MantisConfigurationError: if no levels are declared; nothing is sent.
      ValueError: if ``level`` is not in ``levels``; nothing is sent.
      PlateClampError: at once, after the release, if it reads a state in ``CLAMPING_FAILED``.
      MantisTimeoutError: if it does not read clamping done within ``CLAMP_WAIT_TRIES`` times
        ``CLAMPING_TIMEOUT``, polled every ``CLAMP_STATE_POLL_INTERVAL``.
    """
    if self.levels is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: no clamp levels for this unit or its generation; give its "
        "vendor folder (oem_config_directory)"
      )
    level = self.default_lock_level if level is None else level
    self._driver._check_index(name="level", value=level, allowed=tuple(self.levels))
    await self.unlock()
    locked = False
    try:
      pwm, timeout_ms = self.levels[level]
      await self._unchecked_fw_do_plate_clamping(pwm=pwm, timeout_ms=timeout_ms)
      state = await self.request_state()
      for _ in range(CLAMP_WAIT_TRIES * round(CLAMPING_TIMEOUT / CLAMP_STATE_POLL_INTERVAL)):
        if state == CLAMPING_DONE:
          break
        if state in CLAMPING_FAILED:
          raise PlateClampError(
            f"{self._driver.name}: the clamp did not lock at level {level}: "
            f"{CLAMP_STATE_DESCRIPTIONS[state]} (state {state})",
            state=state,
          )
        await asyncio.sleep(CLAMP_STATE_POLL_INTERVAL)
        state = await self.request_state()
      if state != CLAMPING_DONE:
        raise MantisTimeoutError(
          f"{self._driver.name}: the clamp did not report clamping done at level {level}; "
          f"last state {state}"
        )
      locked = True
    finally:
      if not locked:
        try:
          await self.unlock()
        except Exception:
          logger.warning("[%s] could not release the clamp", self._driver.name, exc_info=True)
