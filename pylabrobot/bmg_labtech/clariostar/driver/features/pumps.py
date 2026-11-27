"""The injector pumps: prime and backflush (PUMP command family, 0x02).

Verified on an original CLARIOstar with firmware 1.20 and two XCalibur pumps: the command encoding
for the settings in `SEEN_PRIME` and `SEEN_BACKFLUSH`, backflushes of pumps 1 and 2 and primes of
pump 1 at 500 uL and 100 uL/s, and the reagent door bit while idle.
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Dict, FrozenSet, Optional, Tuple

from pylabrobot.bmg_labtech.protocol.errors import FrameError

from ..errors import PumpAbortedError, describe_quit_codes, parse_warning_codes

if TYPE_CHECKING:
  from ..master import CLARIOstarDriver

logger = logging.getLogger(__name__)


class Pumps:
  """Prime and backflush the injector pumps.

  Reached as `driver.pumps` (or `CLARIOstar.pumps`), built when the reader reports a pump. A
  command is `02 <pump byte> <flow rate code> <volume / 500 uL>`; the pump byte is the pump
  number, plus 0x10 for a backflush. The reader answers the command at once, then runs it for
  several seconds to minutes and reports busy until it is done.
  """

  # Flow rate (uL/s) -> flow rate code.
  FLOW_RATE_CODES: Dict[int, int] = {100: 0x0C, 300: 0x04, 430: 0x01}
  # Volume byte = volume / 500 uL.
  VOLUME_UNIT_UL = 500
  MIN_VOLUME_UL = 500
  MAX_VOLUME_UL = 4500
  # Pumps whose byte is verified (pump 3's byte is not known).
  ENCODED_PUMPS = (1, 2)
  BACKFLUSH_FLAG = 0x10
  # Settings whose encoding is verified on a reader, as (flow rates, volumes); the others in range
  # follow the same rule and are accepted with a warning.
  SEEN_PRIME: Tuple[FrozenSet[int], FrozenSet[int]] = (
    frozenset({100, 300, 430}),
    frozenset({500, 2000, 4500}),
  )
  SEEN_BACKFLUSH: Tuple[FrozenSet[int], FrozenSet[int]] = (
    frozenset({100, 430}),
    frozenset({500, 1000}),
  )
  # Firmware on which STATUS byte 3 bit 6 is checked to be the reagent door.
  DOOR_CHECKED_FIRMWARE: FrozenSet[str] = frozenset({"1.20"})

  # Seconds a pump command may run: 2000 uL at 100 uL/s takes about 1 min, 4500 uL about 2 min.
  default_timeout: float = 300.0
  # Seconds for the reader to report busy after the command; a command's reply can read idle
  # while the reader starts it.
  default_start_timeout: float = 5.0
  default_poll_interval: float = 0.25

  def __init__(self, driver: "CLARIOstarDriver"):
    """
    Args:
      driver: the driver to send commands through.
    """
    self._driver = driver

  def _fitted(self) -> Tuple[int, ...]:
    """The numbers of the pumps the reader reports."""
    pumps = self._driver.configuration.pumps or (None, None, None)
    return tuple(i for i, kind in enumerate(pumps, 1) if kind is not None)

  def _door_checked(self) -> bool:
    """Whether the reader's firmware is one on which the reagent door bit is checked."""
    return self._driver.configuration.firmware_version in self.DOOR_CHECKED_FIRMWARE

  def _encode(self, pump: int, volume: float, flow_rate: float, backflush: bool) -> bytes:
    """The command's parameters (after the family byte); logs a warning for a setting whose
    encoding is inferred rather than verified.

    Raises:
      ValueError: listing every setting that is out of range, unencoded or not fitted.
    """
    errors = []
    if not isinstance(pump, int) or pump not in self.ENCODED_PUMPS:
      errors.append(f"pump must be one of {self.ENCODED_PUMPS}, got {pump!r}")
    elif pump not in self._fitted():
      errors.append(f"pump {pump} is not fitted (fitted: {self._fitted() or 'none'})")
    if flow_rate not in self.FLOW_RATE_CODES:
      errors.append(
        f"flow_rate must be one of {sorted(self.FLOW_RATE_CODES)} uL/s, got {flow_rate:g}"
      )
    if not self.MIN_VOLUME_UL <= volume <= self.MAX_VOLUME_UL or volume % self.VOLUME_UNIT_UL:
      errors.append(
        f"volume must be a multiple of {self.VOLUME_UNIT_UL} uL from {self.MIN_VOLUME_UL} to"
        f" {self.MAX_VOLUME_UL}, got {volume:g}"
      )
    if errors:
      raise ValueError("; ".join(errors))
    seen_flow_rates, seen_volumes = self.SEEN_BACKFLUSH if backflush else self.SEEN_PRIME
    if flow_rate not in seen_flow_rates or volume not in seen_volumes:
      logger.warning(
        "%s at %g uL and %g uL/s: this setting's encoding is inferred, not verified on a reader",
        "backflush" if backflush else "prime",
        volume,
        flow_rate,
      )
    pump_byte = pump | (self.BACKFLUSH_FLAG if backflush else 0)
    return bytes(
      [pump_byte, self.FLOW_RATE_CODES[int(flow_rate)], int(volume) // self.VOLUME_UNIT_UL]
    )

  async def _run(
    self,
    parameters: bytes,
    timeout: Optional[float],
    start_timeout: Optional[float],
    is_prime: bool,
  ) -> None:
    """Read the status, send a pump command once, and wait until the reader has run it.

    Done once the reader has reported busy (in the command's reply or a later status) and then
    idle. The command is sent once: a malformed reply does not mean the reader missed the command,
    so the driver discards the reply and reads busy and idle from status polls, as `initialize`
    does for INIT.

    The reader reports an aborted command with a warning (quit codes) in a status reply. A
    warning already present before the command is not taken for a new one. A warning sent between
    exchanges can be lost on the FTDI read path, so on firmware in `DOOR_CHECKED_FIRMWARE` a prime
    also watches the reagent door (STATUS byte 3 bit 6). With the door shut the driver sends
    nothing. If the door closes during the prime, the driver waits until the reader reads idle,
    because the reader may still be pumping, and then raises `PumpAbortedError`.

    After a timeout or a cancelled call the reader keeps running the command; the driver sends no
    stop.

    Args:
      parameters: the command's parameters.
      timeout: seconds the command may run; None uses `default_timeout`.
      start_timeout: seconds for the reader to report busy; None uses `default_start_timeout`.
      is_prime: the command is a prime (the reagent door applies).

    Raises:
      RuntimeError: if a measurement or another pump command is running on the reader, or for a
        prime with the reagent door shut; nothing is sent.
      PumpAbortedError: if a reply contains a new device warning, or the door closed during a prime
        (raised once the reader is idle).
      TimeoutError: if the reader does not report busy within `start_timeout`, or is still busy
        after `timeout`.
      CommandRefusedError: if a status reply has the refused bit set.
      FrameError: if status replies stay malformed after retries.
    """
    driver = self._driver
    if driver._measuring:
      raise RuntimeError(
        "a measurement or pump command is already running on this reader; wait for it to end"
      )
    driver._measuring = True
    try:
      await self._send_and_wait(parameters, timeout, start_timeout, is_prime)
    finally:
      driver._measuring = False

  async def _send_and_wait(
    self,
    parameters: bytes,
    timeout: Optional[float],
    start_timeout: Optional[float],
    is_prime: bool,
  ) -> None:
    """`_run` without the one-operation guard."""
    driver = self._driver
    timeout = self.default_timeout if timeout is None else timeout
    start_timeout = self.default_start_timeout if start_timeout is None else start_timeout
    door_open = (await driver.request_status()).reagent_door_open
    stale = driver._last_warning
    watch_door = is_prime and self._door_checked()
    if watch_door and not door_open:
      raise RuntimeError("open the reagent door: the reader primes only with it open")
    if is_prime and not watch_door:
      logger.warning(
        "the reagent door bit is not checked on firmware %s; priming without checking the door",
        driver.configuration.firmware_version,
      )
    door_closed = False
    try:
      reply = await driver.send_command(driver.CommandFamily.PUMP, parameters=parameters, retries=1)
    except FrameError as e:
      logger.warning("pump command reply malformed (%s); waiting on status instead", e)
      await driver._purge_rx()
      started = False
    else:
      warning = parse_warning_codes(reply)
      if warning is not None and warning != stale:
        raise PumpAbortedError(parameters, describe_quit_codes(warning), warning)
      started = driver._parse_status(reply).busy
    status_payload = bytes([driver.CommandFamily.STATUS])
    start = time.monotonic()
    while True:
      await asyncio.sleep(self.default_poll_interval)
      reply = await driver._exchange_with_retries(status_payload, retries=5)
      warning = parse_warning_codes(reply)
      driver._check_reply(status_payload, reply)
      if warning is not None and warning != stale:
        # Usually this poll's own reply. If the warning came unasked instead, the poll's own reply
        # is still on its way: discard it, or every later reply is one behind.
        await driver._drain_rx()
        raise PumpAbortedError(parameters, describe_quit_codes(warning), warning)
      status = driver._parse_status(reply)
      busy = status.busy
      if watch_door and not door_closed and not status.reagent_door_open:
        door_closed = True
        logger.warning("the reagent door closed during the prime; waiting until the reader is idle")
      elapsed = time.monotonic() - start
      if busy:
        started = True
      elif started:
        if door_closed:
          raise PumpAbortedError(
            parameters, "the reagent door closed during the prime; the reader is idle"
          )
        return
      elif elapsed >= start_timeout:
        raise TimeoutError(f"the reader did not start the pump command within {start_timeout} s")
      if elapsed >= timeout:
        raise TimeoutError(f"the pump command still runs after {timeout} s")

  async def prime(
    self,
    pump: int,
    volume: float,
    flow_rate: float = 100,
    timeout: Optional[float] = None,
    start_timeout: Optional[float] = None,
  ) -> None:
    """Prime a pump: draw reagent and push it out through the injector.

    The reader primes only with the reagent door open (STATUS byte 3 bit 6,
    `CLARIOstarStatus.reagent_door_open`); with it shut the reader answers a prime with quit codes
    243/12. On firmware in `DOOR_CHECKED_FIRMWARE` this method raises `RuntimeError` without
    sending when the door bit reads shut. On other firmware it does not check the door, and the
    reader's quit codes raise `PumpAbortedError`. How much a prime delivers when it ends early is
    not known.

    Args:
      pump: 1 or 2.
      volume: uL, a multiple of 500 from 500 to 4500.
      flow_rate: uL/s, one of `FLOW_RATE_CODES`.
      timeout: seconds the prime may run; None uses `default_timeout`. A timeout leaves the
        reader pumping.
      start_timeout: seconds for the reader to report busy; None uses `default_start_timeout`.

    Raises:
      ValueError: if a setting is out of range or the pump is not fitted; nothing is sent.
      RuntimeError: if the reagent door is shut, or another operation is running; nothing is
        sent.
      PumpAbortedError: if the reader reported the prime aborted, or the door closed during it.
      TimeoutError: if the reader does not start or end the prime in time.
      CommandRefusedError: if a status reply has the refused bit set.
      FrameError: if status replies stay malformed after retries.
    """
    parameters = self._encode(pump, volume, flow_rate, backflush=False)
    await self._run(parameters, timeout, start_timeout, is_prime=True)
    logger.info("pump %d primed with %g uL at %g uL/s", pump, volume, flow_rate)

  async def backflush(
    self,
    pump: int,
    volume: float,
    flow_rate: float = 100,
    timeout: Optional[float] = None,
    start_timeout: Optional[float] = None,
  ) -> None:
    """Backflush a pump: push its line's contents back towards the reagent.

    Args:
      pump: 1 or 2.
      volume: uL, a multiple of 500 from 500 to 4500.
      flow_rate: uL/s, one of `FLOW_RATE_CODES`.
      timeout: seconds the backflush may run; None uses `default_timeout`. A timeout leaves the
        reader pumping.
      start_timeout: seconds for the reader to report busy; None uses `default_start_timeout`.

    Raises:
      ValueError: if a setting is out of range or the pump is not fitted; nothing is sent.
      RuntimeError: if another operation is running; nothing is sent.
      PumpAbortedError: if the reader reported the backflush aborted.
      TimeoutError: if the reader does not start or end the backflush in time.
      CommandRefusedError: if a status reply has the refused bit set.
      FrameError: if status replies stay malformed after retries.
    """
    parameters = self._encode(pump, volume, flow_rate, backflush=True)
    await self._run(parameters, timeout, start_timeout, is_prime=False)
    logger.info("pump %d backflushed with %g uL at %g uL/s", pump, volume, flow_rate)
