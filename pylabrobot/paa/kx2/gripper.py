"""The KX2 servo gripper: absolute jaw motion and independent contact verification."""

import asyncio
import math
from typing import TYPE_CHECKING, Optional

from pylabrobot.io.canopen import finish_io

from .config import Axis
from .protocol import CanError

if TYPE_CHECKING:
  from .kx2 import KX2


class KX2Gripper:
  """The physical servo gripper, reached as ``kx2.gripper``."""

  def __init__(self, driver: "KX2") -> None:
    """Use the owning arm's transport, calibration, and motion lock."""
    self._driver = driver

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
    """Infer contact from current draw while stationary and away from the closed limit.

    This is motor-current evidence, not an independent plate-presence sensor.
    """
    fault = await self._driver.request_axis_fault(Axis.SERVO_GRIPPER)
    if fault is not None:
      raise CanError(fault)
    status = await self._driver._motor_get_motion_status(Axis.SERVO_GRIPPER)
    if status not in (0, 1) or await self.sense_fully_closed():
      return False
    return await self._driver._get_servo_gripper_force_fraction() >= 0.9

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

  async def _close_with_contact(self, width: float) -> None:
    """Stop a closing move after sustained current indicates contact above the jaw limit."""
    task = asyncio.create_task(self._driver._motors_move_joint_locked({Axis.SERVO_GRIPPER: width}))
    contact_samples = 0
    try:
      while not task.done():
        fault = await self._driver.request_axis_fault(Axis.SERVO_GRIPPER)
        if fault is not None:
          raise CanError(fault)
        at_limit = await self.sense_fully_closed()
        force = await self._driver._get_servo_gripper_force_fraction()
        contact_samples = contact_samples + 1 if force >= 0.9 and not at_limit else 0
        if contact_samples >= 5:
          break
        await asyncio.sleep(0.05)
      if task.done():
        await task
    finally:
      await finish_io(asyncio.create_task(self._driver._finish_search(task, [Axis.SERVO_GRIPPER])))

  async def move_to_jaw_position(
    self,
    width: float,
    force_sensing: bool = True,
    *,
    max_force_percent: Optional[int] = None,
    check_plate_gripped: bool = False,
  ) -> None:
    """Move the jaws in mm, stopping a force-sensed close on sustained motor current.

    ``max_force_percent`` is a percentage of motor current, not a force in newtons.
    Plate-presence confirmation is separate from limiting the motor current.
    """
    cfg = self._driver.configuration.axes[Axis.SERVO_GRIPPER]
    if not math.isfinite(width) or not cfg.min_travel <= width <= cfg.max_travel:
      raise ValueError("Jaw width is outside the calibrated travel")
    if max_force_percent is not None and not 10 <= max_force_percent <= 100:
      raise ValueError("max_force_percent must be in 10..100")
    if not force_sensing and (check_plate_gripped or max_force_percent is not None):
      raise ValueError("Current limiting and grip verification require force_sensing")
    async with self._driver._motion_guard():
      sg = Axis.SERVO_GRIPPER
      async with self._driver._preserve_settings(floats=[(sg, "PL", 1), (sg, "CL", 1)]):
        if force_sensing:
          await self._driver._set_servo_gripper_force_limit(
            100 if max_force_percent is None else max_force_percent
          )
        if force_sensing and width < await self.request_jaw_position():
          await self._close_with_contact(width)
        else:
          await self._driver._motors_move_joint_locked({sg: width})
        if check_plate_gripped:
          await self.check_plate_gripped()
