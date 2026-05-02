"""Hamilton Nimbus CoRe gripper backend and NimbusGripperArm frontend.

CoRe gripper commands live on ``NimbusCORE.Pipette`` (cmd 9-14 and 17-18).
Units: positions/widths in 0.01mm (INT32/UINT32 wire), speeds in 0.01mm/s (UINT32),
xAcceleration is a scale factor 1-100 (UINT32).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from pylabrobot.capabilities.arms.arm import GripperArm
from pylabrobot.capabilities.arms.backend import GripperArmBackend
from pylabrobot.capabilities.arms.standard import GripperLocation
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Coordinate, Resource

from .commands import (
  DropGripperTool,
  DropPlate,
  IsCoreGripperPlateGripped,
  IsCoreGripperToolHeld,
  MovePlate,
  PickupGripperTool,
  PickupPlate,
  ReleasePlate,
)

if TYPE_CHECKING:
  from .driver import NimbusDriver
  from .pip_backend import NimbusPIPBackend


def _mm(v: float) -> int:
  """Convert mm to 0.01mm wire units."""
  return round(v * 100)


def _mms(v: float) -> int:
  """Convert mm/s to 0.01mm/s wire units."""
  return round(v * 100)


class NimbusCoreGripperFactory:
  """Lightweight factory: :class:`Nimbus` constructs one at setup and calls
  :meth:`build_backend` when tools are picked up."""

  def __init__(self, driver: "NimbusDriver") -> None:
    self._driver = driver

  def build_backend(self, pip: "NimbusPIPBackend") -> "NimbusCoreGripper":
    return NimbusCoreGripper(driver=self._driver, pip=pip)


class NimbusCoreGripper(GripperArmBackend):
  """CoRe gripper backend for Nimbus.

  Translates the v1 GripperArmBackend interface to NimbusCORE.Pipette firmware
  commands (PickupPlate/DropPlate/MovePlate/ReleasePlate).

  Tool management (``pick_up_tool`` / ``drop_tool``) is handled by the
  :meth:`Nimbus.core_grippers` context manager, not the GripperArmBackend interface.
  """

  @dataclass
  class PickUpParams(BackendParams):
    """Firmware parameters for plate pickup.

    Auto-populated from resource geometry by :class:`NimbusGripperArm.pick_up_resource`.
    """

    y_plate_width: float = 85.48
    y_open_position: float = 100.0
    y_grip_speed: float = 5.0
    y_grip_strength: float = 0.5
    z_grip_height: float = 0.0
    z_final: float = 146.0
    z_speed: float = 50.0

  @dataclass
  class DropParams(BackendParams):
    """Firmware parameters for plate drop."""

    y_open_position: float = 100.0
    x_acceleration: int = 10
    z_drop_height: float = 0.0
    z_press_distance: float = 0.0
    z_final: float = 146.0
    z_speed: float = 50.0

  @dataclass
  class MoveToLocationParams(BackendParams):
    """Firmware parameters for moving a held plate."""

    x_acceleration: int = 10
    z_final: float = 146.0
    z_speed: float = 50.0

  @dataclass
  class PickUpToolParams(BackendParams):
    """Firmware parameters for picking up the CoRe gripper tool."""

    traverse_height: float = 146.0
    z_start_offset: float = 10.0
    z_stop_position: float = 0.0
    tip_type: int = 0
    tool_width: float = 9.0

  @dataclass
  class DropToolParams(BackendParams):
    """Firmware parameters for dropping the CoRe gripper tool."""

    traverse_height: float = 146.0
    z_final: float = 146.0

  def __init__(self, *, driver: "NimbusDriver", pip: "NimbusPIPBackend") -> None:
    self._driver = driver
    self._pip = pip

  @property
  def client(self):
    return self._driver

  # -- GripperArmBackend interface -----------------------------------------------

  async def pick_up_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, NimbusCoreGripper.PickUpParams):
      backend_params = NimbusCoreGripper.PickUpParams()
    p = backend_params
    await self._driver.send_command(
      PickupPlate(
        x_position=_mm(location.x),
        y_plate_center_position=_mm(location.y),
        y_plate_width=_mm(resource_width),
        y_open_position=_mm(p.y_open_position),
        y_grip_speed=_mms(p.y_grip_speed),
        y_grip_strength=_mm(p.y_grip_strength),
        traverse_height=_mm(p.z_final),
        z_grip_height=_mm(location.z),
        z_final=_mm(p.z_final),
        z_speed=_mms(p.z_speed),
      )
    )

  async def drop_at_location(
    self,
    location: Coordinate,
    resource_width: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, NimbusCoreGripper.DropParams):
      backend_params = NimbusCoreGripper.DropParams()
    p = backend_params
    await self._driver.send_command(
      DropPlate(
        x_position=_mm(location.x),
        x_acceleration=p.x_acceleration,
        y_plate_center_position=_mm(location.y),
        y_open_position=_mm(p.y_open_position),
        traverse_height=_mm(p.z_final),
        z_drop_height=_mm(location.z),
        z_press_distance=_mm(p.z_press_distance),
        z_final=_mm(p.z_final),
        z_speed=_mms(p.z_speed),
      )
    )

  async def move_to_location(
    self,
    location: Coordinate,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not isinstance(backend_params, NimbusCoreGripper.MoveToLocationParams):
      backend_params = NimbusCoreGripper.MoveToLocationParams()
    p = backend_params
    await self._driver.send_command(
      MovePlate(
        x_position=_mm(location.x),
        x_acceleration=p.x_acceleration,
        y_plate_center_position=_mm(location.y),
        traverse_height=_mm(p.z_final),
        z_final=_mm(location.z),
        z_speed=_mms(p.z_speed),
      )
    )

  async def open_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    """Release plate / open CoRe gripper (ReleasePlate, cmd=14)."""
    num_ch = self._pip.num_channels
    await self._driver.send_command(
      ReleasePlate(
        first_channel_number=1,
        second_channel_number=num_ch,
      )
    )

  async def close_gripper(
    self, gripper_width: float, backend_params: Optional[BackendParams] = None
  ) -> None:
    raise NotImplementedError("Use pick_up_at_location instead.")

  async def is_gripper_closed(self, backend_params: Optional[BackendParams] = None) -> bool:
    raise NotImplementedError("NimbusCoreGripper does not support is_gripper_closed")

  async def halt(self, backend_params: Optional[BackendParams] = None) -> None:
    raise NotImplementedError("NimbusCoreGripper does not support halt")

  async def park(self, backend_params: Optional[BackendParams] = None) -> None:
    raise NotImplementedError(
      "Tool management is handled by Nimbus.core_grippers() context manager."
    )

  async def request_gripper_location(
    self, backend_params: Optional[BackendParams] = None
  ) -> GripperLocation:
    raise NotImplementedError("NimbusCoreGripper does not support request_gripper_location")

  # -- Status queries ------------------------------------------------------------

  async def is_tool_held(self) -> tuple[bool, list[int]]:
    """Query whether a CoRe gripper tool is held (IsCoreGripperToolHeld, cmd=17)."""
    resp = await self._driver.send_command(IsCoreGripperToolHeld())
    assert resp is not None
    return bool(resp.gripped), list(resp.tip_type)

  async def is_plate_gripped(self) -> bool:
    """Query whether a plate is currently gripped (IsCoreGripperPlateGripped, cmd=18)."""
    resp = await self._driver.send_command(IsCoreGripperPlateGripped())
    assert resp is not None
    return bool(resp.gripped)

  # -- Tool management (used by Nimbus.core_grippers context manager) ------------

  async def pick_up_tool(
    self,
    x: float,
    y_ch1: float,
    y_ch2: float,
    *,
    channel1: int = 1,
    channel2: int = 8,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Pick up CoRe gripper tool (PickupGripperTool, cmd=9)."""
    if not isinstance(backend_params, NimbusCoreGripper.PickUpToolParams):
      backend_params = NimbusCoreGripper.PickUpToolParams()
    p = backend_params
    await self._driver.send_command(
      PickupGripperTool(
        x_position=_mm(x),
        y_position_1st_channel=_mm(y_ch1),
        y_position_2nd_channel=_mm(y_ch2),
        traverse_height=_mm(p.traverse_height),
        z_start_position=_mm(p.traverse_height - p.z_start_offset),
        z_stop_position=_mm(p.z_stop_position),
        tip_type=p.tip_type,
        first_channel_number=channel1,
        second_channel_number=channel2,
        tool_width=_mm(p.tool_width),
      )
    )

  async def drop_tool(
    self,
    x: float,
    y_ch1: float,
    y_ch2: float,
    *,
    channel1: int = 1,
    channel2: int = 8,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Drop CoRe gripper tool (DropGripperTool, cmd=10)."""
    if not isinstance(backend_params, NimbusCoreGripper.DropToolParams):
      backend_params = NimbusCoreGripper.DropToolParams()
    p = backend_params
    await self._driver.send_command(
      DropGripperTool(
        x_position=_mm(x),
        y_position_1st_channel=_mm(y_ch1),
        y_position_2nd_channel=_mm(y_ch2),
        traverse_height=_mm(p.traverse_height),
        z_start_position=_mm(p.traverse_height),
        z_stop_position=_mm(0.0),
        z_final=_mm(p.z_final),
        first_channel_number=channel1,
        second_channel_number=channel2,
      )
    )


class NimbusGripperArm(GripperArm):
  """GripperArm that auto-populates Nimbus firmware geometry from the target resource.

  When ``pick_up_resource()`` is called, the plate width (Y-axis for Nimbus) is
  extracted from the :class:`Resource` automatically.
  """

  async def pick_up_resource(
    self,
    resource: Resource,
    offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_bottom: Optional[float] = None,
    backend_params: Optional[BackendParams] = None,
  ):
    if not isinstance(backend_params, NimbusCoreGripper.PickUpParams):
      backend_params = NimbusCoreGripper.PickUpParams()

    backend_params.y_plate_width = resource.get_absolute_size_y()

    pdfb = self._resolve_pickup_distance(resource, pickup_distance_from_bottom)
    backend_params.z_grip_height = pdfb

    await super().pick_up_resource(resource, offset, pickup_distance_from_bottom, backend_params)
