"""OrientableGripperArmBackend for the Tecan EVO RoMa (Robotic Manipulator Arm).

Translates v1b1 arm operations into Tecan RoMa firmware commands. The backend
is itself an :class:`EVOArm`, so it owns its firmware command vocabulary
directly (no separate wrapper) and shares the cross-arm collision cache.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from pylabrobot.capabilities.arms.backend import OrientableGripperArmBackend
from pylabrobot.capabilities.arms.standard import CartesianPose
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.rotation import Rotation

from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.errors import TecanError
from pylabrobot.tecan.evo.firmware.arm_base import EVOArm

logger = logging.getLogger(__name__)

ROMA = "C1"


class EVORoMaBackend(OrientableGripperArmBackend, EVOArm):
  """OrientableGripperArmBackend for the Tecan EVO RoMa plate handling arm.

  The RoMa is an X/Y/Z + rotation (R) plate handler, modelled like the
  Hamilton iSWAP. Positions are derived geometrically from the deck
  coordinate handed in by the capability layer (the same convention the LiHa
  pip backend uses), not from per-carrier taught coordinates.

  Only the 90-degree grip orientation is hardware-validated (every captured
  firmware sequence drove R=900). Requests for other angles raise
  NotImplementedError until they are validated on hardware.
  """

  # Only this grip orientation (degrees) has been validated on hardware.
  VALIDATED_DIRECTION = 90.0

  def __init__(
    self,
    driver: TecanEVODriver,
    deck: Resource,
    park_position: tuple = (9000, 2000, 2464, 1800),
  ):
    EVOArm.__init__(self, driver, ROMA)
    self._deck = deck
    self._z_roma_traversal_height = 68.7  # mm
    # Approach clearance above the grip height (safe hover). Geometric value;
    # needs hardware validation against real plate/carrier heights.
    self._z_roma_safe_clearance = 20.0  # mm
    self._park_position = park_position

  @dataclass(frozen=True)
  class TecanRoMaParams(BackendParams):
    """EVO-specific parameters for RoMa operations.

    Attributes:
      speed_x: X-axis fast speed in 1/10 mm/s.
      speed_y: Y-axis fast speed in 1/10 mm/s.
      speed_z: Z-axis fast speed in 1/10 mm/s.
      speed_r: R-axis fast speed in 1/10 deg/s.
      accel_y: Y-axis acceleration in 1/10 mm/s^2.
      accel_r: R-axis acceleration in 1/10 deg/s^2.
    """

    speed_x: Optional[int] = None
    speed_y: Optional[int] = None
    speed_z: Optional[int] = None
    speed_r: Optional[int] = None
    accel_y: Optional[int] = None
    accel_r: Optional[int] = None

  # ============== RoMa firmware commands ==============

  async def report_z_param(self, param: int) -> int:
    """Report current parameter for z-axis.

    Args:
      param: 0=current position, 5=actual machine range
    """
    resp: List[int] = (
      await self.driver.send_command(module=self.module, command="RPZ", params=[param])
    )["data"]
    return resp[0]

  async def report_r_param(self, param: int) -> int:
    """Report current parameter for r-axis (rotation).

    Args:
      param: 0=current position, 5=actual machine range
    """
    resp: List[int] = (
      await self.driver.send_command(module=self.module, command="RPR", params=[param])
    )["data"]
    return resp[0]

  async def report_g_param(self, param: int) -> int:
    """Report current parameter for g-axis (gripper).

    Args:
      param: 0=current position, 5=actual machine range
    """
    resp: List[int] = (
      await self.driver.send_command(module=self.module, command="RPG", params=[param])
    )["data"]
    return resp[0]

  async def set_smooth_move_x(self, mode: int) -> None:
    """Set X-axis smooth move mode.

    Args:
      mode: 0=active (recalculate accel/speed by distance), 1=use SFX parameters directly
    """
    await self.driver.send_command(module=self.module, command="SSM", params=[mode])

  async def set_fast_speed_x(self, speed: Optional[int], accel: Optional[int] = None) -> None:
    """Set fast speed and acceleration for X-axis.

    Args:
      speed: 1/10 mm/s
      accel: 1/10 mm/s^2
    """
    await self.driver.send_command(module=self.module, command="SFX", params=[speed, accel])

  async def set_fast_speed_y(self, speed: Optional[int], accel: Optional[int] = None) -> None:
    """Set fast speed and acceleration for Y-axis.

    Args:
      speed: 1/10 mm/s
      accel: 1/10 mm/s^2
    """
    await self.driver.send_command(module=self.module, command="SFY", params=[speed, accel])

  async def set_fast_speed_z(self, speed: Optional[int], accel: Optional[int] = None) -> None:
    """Set fast speed and acceleration for Z-axis.

    Args:
      speed: 1/10 mm/s
      accel: 1/10 mm/s^2
    """
    await self.driver.send_command(module=self.module, command="SFZ", params=[speed, accel])

  async def set_fast_speed_r(self, speed: Optional[int], accel: Optional[int] = None) -> None:
    """Set fast speed and acceleration for R-axis (rotation).

    Args:
      speed: 1/10 deg/s
      accel: 1/10 deg/s^2
    """
    await self.driver.send_command(module=self.module, command="SFR", params=[speed, accel])

  async def set_vector_coordinate_position(
    self,
    v: int,
    x: int,
    y: int,
    z: int,
    r: int,
    g: Optional[int],
    speed: int,
    tw: int = 0,
  ) -> None:
    """Set vector coordinate positions into table.

    Args:
      v: vector index (1-100)
      x: absolute x in 1/10 mm
      y: absolute y in 1/10 mm
      z: absolute z in 1/10 mm
      r: absolute r in 1/10 deg
      g: absolute gripper in 1/10 mm (optional)
      speed: 0=slow, 1=fast
      tw: target window class (set with STW)

    Raises:
      TecanError: if movement would cause collision with another arm
    """
    cur_x = EVOArm._pos_cache.setdefault(self.module, await self.report_x_param(0))
    for module, pos in EVOArm._pos_cache.items():
      if module == self.module:
        continue
      if cur_x < x and cur_x < pos < x:
        raise TecanError("Invalid command (collision)", self.module, 2)
      if cur_x > x and cur_x > pos > x:
        raise TecanError("Invalid command (collision)", self.module, 2)
      if abs(pos - x) < 1500:
        raise TecanError("Invalid command (collision)", self.module, 2)

    await self.driver.send_command(
      module=self.module,
      command="SAA",
      params=[v, x, y, z, r, g, speed, 0, tw],
    )

  async def action_move_vector_coordinate_position(self) -> None:
    """Start coordinate movement built by the vector table."""
    await self.driver.send_command(module=self.module, command="AAC")
    EVOArm._pos_cache[self.module] = await self.report_x_param(0)

  async def position_absolute_g(self, g: int) -> None:
    """Move gripper to absolute position.

    Args:
      g: absolute position in 1/10 mm
    """
    await self.driver.send_command(module=self.module, command="PAG", params=[g])

  async def set_gripper_params(self, speed: int, pwm: int, cur: Optional[int] = None) -> None:
    """Set gripper parameters.

    Args:
      speed: search speed in 1/10 mm/s
      pwm: pulse width modification limit
      cur: max current (optional)
    """
    await self.driver.send_command(module=self.module, command="SGG", params=[speed, pwm, cur])

  async def grip_plate(self, pos: int) -> None:
    """Grip plate at current X/Y/Z/R position.

    Args:
      pos: target position — plate must be found between current and target
    """
    await self.driver.send_command(module=self.module, command="AGR", params=[pos])

  async def set_target_window_class(self, wc: int, x: int, y: int, z: int, r: int, g: int) -> None:
    """Set drive parameters for AAC command.

    Args:
      wc: window class (1-100)
      x: target window for x-axis in 1/10 mm
      y: target window for y-axis in 1/10 mm
      z: target window for z-axis in 1/10 mm
      r: target window for r-axis in 1/10 deg
      g: target window for g-axis in 1/10 mm
    """
    await self.driver.send_command(module=self.module, command="STW", params=[wc, x, y, z, r, g])

  # ============== Setup ==============

  def _get_speeds(self, backend_params: Optional[BackendParams]) -> Dict[str, int]:
    """Get speed settings, applying overrides from backend_params."""
    defaults = {
      "x": 10000,
      "y": 5000,
      "z": 1300,
      "r": 5000,
      "accel_y": 1500,
      "accel_r": 1500,
    }
    if isinstance(backend_params, EVORoMaBackend.TecanRoMaParams):
      for key, attr in [
        ("x", "speed_x"),
        ("y", "speed_y"),
        ("z", "speed_z"),
        ("r", "speed_r"),
        ("accel_y", "accel_y"),
        ("accel_r", "accel_r"),
      ]:
        val = getattr(backend_params, attr, None)
        if val is not None:
          defaults[key] = val
    return defaults

  async def _on_setup(self, backend_params: Optional[BackendParams] = None) -> None:
    """Initialize RoMa arm. Skips PIA if already initialized."""
    # Check if RoMa is present and already initialized
    try:
      roma_err = await self.read_error_register()
    except TecanError as e:
      if e.error_code == 5:
        logger.info("RoMa not present (error 5).")
        return
      roma_err = ""

    if roma_err and all(c == "@" for c in roma_err):
      logger.info("RoMa already initialized (REE=%s), skipping PIA.", roma_err)
      return

    # Full init: PIA + park
    logger.info("RoMa needs initialization, running PIA...")
    try:
      await self.position_init_all()
    except TecanError as e:
      if e.error_code == 5:
        logger.info("RoMa not present (error 5).")
        return
      raise
    await self.set_bus_mode(2)

    await self.position_initialization_x()
    await self.park()
    logger.info("RoMa initialized and parked.")

  async def _on_stop(self) -> None:
    pass

  def _direction_to_r(self, direction: float) -> int:
    """Convert a grip direction (degrees) to the RoMa R-axis value (1/10 deg).

    Only :attr:`VALIDATED_DIRECTION` is supported on hardware; any other angle
    raises until it has been validated.
    """
    if direction != self.VALIDATED_DIRECTION:
      raise NotImplementedError(
        f"RoMa grip orientation {direction} deg is not hardware-validated; only "
        f"{self.VALIDATED_DIRECTION} deg is supported. Pass direction=90 (or 'back')."
      )
    return int(direction * 10)

  def _roma_positions(
    self,
    location: Coordinate,
    z_range: int,
  ) -> Tuple[int, int, Dict[str, int]]:
    """Compute RoMa X, Y, Z (1/10 mm) from a deck coordinate.

    ``location`` is the plate centre in the deck frame, as supplied by the
    capability layer. X/Y use the same deck->Tecan conversion as the LiHa pip
    backend; Z is derived from ``location.z`` (RoMa Z is measured downward from
    the top, hence ``z_range - z``).
    """
    x_position = int((location.x - 100) * 10)
    y_position = int((347.1 - location.y) * 10)
    z_end = z_range - int(location.z * 10)
    z_positions = {
      "safe": z_end - int(self._z_roma_safe_clearance * 10),
      "travel": int(self._z_roma_traversal_height * 10),
      "end": z_end,
    }
    return x_position, y_position, z_positions

  # ============== OrientableGripperArmBackend implementation ==============

  async def pick_up_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up a plate at a deck coordinate.

    Args:
      location: Plate centre in the deck frame.
      direction: Grip orientation in degrees (only the validated angle works).
      resource_width: Jaw width target in mm (plate dimension across the jaws).
    """
    r = self._direction_to_r(direction)
    z_range = await self.report_z_param(5)
    x, y, z = self._roma_positions(location, z_range)

    # Move above the plate
    speeds = self._get_speeds(backend_params)
    await self.set_smooth_move_x(1)
    await self.set_fast_speed_x(speeds["x"])
    await self.set_fast_speed_y(speeds["y"], speeds["accel_y"])
    await self.set_fast_speed_z(speeds["z"])
    await self.set_fast_speed_r(speeds["r"], speeds["accel_r"])
    await self.set_vector_coordinate_position(1, x, y, z["safe"], r, None, 1, 0)
    await self.action_move_vector_coordinate_position()
    await self.set_smooth_move_x(0)

    # Descend and grip
    await self.position_absolute_g(900)
    await self.set_target_window_class(1, 0, 0, 0, 135, 0)
    await self.set_vector_coordinate_position(1, x, y, z["travel"], r, None, 1, 1)
    await self.set_vector_coordinate_position(1, x, y, z["end"], r, None, 1, 0)
    await self.action_move_vector_coordinate_position()
    await self.set_fast_speed_y(3500, 1000)
    await self.set_fast_speed_r(2000, 600)
    await self.set_gripper_params(100, 75)
    await self.grip_plate(int(resource_width * 10) - 100)

    # Verify plate was gripped
    g_pos = await self.report_g_param(0)
    if g_pos >= 900:
      logger.warning("Plate may not be gripped (G-axis position: %d)", g_pos)

  async def drop_at_location(
    self,
    location: Coordinate,
    direction: float,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Place the held plate at a deck coordinate and release.

    Args:
      location: Plate centre in the deck frame.
      direction: Grip orientation in degrees (only the validated angle works).
      resource_width: Held-plate jaw width in mm (unused; release opens fully).
    """
    r = self._direction_to_r(direction)
    z_range = await self.report_z_param(5)
    xt, yt, zt = self._roma_positions(location, z_range)

    # Approach the destination from travel height, then descend to place
    await self.set_target_window_class(1, 0, 0, 0, 135, 0)
    await self.set_target_window_class(2, 0, 0, 0, 53, 0)
    await self.set_target_window_class(3, 0, 0, 0, 55, 0)
    await self.set_vector_coordinate_position(1, xt, yt, zt["travel"], r, None, 1, 1)
    await self.set_vector_coordinate_position(2, xt, yt, zt["safe"], r, None, 1, 2)
    await self.set_vector_coordinate_position(3, xt, yt, zt["end"], r, None, 1, 0)
    await self.action_move_vector_coordinate_position()

    # Release and retract
    await self.position_absolute_g(900)
    await self.set_fast_speed_y(5000, 1500)
    await self.set_fast_speed_r(5000, 1500)
    await self.set_vector_coordinate_position(1, xt, yt, zt["end"], r, None, 1, 1)
    await self.set_vector_coordinate_position(2, xt, yt, zt["safe"], r, None, 1, 2)
    await self.set_vector_coordinate_position(3, xt, yt, zt["travel"], r, None, 1, 0)
    await self.action_move_vector_coordinate_position()
    await self.set_fast_speed_y(3500, 1000)
    await self.set_fast_speed_r(2000, 600)

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    r = self._direction_to_r(direction)
    z_range = await self.report_z_param(5)
    x = int((location.x - 100) * 10)
    y = int((347.1 - location.y) * 10)
    z_safe = z_range - 946  # default safe Z
    await self.set_vector_coordinate_position(1, x, y, z_safe, r, None, 1, 0)
    await self.action_move_vector_coordinate_position()

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    await self.bus_module_action(0, 0, 0)

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    px, py, pz, pr = self._park_position
    await self.set_vector_coordinate_position(1, px, py, pz, pr, None, 1, 0)
    await self.action_move_vector_coordinate_position()

  # Jaw-width bounds are hardware-specific and not documented for the RoMa.
  # Declaring them None leaves move_gripper unvalidated (any width passes
  # through) and makes the capability-level open_gripper/close_gripper
  # convenience raise NotImplementedError until the range is measured.
  @property
  def min_gripper_width(self) -> Optional[float]:
    return None

  @property
  def max_gripper_width(self) -> Optional[float]:
    return None

  async def move_gripper(
    self,
    width: float,
    force_sensing: bool = False,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if force_sensing:
      # Close with force feedback, stopping on plate contact.
      await self.set_gripper_params(100, 75)
      await self.grip_plate(int(width * 10))
    else:
      # Drive the jaws to the target width without sensing.
      await self.position_absolute_g(int(width * 10))

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    pos = await self.report_g_param(0)
    return pos < 100  # heuristic: < 10mm = closed

  async def request_gripper_pose(
    self, backend_params: Optional[BackendParams] = None
  ) -> CartesianPose:
    x = await self.report_x_param(0)
    y = (await self.report_y_param(0))[0]
    z = await self.report_z_param(0)
    r = await self.report_r_param(0)
    return CartesianPose(
      location=Coordinate(x=x / 10.0, y=y / 10.0, z=z / 10.0),
      rotation=Rotation(x=0, y=0, z=r / 10.0),
    )
