"""GripperArmBackend for the Tecan EVO RoMa (Robotic Manipulator Arm).

Translates v1b1 arm operations into Tecan RoMa firmware commands via the
TecanEVODriver and RoMa firmware wrapper.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

from pylabrobot.arms.backend import GripperArmBackend
from pylabrobot.arms.standard import GripperLocation
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate, Resource, TecanPlateCarrier
from pylabrobot.resources.rotation import Rotation

from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.errors import TecanError
from pylabrobot.tecan.evo.firmware import RoMa

logger = logging.getLogger(__name__)

ROMA = "C1"


class EVORoMaBackend(GripperArmBackend):
  """GripperArmBackend for the Tecan EVO RoMa plate handling arm.

  The RoMa grips plates along the Y-axis at a fixed 900 (90-degree) R-axis
  orientation. It uses vector coordinate tables for multi-point trajectories
  with target window classes for smooth acceleration profiles.
  """

  def __init__(self, driver: TecanEVODriver, deck: Resource):
    self._driver = driver
    self._deck = deck
    self._z_roma_traversal_height = 68.7  # mm
    self.roma: Optional[RoMa] = None

  async def _on_setup(self) -> None:
    """Initialize RoMa arm. Skips PIA if already initialized."""

    # Check if RoMa is present and already initialized
    try:
      resp = await self._driver.send_command(ROMA, command="REE")
      roma_err = resp["data"][0] if resp and resp.get("data") else ""
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
      await self._driver.send_command(ROMA, command="PIA")
    except TecanError as e:
      if e.error_code == 5:
        logger.info("RoMa not present (error 5).")
        return
      raise
    await self._driver.send_command(ROMA, command="BMX", params=[2])

    self.roma = RoMa(self._driver, ROMA)
    await self.roma.position_initialization_x()
    await self.park()
    logger.info("RoMa initialized and parked.")

  async def _on_stop(self) -> None:
    pass

  def _roma_positions(
    self,
    resource: Resource,
    offset: Coordinate,
    z_range: int,
  ) -> Tuple[int, int, Dict[str, int]]:
    """Compute RoMa X, Y, Z positions from resource and carrier attributes."""
    parent = resource.parent  # PlateHolder
    if parent is None:
      raise ValueError(f"Operation is not supported by resource {resource}.")
    parent = parent.parent  # PlateCarrier
    if not isinstance(parent, TecanPlateCarrier):
      raise ValueError(f"Operation is not supported by resource {parent}.")

    if parent.roma_x is None or parent.roma_y is None:
      raise ValueError(f"RoMa coordinates not defined for carrier {parent}.")
    if parent.roma_z_safe is None or parent.roma_z_end is None:
      raise ValueError(f"RoMa Z positions not defined for carrier {parent}.")

    x_position = int((offset.x - 100) * 10 + parent.roma_x)
    y_position = int((347.1 - (offset.y + resource.get_absolute_size_y())) * 10 + parent.roma_y)
    z_positions = {
      "safe": z_range - int(parent.roma_z_safe),
      "travel": int(self._z_roma_traversal_height * 10),
      "end": z_range - int(parent.roma_z_end - offset.z * 10),
    }
    return x_position, y_position, z_positions

  # ============== GripperArmBackend implementation ==============

  async def pick_up_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up a plate at the given location.

    Note: The current implementation uses the legacy carrier-attribute-based
    positioning rather than the raw Coordinate. The ``location`` is used to
    find the resource and its parent carrier for RoMa coordinate computation.
    """
    assert self.roma is not None
    # For now, this method is called from the Device level which provides
    # the absolute location. The RoMa needs carrier-specific attributes,
    # so the Device.pick_up_resource method should call _pick_up_from_carrier
    # with the full resource reference.
    raise NotImplementedError(
      "Use TecanEVO.pick_up_resource() which provides carrier context. "
      "Direct pick_up_at_location with raw coordinates is not yet supported."
    )

  async def drop_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    assert self.roma is not None
    raise NotImplementedError(
      "Use TecanEVO.drop_resource() which provides carrier context. "
      "Direct drop_at_location with raw coordinates is not yet supported."
    )

  async def pick_up_from_carrier(
    self,
    resource: Resource,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up a plate using carrier-based RoMa coordinates."""
    assert self.roma is not None

    z_range = await self.roma.report_z_param(5)
    offset = resource.get_location_wrt(self._deck)
    x, y, z = self._roma_positions(resource, offset, z_range)
    h = int(resource.get_absolute_size_y() * 10)

    # Move to resource
    await self.roma.set_smooth_move_x(1)
    await self.roma.set_fast_speed_x(10000)
    await self.roma.set_fast_speed_y(5000, 1500)
    await self.roma.set_fast_speed_z(1300)
    await self.roma.set_fast_speed_r(5000, 1500)
    await self.roma.set_vector_coordinate_position(1, x, y, z["safe"], 900, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()
    await self.roma.set_smooth_move_x(0)

    # Pick up
    await self.roma.position_absolute_g(900)
    await self.roma.set_target_window_class(1, 0, 0, 0, 135, 0)
    await self.roma.set_vector_coordinate_position(1, x, y, z["travel"], 900, None, 1, 1)
    await self.roma.set_vector_coordinate_position(1, x, y, z["end"], 900, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()
    await self.roma.set_fast_speed_y(3500, 1000)
    await self.roma.set_fast_speed_r(2000, 600)
    await self.roma.set_gripper_params(100, 75)
    await self.roma.grip_plate(h - 100)

  async def drop_at_carrier(
    self,
    resource: Resource,
    destination: Coordinate,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop a plate at a carrier location using RoMa coordinates."""
    assert self.roma is not None

    z_range = await self.roma.report_z_param(5)
    offset = resource.get_location_wrt(self._deck)
    x, y, z = self._roma_positions(resource, offset, z_range)
    xt, yt, zt = self._roma_positions(resource, destination, z_range)

    # Multi-point trajectory to target
    await self.roma.set_target_window_class(1, 0, 0, 0, 135, 0)
    await self.roma.set_target_window_class(2, 0, 0, 0, 53, 0)
    await self.roma.set_target_window_class(3, 0, 0, 0, 55, 0)
    await self.roma.set_target_window_class(4, 45, 0, 0, 0, 0)
    await self.roma.set_vector_coordinate_position(1, x, y, z["end"], 900, None, 1, 1)
    await self.roma.set_vector_coordinate_position(2, x, y, z["travel"], 900, None, 1, 2)
    await self.roma.set_vector_coordinate_position(3, x, y, z["safe"], 900, None, 1, 3)
    await self.roma.set_vector_coordinate_position(4, xt, yt, zt["safe"], 900, None, 1, 4)
    await self.roma.set_vector_coordinate_position(5, xt, yt, zt["travel"], 900, None, 1, 3)
    await self.roma.set_vector_coordinate_position(6, xt, yt, zt["end"], 900, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()

    # Release
    await self.roma.position_absolute_g(900)
    await self.roma.set_fast_speed_y(5000, 1500)
    await self.roma.set_fast_speed_r(5000, 1500)
    await self.roma.set_vector_coordinate_position(1, xt, yt, zt["end"], 900, None, 1, 1)
    await self.roma.set_vector_coordinate_position(2, xt, yt, zt["travel"], 900, None, 1, 2)
    await self.roma.set_vector_coordinate_position(3, xt, yt, zt["safe"], 900, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()
    await self.roma.set_fast_speed_y(3500, 1000)
    await self.roma.set_fast_speed_r(2000, 600)

  async def move_to_location(
    self, location: Coordinate, backend_params: Optional[BackendParams] = None
  ) -> None:
    assert self.roma is not None
    z_range = await self.roma.report_z_param(5)
    x = int((location.x - 100) * 10)
    y = int((347.1 - location.y) * 10)
    z_safe = z_range - 946  # default safe Z
    await self.roma.set_vector_coordinate_position(1, x, y, z_safe, 900, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    await self._driver.send_command(ROMA, command="BMA", params=[0, 0, 0])

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    assert self.roma is not None
    await self.roma.set_vector_coordinate_position(1, 9000, 2000, 2464, 1800, None, 1, 0)
    await self.roma.action_move_vector_coordinate_position()

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    assert self.roma is not None
    await self.roma.position_absolute_g(int(gripper_width * 10))

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    assert self.roma is not None
    await self.roma.set_gripper_params(100, 75)
    await self.roma.grip_plate(int(gripper_width * 10))

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    assert self.roma is not None
    pos = await self.roma.report_g_param(0)
    return pos < 100  # heuristic: < 10mm = closed

  async def get_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> GripperLocation:
    assert self.roma is not None
    x = await self.roma.report_x_param(0)
    y = (await self.roma.report_y_param(0))[0]
    z = await self.roma.report_z_param(0)
    r = await self.roma.report_r_param(0)
    return GripperLocation(
      location=Coordinate(x=x / 10.0, y=y / 10.0, z=z / 10.0),
      rotation=Rotation(x=0, y=0, z=r / 10.0),
    )
