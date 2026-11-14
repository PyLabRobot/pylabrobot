"""The pneumatic plate clamp, switched by an auxiliary output on the pressure board."""

import asyncio
import logging
import struct
from typing import TYPE_CHECKING, Optional

from ..errors import FrameError, PlateClampError
from ..framing import PRESSURE_BOARD
from .pump import Pump

if TYPE_CHECKING:
  from ..master import MantisDriver

logger = logging.getLogger(__name__)


class PneumaticPlateClamp:
  """A fixed-force clamp valve and its air supply; readback reports the valve, not plate contact.

  Locking starts the pump when needed. Unlocking restores a pump started by this clamp, leaving
  a pump that was already running alone. Failed locking and shutdown release the valve.
  """

  def __init__(self, *, driver: "MantisDriver", pump: Pump, auxiliary_output: int):
    """Take the unit's configured clamp output and the discovered pressure-board pump."""
    if type(auxiliary_output) is not int:
      raise ValueError("auxiliary_output must be an integer")
    driver._check_index(name="auxiliary_output", value=auxiliary_output, allowed=(2,))
    self._driver = driver
    self._pump = pump
    self._auxiliary_output = auxiliary_output
    self._started_pump = False
    self._operation_lock = asyncio.Lock()

  @property
  def auxiliary_output(self) -> int:
    """The unit's configured clamp output."""
    return self._auxiliary_output

  async def _unchecked_fw_set_locked(self, *, locked: bool) -> None:
    """Set the clamp valve (board 10, command 42), sent once; bools occupy a uint16."""
    await self._driver._send_command(
      board=PRESSURE_BOARD,
      command=42,
      payload=struct.pack("<hH", self.auxiliary_output, locked),
      attempts=1,
    )

  async def request_locked(self, *, attempts: int = 3) -> bool:
    """Whether the valve is commanded to hold the plate; no plate-contact sensor is read."""
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

  async def _set_locked(self, *, locked: bool) -> None:
    """Set the valve only when needed, and require its readback to agree."""
    if await self.request_locked() == locked:
      return
    await self._unchecked_fw_set_locked(locked=locked)
    actual = await self.request_locked()
    if actual != locked:
      raise PlateClampError(
        f"{self._driver.name}: clamp output did not become {locked}", state=int(actual)
      )

  async def _restore_pump(self) -> None:
    """Restore a pump started by this clamp; retain ownership if restoring it fails."""
    if self._started_pump:
      await self._pump._set_pump_on(on=False)
      self._started_pump = False

  async def _release_after_failure(self) -> None:
    """Try both valve release and pump restoration, preserving the operation's error."""
    try:
      await self._unchecked_fw_set_locked(locked=False)
      if await self.request_locked(attempts=1):
        raise PlateClampError(
          f"{self._driver.name}: clamp output remains on after release", state=1
        )
    except Exception:
      logger.warning("[%s] could not release the clamp valve", self._driver.name, exc_info=True)
    try:
      await self._restore_pump()
    except Exception:
      logger.warning("[%s] could not restore the clamp's pump", self._driver.name, exc_info=True)

  async def lock(self, *, level: Optional[int] = None) -> None:
    """Engage the valve and supply air, confirming both control states.

    There is no adjustable lock level or mechanical position feedback. A failure or cancellation
    releases the valve and restores a pump started by this clamp before propagating the error.
    """
    if level is not None:
      raise ValueError("the pneumatic clamp has no lock levels; omit level")
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

  async def unlock(self) -> None:
    """Release the valve and restore a pump started by this clamp."""
    async with self._operation_lock:
      try:
        await self._set_locked(locked=False)
      except BaseException:
        await self._release_after_failure()
        raise
      await self._restore_pump()
      logger.info("[%s] clamp valve released", self._driver.name)

  async def _release(self) -> None:
    """Release at shutdown, including any pump restoration still outstanding."""
    await self.unlock()
