"""OrientableGripperArmBackend for the Tecan EVO RoMa (Robotic Manipulator Arm).

Translates v1b1 arm operations into Tecan RoMa firmware commands via the
TecanEVODriver and RoMa firmware wrapper.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

from pylabrobot.capabilities.arms.backend import OrientableGripperArmBackend
from pylabrobot.capabilities.arms.standard import CartesianPose
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.rotation import Rotation

from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.errors import TecanError
from pylabrobot.tecan.evo.firmware import RoMa
from pylabrobot.tecan.evo.firmware.arm_base import EVOArm
from pylabrobot.tecan.evo.params import TecanRoMaParams

logger = logging.getLogger(__name__)

ROMA = "C1"


class EVORoMaBackend(OrientableGripperArmBackend):
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
    self._driver = driver
    self._deck = deck
    self._z_roma_traversal_height = 68.7  # mm
    # Approach clearance above the grip height (safe hover). Geometric value;
    # needs hardware validation against real plate/carrier heights.
    self._z_roma_safe_clearance = 20.0  # mm
    self._park_position = park_position
    self.roma: Optional[RoMa] = None

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
    if isinstance(backend_params, TecanRoMaParams):
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

  async def _on_setup(self) -> None:
    """Initialize RoMa arm. Skips PIA if already initialized."""

    arm = EVOArm(self._driver, ROMA)

    # Check if RoMa is present and already initialized
    try:
      roma_err = await arm.read_error_register()
    except TecanError as e:
      if e.error_code == 5:
        logger.info("RoMa not present (error 5).")
        return
      roma_err = ""

    if roma_err and all(c == "@" for c in roma_err):
      # Already initialized — skip PIA, just set up firmware wrapper
      logger.info("RoMa already initialized (REE=%s), skipping PIA.", roma_err)
      self.roma = RoMa(self._driver, ROMA)
      return

    # Full init: PIA + park
    logger.info("RoMa needs initialization, running PIA...")
    try:
      await arm.position_init_all()
    except TecanError as e:
      if e.error_code == 5:
        logger.info("RoMa not present (error 5).")
        return
      raise
    await arm.set_bus_mode(2)

    self.roma = RoMa(self._driver, ROMA)
    await self.roma.position_initialization_x()
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
    assert self.roma is not None

    r = self._direction_to_r(direction)
    z_range = await self.roma.report_z_param(5)
    x, y, z = self._roma_positions(location, z_range)

    # Move above the plate
    speeds = self._get_speeds(backend_params)
    await self.roma.set_smooth_move_x(1)
    await self.roma.set_fast_speed_x(speeds["x"])
    await self.roma.set_fast_speed_y(speeds["y"], speeds["accel_y"])
    await self.roma.set_fast_speed_z(speeds["z"])
    await self.roma.set_fast_speed_r(speeds["r"], speeds["accel_r"])
    await self.roma.set_vector_coordinate_position(1, x, y, z["safe"], r, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()
    await self.roma.set_smooth_move_x(0)

    # Descend and grip
    await self.roma.position_absolute_g(900)
    await self.roma.set_target_window_class(1, 0, 0, 0, 135, 0)
    await self.roma.set_vector_coordinate_position(1, x, y, z["travel"], r, None, 1, 1)
    await self.roma.set_vector_coordinate_position(1, x, y, z["end"], r, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()
    await self.roma.set_fast_speed_y(3500, 1000)
    await self.roma.set_fast_speed_r(2000, 600)
    await self.roma.set_gripper_params(100, 75)
    await self.roma.grip_plate(int(resource_width * 10) - 100)

    # Verify plate was gripped
    g_pos = await self.roma.report_g_param(0)
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
    assert self.roma is not None

    r = self._direction_to_r(direction)
    z_range = await self.roma.report_z_param(5)
    xt, yt, zt = self._roma_positions(location, z_range)

    # Approach the destination from travel height, then descend to place
    await self.roma.set_target_window_class(1, 0, 0, 0, 135, 0)
    await self.roma.set_target_window_class(2, 0, 0, 0, 53, 0)
    await self.roma.set_target_window_class(3, 0, 0, 0, 55, 0)
    await self.roma.set_vector_coordinate_position(1, xt, yt, zt["travel"], r, None, 1, 1)
    await self.roma.set_vector_coordinate_position(2, xt, yt, zt["safe"], r, None, 1, 2)
    await self.roma.set_vector_coordinate_position(3, xt, yt, zt["end"], r, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()

    # Release and retract
    await self.roma.position_absolute_g(900)
    await self.roma.set_fast_speed_y(5000, 1500)
    await self.roma.set_fast_speed_r(5000, 1500)
    await self.roma.set_vector_coordinate_position(1, xt, yt, zt["end"], r, None, 1, 1)
    await self.roma.set_vector_coordinate_position(2, xt, yt, zt["safe"], r, None, 1, 2)
    await self.roma.set_vector_coordinate_position(3, xt, yt, zt["travel"], r, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()
    await self.roma.set_fast_speed_y(3500, 1000)
    await self.roma.set_fast_speed_r(2000, 600)

  async def move_to_location(
    self,
    location: Coordinate,
    direction: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    assert self.roma is not None
    r = self._direction_to_r(direction)
    z_range = await self.roma.report_z_param(5)
    x = int((location.x - 100) * 10)
    y = int((347.1 - location.y) * 10)
    z_safe = z_range - 946  # default safe Z
    await self.roma.set_vector_coordinate_position(1, x, y, z_safe, r, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    assert self.roma is not None
    await self.roma.bus_module_action(0, 0, 0)

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    assert self.roma is not None
    px, py, pz, pr = self._park_position
    await self.roma.set_vector_coordinate_position(1, px, py, pz, pr, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()

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
    assert self.roma is not None
    if force_sensing:
      # Close with force feedback, stopping on plate contact.
      await self.roma.set_gripper_params(100, 75)
      await self.roma.grip_plate(int(width * 10))
    else:
      # Drive the jaws to the target width without sensing.
      await self.roma.position_absolute_g(int(width * 10))

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    assert self.roma is not None
    pos = await self.roma.report_g_param(0)
    return pos < 100  # heuristic: < 10mm = closed

  async def request_gripper_pose(
    self, backend_params: Optional[BackendParams] = None
  ) -> CartesianPose:
    assert self.roma is not None
    x = await self.roma.report_x_param(0)
    y = (await self.roma.report_y_param(0))[0]
    z = await self.roma.report_z_param(0)
    r = await self.roma.report_r_param(0)
    return CartesianPose(
      location=Coordinate(x=x / 10.0, y=y / 10.0, z=z / 10.0),
      rotation=Rotation(x=0, y=0, z=r / 10.0),
    )
