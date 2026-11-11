"""One pod: an arm on the shared X rail, its ACE module, and the axes that module drives."""

import logging
from typing import Dict, List, Literal, Optional, Sequence, Tuple, cast

from pylabrobot.beckman_coulter.biomek_i7 import units
from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import (
  ModuleConfiguration,
  PodConfiguration,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.module import (
  read_analog_inputs,
  read_module,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.motors import Axis, MotorTables
from pylabrobot.beckman_coulter.biomek_i7.driver.recipes.homing import ENABLE_ORDER, ENABLE_PREFIX

logger = logging.getLogger(__name__)

# How Biomek numbers its pods. Pod1 is the left pod and Pod2 the right on the instrument this
# driver was written against; whether that holds on every build is not established.
POD_NUMBERS = {"left": 1, "right": 2}
COMPONENT_PREFIXES = {"multichannel": "MC", "span8": "SPAN8"}

# Get Homed State answers. 0x01 was read from every axis at a cold start, 0x00 from every axis of
# both pods after Biomek's Home All (H1, 2026-10-24).
HOMED_STATES = {0x00: "homed", 0x01: "not_homed"}

# Biomek's "Safe Height" (`GlobalMinSafeHeight`) at or below this is unset (it stores -100000).
UNSET_SAFE_HEIGHT = -1000.0

# The arm board's analog input carrying the interlocked motor supply ("+36V Interlock").
DRIVE_SUPPLY_INPUT = "+36V Interlock"

# Set State ENABLED on a CiA 402 drive resets a fault, then steps the drive through shutdown, switch
# on and enable operation, waiting up to 3 s for each. Whether a board enables
# a batch one drive at a time is not known, so a batch is given that per axis; it is a bound for
# a drive that does not come up, not how long enabling takes.
ENABLE_SECONDS_PER_AXIS = 9.0


class Pod:
  """One pod. Reached as `driver.left_x_arm` / `driver.right_x_arm`: it fills the slot of STAR's
  `XArm`, though more of the instrument hangs on it. One board drives its X, Y and Z, its head
  or channels and its gripper.

  `module_type` is what the board reports itself to be (Get Module ID): `multichannel` (a
  96-channel head) or `span8` (eight independent channels). Its axes are the motor tables' for
  that type; what else is known of the pod (conversions, travel limits, geometry, settings) is the
  declared configuration's.
  """

  def __init__(
    self,
    board: AceBoard,
    side: Literal["left", "right"],
    module_type: str,
    motor_tables: MotorTables,
    declared: Optional[PodConfiguration] = None,
  ):
    self.board = board
    self.side = side
    self.module_type = module_type
    self.axes: List[Axis] = motor_tables.axes(module_type)
    self.configuration: Optional[ModuleConfiguration] = None
    # What the instrument's declared configuration says about this pod: its conversions, travel
    # limits and geometry. None when nothing is declared for this side.
    self.declared = declared
    # The height the channels rise to before traveling over the deck, mm of the channels' bottom
    # above the deck: STAR's `default_minimum_traverse_height`. Seeded from Biomek's "Safe Height"
    # (`GlobalMinSafeHeight`) when it is set; None, as on an instrument where it is not (-100000),
    # means the top of travel less the park margin, which is what Biomek does with Safe Height set
    # to the top.
    self.default_minimum_traverse_height: Optional[float] = None
    if declared is not None:
      safe = declared.settings.get("GlobalMinSafeHeight")
      if isinstance(safe, (int, float)) and safe > UNSET_SAFE_HEIGHT:
        self.default_minimum_traverse_height = units.to_mm(float(safe))

  @property
  def pod_number(self) -> int:
    return POD_NUMBERS[self.side]

  def __str__(self) -> str:
    return f"{self.side} pod (Pod{self.pod_number}, {self.module_type})"

  @property
  def component(self) -> str:
    """The name Biomek gives this pod's component in its logs (`host_command.component`):
    `MC_1`, `SPAN8_2`."""
    return f"{COMPONENT_PREFIXES[self.module_type]}_{self.pod_number}"

  def axis(self, name: str) -> Axis:
    for axis in self.axes:
      if axis.name == name:
        return axis
    raise ValueError(f"{self} has no axis {name!r}; it has {[a.name for a in self.axes]}")

  async def discover(self) -> ModuleConfiguration:
    """Read what the pod's module says about itself. Moves nothing."""
    self.configuration = await read_module(self.board)
    return self.configuration

  async def request_positions(self) -> Dict[str, int]:
    """Every axis's current position, in counts, as the drives report it."""
    replies = await self.board.send_many(
      [p.get_current_position(axis.device, axis.index) for axis in self.axes]
    )
    return {axis.name: p.parse_s32(r.payload) for axis, r in zip(self.axes, replies)}

  async def request_axis_states(self) -> Dict[str, p.AxisStateName]:
    """Every axis's state (motors and pumps), sent together: `enabled` (it may move), `disabled`
    (configured, not enabled), `not_configured` (as at power-up) or `fault`. Moves nothing.

    Raises:
      ValueError: for a state byte the firmware does not set.
    """
    replies = await self.board.send_many([p.get_state(a.device, a.index) for a in self.axes])
    return {
      a.name: cast(p.AxisStateName, p.parse_axis_state(r.payload).name.lower())
      for a, r in zip(self.axes, replies)
    }

  async def request_drive_supply(self) -> float:
    """The pod's interlocked motor supply, in volts: about 36 with the interlock armed, about 0.1
    tripped. Moves nothing.

    Raises:
      RuntimeError: if the pod has no input named for it.
    """
    if self.configuration is None:
      raise RuntimeError(f"{self} has not been read; call `discover()` or `setup()` first")
    for index, name in self.configuration.analog_inputs.items():
      if name.startswith(DRIVE_SUPPLY_INPUT):
        reply = await self.board.send(p.read_input_output(p.Device.ANALOG_INPUT, index))
        return p.parse_float(reply.payload)
    raise RuntimeError(f"{self} has no {DRIVE_SUPPLY_INPUT!r} analog input")

  async def request_homed_state(self, axis_name: str) -> str:
    """Whether an axis (a motor or a pump) has been homed: `homed`, `not_homed`, or the raw code
    of a state not yet seen."""
    axis = self.axis(axis_name)
    code = (await self.board.send(p.get_homed_state(axis.device, axis.index))).payload[0]
    return HOMED_STATES.get(code, f"0x{code:02X}")

  async def request_accuframe_offsets(self) -> Tuple[float, float, float]:
    """The X, Y and Z offsets the pod's AccuFrame board holds."""
    return p.parse_accuframe_offsets((await self.board.send(p.get_accuframe_offsets())).payload)

  async def request_analog_inputs(self) -> Dict[str, float]:
    if self.configuration is None:
      raise RuntimeError(f"{self} has not been read; call `discover()` or `setup()` first")
    return await read_analog_inputs(self.board, self.configuration)

  # -- configuration and enabling ------------------------------------------------
  # These write to the board. Nothing in this driver calls them on its own.

  async def configure(self) -> None:
    """Load every axis's motor table into the board, then Stop Module Execution, as after a
    power-up. The tables come from the motor recording; the board cannot be asked for them.

    Configuring an axis that is configured is accepted, and leaves it disabled and not homed, so
    this un-homes the pod."""
    logger.info("%s configure (%d axes)", self.component, len(self.axes))
    for axis in self.axes:
      await self._unchecked_fw_configure(axis)
    await self._unchecked_fw_stop_module_execution()

  async def enable_axes(self) -> Dict[str, int]:
    """Stop motion and enable every axis, as after the interlock is armed (its drive power was
    off), and read where each is, as the Master Controller resumes: each axis enabled and read.
    Returns every axis's position."""
    axes = [self.axis(name) for name in ENABLE_ORDER[self.module_type]]
    await self._unchecked_fw_enable(ENABLE_PREFIX[self.module_type])
    await self._unchecked_fw_stop_motion()
    await self._unchecked_fw_enable(ENABLE_ORDER[self.module_type])
    replies = await self.board.send_many([p.get_current_position(a.device, a.index) for a in axes])
    return {a.name: p.parse_s32(r.payload) for a, r in zip(axes, replies)}

  # -- drive commands ----------------------------------------------------------
  # One request, or one batch sent together, each, as the Master Controller sends them. No checks
  # and no bookkeeping: the public methods above decide when each is sent.

  async def _unchecked_fw_enable(self, axes: Sequence[str]) -> None:
    """Set State ENABLED on each axis, sent together. On a drive in fault this is also the fault
    reset; one that cannot come up answers an error and stays in fault."""
    if axes:
      targets = [self.axis(name) for name in axes]
      await self.board.send_many(
        [p.set_state_enabled(a.device, a.index) for a in targets],
        timeout=5.0 + ENABLE_SECONDS_PER_AXIS * len(targets),
      )

  async def _unchecked_fw_stop_motion(self) -> None:
    """Stop Motion Service on this pod's board: stops every axis and drops pending moves."""
    await self.board.send(p.stop_motion_service())

  async def _unchecked_fw_configure(self, axis: Axis) -> None:
    """Load one axis's motor table (Configure). Leaves it disabled and not homed."""
    await self.board.send(p.configure(axis.device, axis.index, axis.configure))

  async def _unchecked_fw_stop_module_execution(self) -> None:
    await self.board.send(p.stop_module_execution())
