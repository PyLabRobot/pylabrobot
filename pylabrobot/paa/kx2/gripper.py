"""The KX2 servo gripper: absolute jaw motion and independent contact verification."""

import asyncio
import math
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Optional

from pylabrobot.io.canopen import finish_io

from . import kinematics
from .config import Axis
from .protocol import CanError, MotorsMovePlan

if TYPE_CHECKING:
  from .kx2 import KX2, _Registers


class KX2Gripper:
  """The physical servo gripper, reached as ``kx2.gripper``."""

  def __init__(self, driver: "KX2") -> None:
    """Use the owning arm's transport, calibration, and motion lock."""
    self._driver = driver
    self._hold_settings: Optional["_Registers"] = None

  @property
  def jaw_width_range(self) -> tuple[float, float]:
    """Calibrated jaw travel in millimeters."""
    cfg = self._driver.configuration.axes[Axis.SERVO_GRIPPER]
    return cfg.min_travel, cfg.max_travel

  async def request_jaw_position(self) -> float:
    """Read the jaw width in millimeters."""
    return await self._driver.request_axis_position(Axis.SERVO_GRIPPER)

  async def sense_fully_closed(self) -> bool:
    """Whether the measured jaws are within 0.1 mm of their calibrated closed limit."""
    return abs(await self.request_jaw_position() - self.jaw_width_range[0]) <= 0.1

  async def sense_plate_gripped(self) -> bool:
    """Require plate detection, stationary current, and jaws away from both travel limits."""
    fault = await self._driver.request_axis_fault(Axis.SERVO_GRIPPER)
    if fault is not None:
      raise CanError(fault)
    if not await self._driver.request_axis_enabled(Axis.SERVO_GRIPPER):
      return False
    status = await self._driver._motor_get_motion_status(Axis.SERVO_GRIPPER)
    if status not in (0, 1) or not await self._driver.sense_proximity():
      return False
    position = await self.request_jaw_position()
    low, high = self.jaw_width_range
    if not low + 0.1 < position < high - 0.1:
      return False
    for _ in range(3):
      if await self._driver._get_servo_gripper_force_fraction() < 0.9:
        return False
      await asyncio.sleep(0.05)
      measured = await self.request_jaw_position()
      if abs(measured - position) > 0.02:
        return False
    return (
      await self._driver.request_axis_enabled(Axis.SERVO_GRIPPER)
      and await self._driver.request_axis_fault(Axis.SERVO_GRIPPER) is None
      and await self._driver.sense_proximity()
    )

  async def check_plate_gripped(self, num_attempts: int = 5) -> None:
    """Require measured contact, independently of reaching the requested width."""
    if num_attempts < 1:
      raise ValueError("num_attempts must be positive")
    for _ in range(num_attempts):
      if await self.sense_plate_gripped():
        return
      await asyncio.sleep(0.05)
    raise CanError("Gripper did not confirm a held plate")

  async def home(self) -> None:
    """Calibrate the jaws against the hard stop and encoder index."""
    await self._driver._home_gripper()

  async def _restore_current_limits(self) -> None:
    """Restore the limits saved before a grip, after release or when the session ends."""
    if self._hold_settings is not None:
      await self._driver._restore_registers(self._hold_settings)
      self._hold_settings = None

  async def _wait_for_contact(
    self, plan: MotorsMovePlan, width: float, start_width: float, already_gripped: bool
  ) -> bool:
    """Wait for settled contact or the requested position, keeping the closing target active."""
    deadline = time.monotonic() + plan.move_time + 2.0
    contact_samples = 0
    contact_position: Optional[float] = None
    await asyncio.sleep(0.05)
    while time.monotonic() < deadline:
      fault = await self._driver.request_axis_fault(Axis.SERVO_GRIPPER)
      if fault is not None:
        raise CanError(fault)
      if not await self._driver.request_axis_enabled(Axis.SERVO_GRIPPER):
        raise CanError("Gripper disabled during closing motion")
      status = await self._driver._motor_get_motion_status(Axis.SERVO_GRIPPER)
      position = await self.request_jaw_position()
      force = await self._driver._get_servo_gripper_force_fraction()
      at_limit = abs(position - self.jaw_width_range[0]) <= 0.1
      if force < 0.9 or at_limit or status not in (0, 1):
        contact_position, contact_samples = None, 0
      elif contact_position is None or abs(position - contact_position) > 0.02:
        contact_position, contact_samples = position, 1
      else:
        contact_samples += 1
      if contact_samples >= 5:
        if not already_gripped and start_width - position < 0.1:
          raise CanError(
            "Current limit reached before measurable jaw closure; check the limit and obstruction"
          )
        if not await self._driver.sense_proximity():
          raise CanError("Jaws encountered resistance but the plate sensor is clear")
        return True
      if status in (0, 1) and abs(position - width) <= 0.01 and (force < 0.9 or at_limit):
        return False
      await asyncio.sleep(0.05)
    raise CanError("Gripper did not reach its target or establish stationary contact")

  async def _wait_for_position(self, width: float, timeout: float) -> None:
    """Confirm settled encoder feedback, including profiler-done status with residual error."""
    deadline = time.monotonic() + timeout
    settled = 0
    position = await self.request_jaw_position()
    await asyncio.sleep(0.05)
    while time.monotonic() < deadline:
      fault = await self._driver.request_axis_fault(Axis.SERVO_GRIPPER)
      if fault is not None:
        raise CanError(fault)
      if not await self._driver.request_axis_enabled(Axis.SERVO_GRIPPER):
        raise CanError("Gripper disabled during jaw motion")
      status = await self._driver._motor_get_motion_status(Axis.SERVO_GRIPPER)
      position = await self.request_jaw_position()
      settled = settled + 1 if status in (0, 1) and abs(position - width) <= 0.01 else 0
      if settled >= 3:
        return
      await asyncio.sleep(0.05)
    raise CanError(f"Gripper did not reach {width:.3f} mm; measured {position:.3f} mm")

  async def _move_jaws(
    self,
    width: float,
    *,
    closing: bool,
    max_speed: Optional[float],
    max_acceleration: Optional[float],
  ) -> bool:
    """Run a jaw-only profile with scoped speed settings and optional contact detection."""
    sg = Axis.SERVO_GRIPPER
    cfg = self._driver.configuration
    axis = cfg.axes[sg]
    limits = replace(
      axis,
      max_vel=axis.max_vel if max_speed is None else min(axis.max_vel, max_speed),
      max_accel=axis.max_accel
      if max_acceleration is None
      else min(axis.max_accel, max_acceleration),
    )
    start_width = await self.request_jaw_position()
    already_gripped = await self.sense_plate_gripped() if closing else False
    plan = kinematics.plan_joint_move(
      current={sg: start_width},
      target={sg: width},
      cfg=replace(cfg, axes={**cfg.axes, sg: limits}),
      gripper_params=self._driver._gripper_params,
    )
    if plan is None:
      if closing:
        return await self.sense_plate_gripped()
      if self._hold_settings is not None:
        await self._driver._stop_axes([sg])
      return False
    registers = [(sg, index, 0) for index in (0x60F2, 0x6081, 0x6082, 0x6083, 0x6084, 0x6060)]
    async with self._driver._preserve_settings(sdo=registers):
      try:
        await self._driver._start_joint_plan(plan)
        if closing:
          return await self._wait_for_contact(plan, width, start_width, already_gripped)
        await self._wait_for_position(width, timeout=plan.move_time + 2)
        return False
      except BaseException:
        await finish_io(asyncio.create_task(self._driver._stop_axes([sg])))
        raise

  async def move_to_jaw_position(
    self,
    width: float,
    force_sensing: bool = True,
    *,
    max_force_percent: Optional[int] = None,
    check_plate_gripped: bool = False,
    max_speed: Optional[float] = None,
    max_acceleration: Optional[float] = None,
  ) -> None:
    """Move the jaws in mm, retaining the closing target and current limit while gripping.

    ``max_force_percent`` is a percentage of motor current, not a force in newtons.
    A confirmed grip holds that limit until the jaws open or the connection closes;
    release restores the exact limits saved before the grip. Speed (mm/s) and
    acceleration (mm/s²) apply only to the jaws and are restored after each move.
    """
    cfg = self._driver.configuration.axes[Axis.SERVO_GRIPPER]
    if not math.isfinite(width) or not cfg.min_travel <= width <= cfg.max_travel:
      raise ValueError("Jaw width is outside the calibrated travel")
    if max_force_percent is not None and not 10 <= max_force_percent <= 100:
      raise ValueError("max_force_percent must be in 10..100")
    for value in (max_speed, max_acceleration):
      if value is not None and (not math.isfinite(value) or value <= 0):
        raise ValueError("Jaw speed and acceleration must be finite and positive")
    if not force_sensing and (check_plate_gripped or max_force_percent is not None):
      raise ValueError("Current limiting and grip verification require force_sensing")
    async with self._driver._motion_guard():
      current = await self.request_jaw_position()
      closing = force_sensing and width < current
      opening = width > current
      holding = False
      try:
        if closing:
          if self._hold_settings is None:
            self._hold_settings = await self._driver._snapshot_registers(
              floats=[(Axis.SERVO_GRIPPER, "PL", 1), (Axis.SERVO_GRIPPER, "CL", 1)]
            )
          await self._driver._set_servo_gripper_force_limit(
            100 if max_force_percent is None else max_force_percent
          )
        if opening:
          # End the closing reference before increasing the current available for release.
          await self._driver._stop_axes([Axis.SERVO_GRIPPER])
          async with self._driver._preserve_settings(
            floats=[(Axis.SERVO_GRIPPER, "PL", 1), (Axis.SERVO_GRIPPER, "CL", 1)]
          ):
            await self._driver._set_servo_gripper_force_limit(100)
            await self._move_jaws(
              width, closing=False, max_speed=max_speed, max_acceleration=max_acceleration
            )
        else:
          holding = await self._move_jaws(
            width, closing=closing, max_speed=max_speed, max_acceleration=max_acceleration
          )
        if check_plate_gripped:
          await self.check_plate_gripped()
      finally:
        if not holding:
          await finish_io(asyncio.create_task(self._restore_current_limits()))
