"""A PreciseFlex arm that answers without being connected.

Each feature has a small subclass here that overrides the methods which would otherwise talk to the
controller, returning what one would have said. `PreciseFlexSimulationDriver` swaps those in, so
everything above them - discovery, the declared configuration's check, the guards, the model - runs
as it does against an arm.

Nothing reaches the wire. `send_command` raises, which is how a command that has not been simulated
makes itself known: override the method that sends it, on the feature that owns it.
"""

import logging
from typing import Any, List, Optional

import anyio

from pylabrobot.brooks.precise_flex.driver.configuration import (
  PreciseFlexConfiguration,
  read_configuration,
)
from pylabrobot.brooks.precise_flex.driver.errors import PreciseFlexError
from pylabrobot.brooks.precise_flex.driver.features.arm import PreciseFlexArm
from pylabrobot.brooks.precise_flex.driver.features.gripper import PreciseFlexGripper
from pylabrobot.brooks.precise_flex.driver.master import PreciseFlexDriver
from pylabrobot.brooks.precise_flex.kinematics import JointState, PreciseFlexAxis
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.events import evented_operation
from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)


class _SimulatedArm(PreciseFlexArm):
  """An arm that holds its joint state: a joint read returns it, a joint move sets it."""

  _driver: "PreciseFlexSimulationDriver"

  def __init__(self, driver: "PreciseFlexSimulationDriver") -> None:
    """Bind the simulated arm feature to its simulation driver."""
    super().__init__(driver)
    # Set at discovery, once the soft limits and the parking pose are known.
    self.simulated_joint_state: JointState = {}
    self.simulated_speed_percent = 100.0

  async def _wait_for_eom(self, *args: Any, **kwargs: Any) -> None:
    """Return immediately because simulated motion settles synchronously."""
    del args, kwargs

  async def request_joint_state(self) -> JointState:
    """Return the held joint state and synchronize the arm and gripper models."""
    self._driver._require_link()
    joints = dict(self.simulated_joint_state)
    self.update_joint_state(joints)
    gripper = self._driver.gripper
    gripper.update_width(gripper._firmware_units_to_mm(joints[PreciseFlexAxis.GRIPPER]))
    return joints

  async def _unchecked_fw_move_j(self, profile_index: int, joint_coords: JointState) -> None:
    """Update the simulated joint state for an unguarded joint-space move."""
    del profile_index
    self._driver._require_link()
    self._driver._reject_motion_during_freedrive_scope()
    self.simulated_joint_state.update(joint_coords)

  async def _unchecked_fw_move_one_axis(self, axis: PreciseFlexAxis, position: float) -> None:
    """Update one simulated axis for an unguarded single-axis move."""
    self._driver._require_link()
    self._driver._reject_motion_during_freedrive_scope()
    self.simulated_joint_state[axis] = position

  async def _set_speed(self, speed_percent: float) -> None:
    """Record the simulated profile speed percentage."""
    self._driver._require_link()
    self.simulated_speed_percent = speed_percent

  async def _request_speed(self) -> float:
    """Return the simulated profile speed percentage."""
    self._driver._require_link()
    return self.simulated_speed_percent

  async def _stop_freedrive_command(self) -> None:
    """Exit simulated freedrive without wire traffic."""
    await anyio.lowlevel.checkpoint()
    self._driver._require_link()
    self._driver._freedrive_active = False

  @evented_operation(
    "precise_flex.start_freedrive",
    lambda self, free_axes=None: {
      "device": self._driver._controller_reference(),
      "free_axes": [int(axis) for axis in free_axes] if free_axes is not None else None,
    },
  )
  async def _start_freedrive_transition(self, free_axes: Optional[List[int]] = None) -> None:
    """Enter simulated freedrive without wire traffic while the caller holds the transition lock."""
    await anyio.lowlevel.checkpoint()
    self._driver._require_link()
    if free_axes is None:
      axes_to_free: List[int] = [
        PreciseFlexAxis.BASE,
        PreciseFlexAxis.SHOULDER,
        PreciseFlexAxis.ELBOW,
        PreciseFlexAxis.WRIST,
      ]
      if self._driver.rail is not None:
        axes_to_free.append(PreciseFlexAxis.RAIL)
    else:
      axes_to_free = list(free_axes)
    if PreciseFlexAxis.RAIL in axes_to_free and self._driver.rail is None:
      raise PreciseFlexError(-2800, "Rail axis is not configured.")
    if axes_to_free:
      self._driver._freedrive_active = True

  @evented_operation(
    "precise_flex.halt",
    lambda self: {"device": self._driver._controller_reference()},
  )
  async def halt(self) -> None:
    """Acknowledge a halt on a simulated arm."""
    await anyio.lowlevel.checkpoint()
    self._driver._require_link()


class _SimulatedGripper(PreciseFlexGripper):
  """A gripper whose jaws stand wherever they were last sent, in the arm's held joint state."""

  _driver: "PreciseFlexSimulationDriver"

  def __init__(
    self, driver: "PreciseFlexSimulationDriver", arm: _SimulatedArm, **kwargs: Any
  ) -> None:
    """Bind the simulated gripper feature to its simulation driver and arm."""
    super().__init__(driver, **kwargs)
    self._arm = arm

  async def _unchecked_fw_move_jaws(self, units: float, force_sensing: bool) -> None:
    """Set the simulated gripper axis in firmware units."""
    del force_sensing
    self._driver._require_link()
    self._driver._reject_motion_during_freedrive_scope()
    self._arm.simulated_joint_state[PreciseFlexAxis.GRIPPER] = units


class PreciseFlexSimulationDriver(PreciseFlexDriver):
  """A simulated PreciseFlex arm, driven exactly like the real one."""

  def __init__(
    self,
    declared_configuration_json: str,
    base_plate: Optional[Resource] = None,
  ) -> None:
    """Configure a simulated PreciseFlex driver from a declared configuration JSON file.

    Args:
      declared_configuration_json: path to a declared configuration, which this arm then answers
        as. Its kinematics give the tool length and offset.
      base_plate: the base plate to build the arm on, as the real driver takes it.

    Raises:
      ValueError: If the file declares no device configuration.
    """
    configuration = read_configuration(declared_configuration_json).get("device")
    if configuration is None:
      raise ValueError(
        "a simulated arm has to be told what it is simulating: pass "
        "`declared_configuration_json`, naming a file that holds one"
      )
    # Firmware units equal millimetres: the narrow end of the jaws is their first unit.
    closed = PreciseFlexGripper.jaw_width_range[0]
    super().__init__(
      host="simulation",
      gripper_length=configuration.arm.kinematics.gripper_length,
      gripper_z_offset=configuration.arm.kinematics.gripper_z_offset,
      closed_gripper_position=closed,
      base_plate=base_plate,
      declared_configuration_json=declared_configuration_json,
    )
    self.simulated_configuration: PreciseFlexConfiguration = configuration
    self._simulated_arm = _SimulatedArm(self)
    self.arm = self._simulated_arm
    # Unset, as the real constructor leaves it: discovery fills the default.
    self.arm.parking_position = None
    self.gripper = _SimulatedGripper(
      self,
      self._simulated_arm,
      closed_gripper_position=closed,
      is_dual_gripper=configuration.gripper.is_dual_gripper,
    )
    self._link_open = False
    self._homed = False

  def _require_link(self) -> None:
    """Raise if the simulated controller session is not open."""
    if not self._link_open:
      raise RuntimeError("PreciseFlex simulation link is not open")

  def _close_link(self) -> None:
    """Mark the simulated controller session closed."""
    self._link_open = False
    logger.debug("[PreciseFlex simulation] disconnected")

  async def send_command(self, command: str, **kwargs: Any) -> str:
    """Raise ``NotImplementedError`` naming any firmware command without a simulation override."""
    del kwargs
    self._require_link()
    raise NotImplementedError(
      f"a simulated arm sends nothing, and {command.split()[0]!r} is not simulated: override the "
      "method that sends it, on the feature that owns it"
    )

  async def _enter_link(self, stack: AsyncExitStackWithShielding) -> None:
    """Open the simulated controller session on ``stack`` without network traffic."""
    self._link_open = True
    stack.callback(self._close_link)
    logger.debug("[PreciseFlex simulation] connected")

  @evented_operation(
    "precise_flex.power_on",
    lambda self: {"device": self._controller_reference()},
  )
  async def power_on_robot(self) -> None:
    """Enable simulated high power."""
    self._require_link()

  @evented_operation(
    "precise_flex.power_off",
    lambda self: {"device": self._controller_reference()},
  )
  async def power_off_robot(self) -> None:
    """Drop simulated high power, clearing incremental homing state."""
    self._require_link()
    self._homed = False

  async def attach(self, attach_state: Optional[int] = None) -> int:
    """Simulate controller attachment queries and state changes."""
    self._require_link()
    return 1 if attach_state is None else 0

  async def _unchecked_fw_home(self) -> None:
    """Home the simulated arm and open the jaws to their soft limit."""
    self._require_link()
    self._reject_motion_during_freedrive_scope()
    self._simulated_arm.simulated_joint_state[PreciseFlexAxis.GRIPPER] = (
      self.simulated_configuration.gripper.soft_limit_range[1]
    )
    self._homed = True

  async def _is_robot_homed(self) -> bool:
    """Return whether the simulated arm is currently homed."""
    self._require_link()
    return self._homed

  async def _request_configuration(self) -> PreciseFlexConfiguration:
    """Return the declared device configuration without wire reads."""
    self._require_link()
    return self.simulated_configuration

  async def discover(self) -> PreciseFlexConfiguration:
    """Adopt the declared configuration; the first time, stand the arm parked with its jaws open."""
    configuration = await super().discover()
    arm = self._simulated_arm
    if not arm.simulated_joint_state:
      parked = arm._parking_pose_with_default_z(arm.parking_position or {})
      arm.simulated_joint_state = {
        **dict.fromkeys([PreciseFlexAxis.RAIL, *PreciseFlexArm._WIRE_ORDER], 0.0),
        **parked,
        PreciseFlexAxis.GRIPPER: configuration.gripper.soft_limit_range[1],
      }
    return configuration
