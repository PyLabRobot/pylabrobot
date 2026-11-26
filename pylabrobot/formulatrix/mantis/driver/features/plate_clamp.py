"""The plate clamp that holds the plate on the deck: a solenoid on the LC3 board (board 12), or a
valve on a pressure-board output (board 10) supplied by the air pump."""

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
from ..framing import LC3_BOARD, PRESSURE_BOARD
from .pump import Pump

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
  """The plate clamp, on board 12 or on a pressure-board output, with the same methods.

  On board 12, locking waits until the device reports clamping done. On a pressure-board output
  the valve's readback is read, never plate contact; it has no lock levels and no clamp state.
  Locking it starts the pump when needed; unlocking leaves the pump on. A failed lock or unlock and
  shutdown release the valve, then turn off a pump this clamp started.
  """

  # Lock level when the caller names none: 1, the weakest.
  default_lock_level: int = 1

  def __init__(
    self,
    *,
    driver: "MantisDriver",
    levels: Optional[Dict[int, Tuple[float, int]]] = None,
    auxiliary_output: Optional[int] = None,
    pump: Optional[Pump] = None,
  ):
    """
    Args:
      driver: the driver it sends through.
      levels: a clamp on board 12: the unit's lock levels, level -> (PWM duty, timeout_ms), as
        declared; None when none are declared, and then ``lock`` refuses.
      auxiliary_output: a clamp on a pressure-board output: the output switching its valve.
      pump: the pump supplying that valve's air; given with ``auxiliary_output`` only.

    Raises:
      ValueError: unless given ``levels`` alone, nothing, or ``auxiliary_output`` with ``pump``;
        or if ``auxiliary_output`` is not 2.
    """
    if (auxiliary_output is None) != (pump is None) or (
      auxiliary_output is not None and levels is not None
    ):
      raise ValueError("a plate clamp takes levels, or auxiliary_output together with pump")
    if auxiliary_output is not None:
      if type(auxiliary_output) is not int:
        raise ValueError("auxiliary_output must be an integer")
      driver._check_index(name="auxiliary_output", value=auxiliary_output, allowed=(2,))
    self._driver = driver
    self.levels = levels
    self._auxiliary_output = auxiliary_output
    self._pump = pump
    self._started_pump = False
    self._operation_lock = asyncio.Lock()

  @property
  def auxiliary_output(self) -> Optional[int]:
    """The pressure-board output switching the clamp's valve; None for a clamp on board 12."""
    return self._auxiliary_output

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

  async def _unchecked_fw_set_auxiliary_output(self, *, on: bool) -> None:
    """Switch the clamp's valve (board 10, command 42), sent once.

    Args:
      on: whether the valve holds the plate.
    """
    await self._driver._send_command(
      board=PRESSURE_BOARD,
      command=42,
      payload=struct.pack("<hH", self.auxiliary_output, on),
      attempts=1,
    )

  async def request_state(self, *, attempts: int = 3) -> int:
    """The clamp's state (GetMotorClampState); ``CLAMP_STATE_DESCRIPTIONS`` says what each means.

    Clamping done (3) means the lock's timer ran out, not that a plate is held: a device test read
    the same states at the same times with and without a plate.

    Args:
      attempts: how often the read is sent before giving up.

    Raises:
      MantisConfigurationError: for a clamp on a pressure-board output, which has no state;
        nothing is sent.
    """
    if self.auxiliary_output is not None:
      raise MantisConfigurationError(
        f"{self._driver.name}: a clamp on a pressure-board output has no clamp state; "
        "request_locked reads its valve"
      )
    reply = await self._driver._send_command(board=LC3_BOARD, command=33, attempts=attempts)
    if len(reply) < 2:
      raise FrameError(f"{self._driver.name}: a clamp state reply is 2 bytes, got {reply.hex()}")
    (state,) = struct.unpack_from("<H", reply)
    return int(state)

  async def _request_auxiliary_output(self, *, attempts: int = 3) -> bool:
    """Whether the clamp's valve is switched on (board 10, command 41); not plate contact.

    Args:
      attempts: how often the read is sent before giving up.

    Raises:
      FrameError: if the reply is shorter than 2 bytes or not 0 or 1.
    """
    reply = await self._driver._send_command(
      board=PRESSURE_BOARD,
      command=41,
      payload=struct.pack("<h", self.auxiliary_output),
      attempts=attempts,
    )
    if len(reply) < 2:
      raise FrameError(f"{self._driver.name}: a clamp output reply is 2 bytes, got {reply.hex()}")
    (state,) = struct.unpack_from("<H", reply)
    if state not in (0, 1):
      raise FrameError(f"{self._driver.name}: invalid clamp output state {state}")
    return bool(state)

  async def request_locked(self, *, attempts: int = 3) -> bool:
    """Whether the clamp holds: on board 12 whether it reports clamping done; on a pressure-board
    output whether its valve reads switched on, which is not plate contact.

    Args:
      attempts: how often the read is sent before giving up.
    """
    if self.auxiliary_output is None:
      return await self.request_state(attempts=attempts) == CLAMPING_DONE
    return await self._request_auxiliary_output(attempts=attempts)

  async def _set_locked(self, *, locked: bool) -> None:
    """Switch the valve only when needed, and require its readback to agree.

    Args:
      locked: whether the valve holds the plate.
    """
    if await self._request_auxiliary_output() == locked:
      return
    await self._unchecked_fw_set_auxiliary_output(on=locked)
    actual = await self._request_auxiliary_output()
    if actual != locked:
      raise PlateClampError(
        f"{self._driver.name}: clamp output did not become {locked}", state=int(actual)
      )

  async def _restore_pump(self) -> None:
    """Turn off a pump this clamp started; it stays this clamp's if that fails."""
    if self._started_pump and self._pump is not None:
      await self._pump._set_pump_on(on=False)
      self._started_pump = False

  async def _release_after_failure(self) -> None:
    """Try both the valve's release and the pump's restoration, keeping the operation's error."""
    try:
      await self._unchecked_fw_set_auxiliary_output(on=False)
      if await self._request_auxiliary_output(attempts=1):
        raise PlateClampError(
          f"{self._driver.name}: clamp output remains on after release", state=1
        )
    except Exception:
      logger.warning("[%s] could not release the clamp valve", self._driver.name, exc_info=True)
    try:
      await self._restore_pump()
    except Exception:
      logger.warning("[%s] could not restore the clamp's pump", self._driver.name, exc_info=True)

  async def unlock(self) -> None:
    """Release the clamp: on board 12 wait until it reads unclamping done; on a pressure-board
    output switch the valve off and leave the pump on.

    Raises:
      PlateClampError: at once, if it reads a state in ``UNCLAMPING_FAILED``, or the valve does not
        read back off.
      MantisTimeoutError: if it does not read unclamping done within ``CLAMP_WAIT_TRIES`` times
        ``UNCLAMPING_TIMEOUT``, polled every ``CLAMP_STATE_POLL_INTERVAL``.
    """
    if self.auxiliary_output is not None:
      async with self._operation_lock:
        try:
          await self._set_locked(locked=False)
        except BaseException:
          await self._release_after_failure()
          raise
        logger.info("[%s] clamp valve released", self._driver.name)
      return
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

  async def _release(self) -> None:
    """Release at shutdown: on board 12 unless it already reads unclamping done; on a
    pressure-board output always, then turn off a pump this clamp started."""
    if self.auxiliary_output is None:
      if await self.request_state(attempts=1) != UNCLAMPING_DONE:
        await self.unlock()
      return
    await self.unlock()
    async with self._operation_lock:
      await self._restore_pump()

  async def lock(self, *, level: Optional[int] = None) -> None:
    """On board 12, release the clamp, lock it at ``level`` and wait until it reads clamping done;
    if it does not, it is released again before raising. On a pressure-board output, switch the
    valve on and start the pump if it is off; a failure or cancellation releases the valve and
    restores a pump this clamp started before raising.

    On board 12 clamping done is the lock's timer running out; it does not tell a plate is held.

    Args:
      level: one of ``levels``, 1 the weakest. ``default_lock_level`` when None. None only on a
        pressure-board output, which has no levels.

    Raises:
      MantisConfigurationError: if no levels are declared; nothing is sent.
      ValueError: if ``level`` is not in ``levels``, or is given on a pressure-board output;
        nothing is sent.
      PlateClampError: at once, after the release, if it reads a state in ``CLAMPING_FAILED``, or
        the valve does not read back on.
      MantisTimeoutError: if it does not read clamping done within ``CLAMP_WAIT_TRIES`` times
        ``CLAMPING_TIMEOUT``, polled every ``CLAMP_STATE_POLL_INTERVAL``.
    """
    if self.auxiliary_output is not None:
      if level is not None:
        raise ValueError("a clamp on a pressure-board output has no lock levels; omit level")
      if self._pump is None:
        raise MantisConfigurationError(f"{self._driver.name}: no pump supplies the clamp's air")
      async with self._operation_lock:
        pump_on = await self._pump.request_pump_on()
        try:
          await self._set_locked(locked=True)
          if not pump_on:
            # Own the change before sending: a cancelled or unanswered write may still take effect.
            self._started_pump = True
            await self._pump._set_pump_on(on=True)
        except BaseException:
          await self._release_after_failure()
          raise
        logger.info("[%s] clamp valve engaged, air pump on", self._driver.name)
      return
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
