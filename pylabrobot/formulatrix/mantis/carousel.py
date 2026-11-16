"""The Mantis LC3 chip carousel on its own FTDI command link."""

import asyncio
import logging
import math
import struct
from typing import Optional, Tuple

from pylabrobot.io.ftdi import FTDI

from .driver.carousel_configuration import CarouselConfiguration
from .driver.errors import (
  MOTOR_ERROR_CODE_DESCRIPTIONS,
  MOTOR_STATUS_ERROR_BITS,
  FrameError,
  MantisConfigurationError,
  MantisMotionError,
  MantisNotConnectedError,
  MantisTimeoutError,
)
from .driver.events import HOME_DONE_EVENT, MOVE_DONE_EVENT, decode_home_done, decode_move_done
from .driver.master import MantisDriver

logger = logging.getLogger(__name__)

# The controller's position readback uses native motor units.
_POSITION_TOLERANCE = 0.01
_MOVING_OR_HOMING = 3
_HOMED = 4
_FAULT_BITS = {8: "lower limit", 16: "upper limit", **MOTOR_STATUS_ERROR_BITS}


def _is_carousel_home(payload: bytes) -> bool:
  """Whether a home event belongs to the carousel's sole motor."""
  return decode_home_done(payload)[0] == 0


def _is_carousel_move(payload: bytes) -> bool:
  """Whether a move event belongs to the carousel's sole motor."""
  return decode_move_done(payload)[0] == 0


class MantisCarousel:
  """Home and position an LC3 carousel independently of the Mantis arm.

  Setup opens the LC3 link without moving. Home before selecting an angle or a one-based chip
  slot. Keep the Mantis head clear of the carousel; this class does not coordinate arm movement.
  """

  def __init__(
    self,
    *,
    configuration: CarouselConfiguration,
    io: Optional[FTDI] = None,
    ftdi_library: Optional[str] = None,
  ):
    """Use this unit's carousel calibration and interface B of its USB product ``LC3``.

    Args:
      configuration: serial-matched geometry and motion settings, from the OEM database or
        explicitly supplied calibration.
      io: an already-built FTDI link, instead of constructing one for the configured serial.
      ftdi_library: "libftdi", "d2xx", or None for the transport's platform default.
    """
    self.configuration = configuration
    link = (
      io
      if io is not None
      else FTDI(
        human_readable_device_name="Formulatrix Mantis LC3 carousel",
        usb_product="LC3",
        device_id=configuration.device_id,
        vid=0x0403,
        pid=0x6010,
        interface_select=2,
        ftdi_library=ftdi_library,
      )
    )
    # Use the Mantis command transport without its arm, pressure-board or RFID discovery.
    self._driver = MantisDriver(io=link, name=f"LC3 {configuration.device_id}")
    self._operation_lock = asyncio.Lock()
    self._connected = False
    self._limits: Optional[Tuple[float, float]] = None
    self.firmware_version: Optional[str] = None

  def _require_connected(self) -> None:
    """Refuse operations without a completed setup, including during shutdown."""
    if not self._connected:
      raise MantisNotConnectedError("carousel is not set up; call setup() first")

  def _to_device(self, value: float) -> float:
    """Convert an angle or angular rate to the controller's position units."""
    return value * self.configuration.steps_per_revolution / 360.0

  def _check_status(self, status: int, *, require_homed: bool = False) -> None:
    """Reject motor faults and, for positioning, a missing home reference."""
    faults = [name for bit, name in _FAULT_BITS.items() if status & bit]
    if faults:
      raise MantisMotionError(f"carousel reports {', '.join(faults)}", motor=0, status=status)
    if require_homed and not status & _HOMED:
      raise MantisMotionError("carousel is not homed; call home() first", motor=0, status=status)

  async def request_status(self) -> int:
    """The motor status word: bit 0 moving, bit 1 homing, bit 2 homed; higher bits are faults."""
    self._require_connected()
    return await self._driver.request_motor_status(motor=0)

  async def request_position(self) -> float:
    """The controller's position in degrees, normalized to [0, 360); requires homing."""
    self._check_status(await self.request_status(), require_homed=True)
    position = await self._driver.request_motor_position(motor=0)
    if not math.isfinite(position):
      raise FrameError("carousel returned a non-finite position")
    return position / self.configuration.steps_per_revolution * 360.0 % 360.0

  async def _send_motion(self, *, command: int, payload: bytes) -> None:
    """Send a motor command once and validate its uint16 result code."""
    reply = await self._driver._send_command(board=0, command=command, payload=payload, attempts=1)
    if len(reply) != 2:
      raise FrameError(f"carousel command {command} result must be 2 bytes, got {reply.hex()}")
    (code,) = struct.unpack("<H", reply)
    if code:
      meaning = MOTOR_ERROR_CODE_DESCRIPTIONS.get(code, "unknown motor error")
      raise MantisMotionError(f"carousel: {meaning} ({code})", motor=0, code=code)

  async def _wait_idle(self, *, timeout: float) -> None:
    """Wait until motion stops; a fault may remain for the caller to inspect."""
    deadline = asyncio.get_running_loop().time() + timeout
    while await self._driver.request_motor_status(motor=0) & _MOVING_OR_HOMING:
      if asyncio.get_running_loop().time() >= deadline:
        raise MantisTimeoutError("carousel did not stop")
      await asyncio.sleep(0.05)

  async def _stop_motor(self) -> None:
    """Send stop once and wait for the motor to stop, even when its previous command failed."""
    await self._send_motion(
      command=24,
      payload=struct.pack("<hd", 0, self._to_device(self.configuration.default_acceleration)),
    )
    await self._wait_idle(timeout=5.0)

  async def _stop_after_failure(self) -> None:
    """Attempt to halt motion while preserving the operation's original exception."""
    if not self._connected:
      return  # Shutdown owns stopping the motor and closing the link.
    try:
      await self._stop_motor()
    except Exception:
      logger.warning("[%s] could not stop carousel", self.configuration.device_id, exc_info=True)

  async def _execute_motion(
    self, *, command: int, payload: bytes, homing: bool, timeout: float
  ) -> None:
    """Register completion before sending, then require an idle, homed motor without faults."""
    self._require_connected()
    waiter = self._driver._expect_event(
      board=0,
      code=HOME_DONE_EVENT if homing else MOVE_DONE_EVENT,
      match=_is_carousel_home if homing else _is_carousel_move,
    )
    try:
      await self._send_motion(command=command, payload=payload)
      await self._driver._wait_for_event(waiter=waiter, timeout=timeout)
      status = await self.request_status()
      self._check_status(status, require_homed=True)
      if status & _MOVING_OR_HOMING:
        raise MantisMotionError("carousel reported completion while moving", motor=0, status=status)
    finally:
      if waiter.done() and not waiter.cancelled():
        waiter.exception()
      waiter.cancel()
      self._driver._event_waiters = [
        entry for entry in self._driver._event_waiters if entry[3] is not waiter
      ]

  async def home(self, *, timeout: float = 45.0) -> None:
    """Establish the carousel's flag reference; do nothing if already homed without faults.

    Faults are cleared before homing. Failure or cancellation stops the motor.
    """
    if not math.isfinite(timeout) or timeout <= 0:
      raise ValueError("timeout must be finite and positive")
    async with self._operation_lock:
      status = await self.request_status()
      if status & _MOVING_OR_HOMING:
        raise MantisMotionError("carousel is already moving", motor=0, status=status)
      if status & _HOMED and not any(status & bit for bit in _FAULT_BITS):
        return
      try:
        await self._driver._send_command(board=0, command=17, payload=struct.pack("<h", 0))
        config = self.configuration
        await self._execute_motion(
          command=21,
          payload=struct.pack(
            "<hhhhddd",
            0,
            0,
            1,
            0,
            self._to_device(config.homing_slow_speed),
            self._to_device(config.homing_fast_speed),
            self._to_device(config.homing_acceleration),
          ),
          homing=True,
          timeout=timeout,
        )
      except BaseException:
        await self._stop_after_failure()
        raise
      logger.info("[%s] carousel homed", self.configuration.device_id)

  def _plan_rotation(self, *, position: float, current: float) -> Tuple[float, ...]:
    """Choose the nearest equivalent angle whose target and backlash approach fit the limits."""
    assert self._limits is not None
    lower, upper = self._limits
    revolution = self.configuration.steps_per_revolution
    base = self._to_device(position % 360.0)
    first, last = math.ceil((lower - base) / revolution), math.floor((upper - base) / revolution)
    if first > last:
      raise MantisMotionError("no equivalent carousel position lies inside its travel limits")
    nearest = min(last, max(first, math.floor((current - base) / revolution + 0.5)))
    target = base + nearest * revolution
    if abs(target - current) <= _POSITION_TOLERANCE:
      return ()
    preposition = self._to_device(self.configuration.preposition)
    if target < current and preposition:
      if target - preposition < lower:
        target += revolution
        if target > upper:
          raise MantisMotionError("carousel backlash approach would exceed its travel limits")
      if target < current:
        return target - preposition, target
    return (target,)

  async def move_to(
    self,
    position: float,
    *,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    timeout: float = 30.0,
  ) -> None:
    """Rotate to an absolute angle in degrees by the nearest permitted route.

    A decreasing move approaches from the unit's configured preposition below the target.
    Repeating the same angle sends no motion. Rates are degrees/s and degrees/s²; omitted rates
    use the unit's configuration. Failure or cancellation stops the motor.
    """
    speed = self.configuration.default_speed if speed is None else speed
    acceleration = self.configuration.default_acceleration if acceleration is None else acceleration
    if not math.isfinite(position):
      raise ValueError("position must be finite")
    if any(not math.isfinite(value) or value <= 0 for value in (speed, acceleration, timeout)):
      raise ValueError("speed, acceleration and timeout must be finite and positive")
    async with self._operation_lock:
      status = await self.request_status()
      self._check_status(status, require_homed=True)
      if status & _MOVING_OR_HOMING:
        raise MantisMotionError("carousel is already moving", motor=0, status=status)
      current = await self._driver.request_motor_position(motor=0)
      if not math.isfinite(current):
        raise FrameError("carousel returned a non-finite position")
      targets = self._plan_rotation(position=position, current=current)
      try:
        for target in targets:
          await self._execute_motion(
            command=22,
            payload=struct.pack(
              "<hddd", 0, target, self._to_device(speed), self._to_device(acceleration)
            ),
            homing=False,
            timeout=timeout,
          )
          actual = await self._driver.request_motor_position(motor=0)
          if not math.isfinite(actual) or abs(actual - target) > _POSITION_TOLERANCE:
            raise MantisMotionError(f"carousel stopped at {actual}, expected {target}", motor=0)
      except BaseException:
        await self._stop_after_failure()
        raise
      logger.info("[%s] carousel at %.3f degrees", self.configuration.device_id, position % 360.0)

  async def move_to_slot(
    self, slot: int, *, speed: Optional[float] = None, acceleration: Optional[float] = None
  ) -> None:
    """Select a one-based chip slot using this carousel's calibrated slot-one angle."""
    if type(slot) is not int or not 1 <= slot <= self.configuration.capacity:
      raise ValueError(f"slot must be an integer from 1 to {self.configuration.capacity}")
    position = (
      self.configuration.slot_one_position + 360.0 * (slot - 1) / self.configuration.capacity
    )
    await self.move_to(position, speed=speed, acceleration=acceleration)

  async def stop(self) -> None:
    """Stop any motion and close the link, leaving the carousel at its current angle."""
    connected, self._connected = self._connected, False
    try:
      if connected and await self._driver.request_motor_status(motor=0) & _MOVING_OR_HOMING:
        await self._stop_motor()
    finally:
      await self._driver.stop()
      self._limits = None
    logger.info("[%s] carousel disconnected", self.configuration.device_id)

  async def setup(self) -> None:
    """Open only this LC3's command link and read firmware and limits, without motion."""
    if self._connected:
      return
    try:
      await self._driver._open()
      self.firmware_version = await self._driver.request_firmware_version()
      lower, upper = await self._driver.request_motor_limits(motor=0)
      if not all(math.isfinite(value) for value in (lower, upper)) or lower >= upper:
        raise MantisConfigurationError("carousel returned invalid travel limits")
      self._limits = lower, upper
      self._connected = True
    except BaseException:
      await self._driver.stop()
      raise
    logger.info(
      "[%s] carousel connected, firmware %s", self.configuration.device_id, self.firmware_version
    )
