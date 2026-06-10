import abc
import datetime
import math
from dataclasses import dataclass, field
from typing import Any

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.hamilton.STAR_backend import (
  ExtendedConfiguration,
  Head96Information,
  MachineConfiguration,
  STARBackend,
  iSWAPInformation,
)
from pylabrobot.liquid_handling.backends.hamilton.STAR_chatterbox import (
  _DEFAULT_EXTENDED_CONFIGURATION,
  _DEFAULT_ISWAP_INFORMATION,
  _DEFAULT_MACHINE_CONFIGURATION,
)
from pylabrobot.resources import Coordinate
from pylabrobot.testing import mock_io


@dataclass(kw_only=True, frozen=True)
class DriveConfig:
  speed: float
  acceleration: float


_Y_DRIVE_STEP = 0.046302083  # mm per hardware drive step
_Z_DRIVE_STEP = 0.01072765  # mm per hardware drive step
_FW_STEP = 0.1  # mm per firmware unit (0.1 mm)
_FW_VOL_STEP = 0.1  # uL per firmware volume unit (0.1 uL)
_FW_ACCEL_SCALE = 1000.0  # scale factor for acceleration parameters (1000 increments/s^2)


@dataclass(kw_only=True, frozen=True)
class SimulatorConfig:
  # We couldn't find documentation about X
  x_drive: DriveConfig = field(default_factory=lambda: DriveConfig(speed=500.0, acceleration=200.0))
  # Y drive has a step resolution of 0.046 mm/step, 6000 st/s default max speed and 20000 st/s^2 acceleration
  pip_y_drive: DriveConfig = field(
    default_factory=lambda: DriveConfig(
      speed=6000 * _Y_DRIVE_STEP, acceleration=20000 * _Y_DRIVE_STEP
    )
  )
  # Z drive has a step resolution of 0.01 mm/step, 12000 st/s default max speed and 75000 st/s^2 acceleration
  pip_z_drive: DriveConfig = field(
    default_factory=lambda: DriveConfig(
      speed=12000 * _Z_DRIVE_STEP, acceleration=75000 * _Z_DRIVE_STEP
    )
  )
  head96_y_drive: DriveConfig = field(
    default_factory=lambda: DriveConfig(speed=300.0, acceleration=300.0)
  )
  head96_z_drive: DriveConfig = field(
    default_factory=lambda: DriveConfig(speed=80.0, acceleration=300.0)
  )
  iswap_y_drive: DriveConfig = field(
    default_factory=lambda: DriveConfig(speed=220.0, acceleration=1000.0)
  )
  iswap_z_drive: DriveConfig = field(
    default_factory=lambda: DriveConfig(speed=118.0, acceleration=643.66)
  )
  iswap_rotation_drive: DriveConfig = field(
    default_factory=lambda: DriveConfig(speed=75.0, acceleration=500.0)
  )
  iswap_wrist_drive: DriveConfig = field(
    default_factory=lambda: DriveConfig(speed=100.0, acceleration=725.0)
  )
  z_safety_height: float = 360.0
  min_channel_separation: float = 9.0
  min_op_duration: float = 0.5
  tip_op_duration: float = 1.0
  iswap_gripper_op_duration: float = 0.5
  module_init_time: float = 5.0
  head96_x_offset: float = -250.0


@dataclass(kw_only=True)
class ToolPosition:
  current_y: float = 250.0
  current_z: float = 300.0


@dataclass(kw_only=True)
class PipettePosition(ToolPosition):
  dispensing_drive_position: float = 0.0  # in uL
  has_tip: bool = False


@dataclass(kw_only=True)
class ISwapState(ToolPosition):
  # rotation (W), wrist (T) in degrees, gripper (G) in mm
  current_w: float = 90.0  # 0: towards front, positive turns ccw as seen from above
  current_t: float = -135.0  # -45: straight, positive turns ccw as seen from above
  current_g: float = 90.0
  holding_resource: bool = False


@dataclass(kw_only=True, frozen=True)
class _ChannelOpBase(abc.ABC):
  target_z: float

  @abc.abstractmethod
  def get_duration(self, config: SimulatorConfig) -> float:
    pass


@dataclass(kw_only=True, frozen=True)
class _AspirateDispenseOp(_ChannelOpBase):
  volume: float
  speed: float
  mix_cycles: int = 0
  mix_volume: float = 0.0
  mix_speed: float | None = None

  def get_duration(self, config: SimulatorConfig) -> float:
    assert self.speed > 0, f"Speed must be positive, got {self.speed}"
    duration = max(config.min_op_duration, self.volume / self.speed)

    if self.mix_cycles > 0:
      assert self.mix_speed is not None and self.mix_speed > 0, (
        f"Mix speed must be positive when mix_cycles > 0, got {self.mix_speed}"
      )
      duration += self.mix_cycles * 2 * (self.mix_volume / self.mix_speed)

    return duration


@dataclass(kw_only=True, frozen=True)
class _TipOp(_ChannelOpBase):
  def get_duration(self, config: SimulatorConfig) -> float:
    return config.tip_op_duration


@dataclass(kw_only=True, frozen=True)
class _ChannelMoveOp:
  target_y: float
  op: _ChannelOpBase | None = None


@dataclass(kw_only=True, frozen=True)
class _ParsedChannelOpParams:
  target_x_step: int
  target_y: float
  op: _ChannelOpBase


@dataclass(kw_only=True, frozen=True)
class _ParsedGlobalOpParams:
  minimum_traverse_height: float


class STARSimulatorBackend(STARBackend):
  """A simulated backend for Hamilton STAR with realistic timing."""

  def __init__(
    self,
    num_channels: int = 8,
    machine_configuration: MachineConfiguration = _DEFAULT_MACHINE_CONFIGURATION,
    extended_configuration: ExtendedConfiguration = _DEFAULT_EXTENDED_CONFIGURATION,
    iswap_information: iSWAPInformation = _DEFAULT_ISWAP_INFORMATION,
    channels_minimum_y_spacing: list[float] | None = None,
    config: SimulatorConfig | None = None,
  ):
    """Initialize a chatter box backend.

    Args:
      num_channels: Number of pipetting channels (default: 8)
      machine_configuration: Machine configuration to return from `request_machine_configuration`.
      extended_configuration: Extended configuration to return from `request_extended_configuration`.
      iswap_information: Optional override for the simulated iSWAP setup state
        (link lengths, EEPROM-calibrated stops, fw version). None means use
        `_DEFAULT_ISWAP_INFORMATION` (Hamilton factory defaults). Only used
        when the extended configuration reports iSWAP as installed.
      channels_minimum_y_spacing: Per-channel minimum Y spacing in mm. If None, defaults to
        `extended_configuration.min_raster_pitch_pip_channels` for all channels.
    """
    super().__init__()
    self._num_channels = num_channels
    self._iswap_parked = True

    self._iswap_information = (
      iswap_information if extended_configuration.left_x_drive.iswap_installed else None
    )
    self._machine_configuration = machine_configuration
    self._extended_conf = extended_configuration

    if channels_minimum_y_spacing is not None:
      if len(channels_minimum_y_spacing) != num_channels:
        raise ValueError(
          f"channels_minimum_y_spacing has {len(channels_minimum_y_spacing)}"
          f" entries, expected {num_channels}."
        )
      self._channels_minimum_y_spacing = list(channels_minimum_y_spacing)
    else:
      self._channels_minimum_y_spacing = [
        extended_configuration.min_raster_pitch_pip_channels
      ] * num_channels

    self.io = mock_io.MockIO()  # type: ignore[assignment]

    self.config = config or SimulatorConfig()

    # Initial positions
    self._current_x = 500.0
    if self.extended_conf.left_x_drive.core_96_head_installed:
      self._head96_position = PipettePosition()
    else:
      self._head96_position = None  # type: ignore[assignment]

    if self.extended_conf.left_x_drive.iswap_installed:
      self._iswap_state = ISwapState()
    else:
      self._iswap_state = None  # type: ignore[assignment]

    self.channels = [PipettePosition() for _ in range(num_channels)]

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    *,
    skip_instrument_initialization: bool = False,
    skip_pip: bool = False,
    skip_autoload: bool = False,
    skip_iswap: bool = False,
    skip_core96_head: bool = False,
  ):
    """Initialize the chatterbox backend and detect installed modules.

    Args:
      skip_instrument_initialization: If True, skip instrument initialization.
      skip_pip: If True, skip pipetting channel initialization.
      skip_autoload: If True, skip initializing the autoload module, if applicable.
      skip_iswap: If True, skip initializing the iSWAP module, if applicable.
      skip_core96_head: If True, skip initializing the CoRe 96 head module, if applicable.
    """
    await LiquidHandlerBackend._enter_lifespan(self, stack)

    self.id_ = 0

    # Mock firmware information for 96-head if installed
    if self.extended_conf.left_x_drive.core_96_head_installed and not skip_core96_head:
      self._head96_information = Head96Information(
        fw_version=datetime.date(2023, 1, 1),
        supports_clot_monitoring_clld=False,
        stop_disc_type="core_ii",
        instrument_type="FM-STAR",
        head_type="96 head II",
      )
    else:
      self._head96_information = None

    self._core_parked = True
    self._iswap_parked = True
    self._setup_done = True

    @stack.callback
    def exit():
      self._setup_done = False

  # ---- queries

  async def request_tip_presence(self) -> list[bool | None]:
    return [self.channels[ch].has_tip for ch in range(self.num_channels)]

  async def request_machine_configuration(self) -> MachineConfiguration:
    return self._machine_configuration

  async def request_extended_configuration(self) -> ExtendedConfiguration:
    assert self._extended_conf is not None
    return self._extended_conf

  async def request_left_x_arm_position(self) -> float:
    """Request left X-Arm position"""
    return self._current_x

  async def request_y_pos_channel_n(self, pipetting_channel_index: int) -> float:
    """Request Y-Position of Pipetting channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 0 and 15.
        0 is the backmost channel.
    """

    assert 0 <= pipetting_channel_index < self.num_channels, (
      "pipetting_channel_index must be between 0 and self.num_channels"
    )
    return self.channels[pipetting_channel_index].current_y

  async def head96_request_position(self) -> Coordinate:
    assert self._head96_position is not None
    return Coordinate(
      x=self._current_x + self.config.head96_x_offset,
      y=self._head96_position.current_y,
      z=self._head96_position.current_z,
    )

  async def iswap_rotation_drive_request_y(self) -> float:
    """Request iSWAP rotation drive Y position (deck coordinates), in mm.

    Reads the linear Y carriage that the rotation joint is mounted on. This is
    NOT the gripper finger's Y - the finger position depends on the rotation
    drive (W) and wrist (T) angles. Use `iswap_rotation_drive_request_position`
    for the rotation drive's full XYZ.
    """
    if not self.extended_conf.left_x_drive.iswap_installed:
      raise RuntimeError("iSWAP is not installed")
    return self._iswap_state.current_y

  async def iswap_rotation_drive_request_z(self) -> float:
    """Request iSWAP rotation-drive-bottom Z (deck coordinates), in mm.

    Returns the Z of the rotation drive's lowest physical point, which sits
    `iswap_rotation_drive_z_offset_above_finger_mm` above the gripper finger
    plane that R0 RZ reports.
    """
    if not self.extended_conf.left_x_drive.iswap_installed:
      raise RuntimeError("iSWAP is not installed")
    return self._iswap_state.current_z

  async def iswap_rotation_drive_request_angle(self) -> float:
    """Query the iSWAP rotation drive angle in degrees (signed, 0 deg = calibrated FRONT)."""
    if not self.extended_conf.left_x_drive.iswap_installed:
      raise RuntimeError("iSWAP is not installed")
    return self._iswap_state.current_w

  async def iswap_wrist_drive_request_angle(self) -> float:
    """Query the iSWAP wrist drive angle in degrees (signed, 0 deg = motor zero).

    See `_iswap_wrist_drive_increments_to_angle` for the conversion. The
    motor's raw zero sits between STRAIGHT and LEFT; this convention keeps
    the achievable range symmetric (~+/-152 deg).
    """
    if not self.extended_conf.left_x_drive.iswap_installed:
      raise RuntimeError("iSWAP is not installed")
    return self._iswap_state.current_t

  async def iswap_gripper_request_width(self) -> float:
    """Request the current iSWAP gripper jaw opening width, in mm.

    RG is always available and reads the raw drive encoder.
    """
    if not self.extended_conf.left_x_drive.iswap_installed:
      raise RuntimeError("iSWAP is not installed")
    return self._iswap_state.current_g

  async def request_plate_in_iswap(self) -> bool:
    if not self.extended_conf.left_x_drive.iswap_installed:
      raise RuntimeError("iSWAP is not installed")
    return self._iswap_state.holding_resource

  async def head96_request_tip_presence(self) -> int:
    if not self.extended_conf.left_x_drive.core_96_head_installed:
      raise RuntimeError("96-head is not installed")
    return 1 if self._head96_position.has_tip else 0

  async def request_tip_presence_in_core_96_head(self) -> dict[str, int]:
    if not self.extended_conf.left_x_drive.core_96_head_installed:
      raise RuntimeError("96-head is not installed")
    return {"qh": 1 if self._head96_position.has_tip else 0}

  async def head96_dispensing_drive_request_position_mm(self) -> float:
    if not self.extended_conf.left_x_drive.core_96_head_installed:
      raise RuntimeError("96-head is not installed")
    return self._head96_dispensing_drive_uL_to_mm(self._head96_position.dispensing_drive_position)

  async def head96_dispensing_drive_request_position_uL(self) -> float:
    if not self.extended_conf.left_x_drive.core_96_head_installed:
      raise RuntimeError("96-head is not installed")
    return self._head96_position.dispensing_drive_position

  async def channel_dispensing_drive_request_position(self, channel_idx: int) -> float:
    assert 0 <= channel_idx < self.num_channels, "Invalid channel index"
    return self.channels[channel_idx].dispensing_drive_position

  def _check_iswap_installed(self):
    if self._iswap_state is None:
      raise RuntimeError("iSWAP is not installed")

  def _check_head96_installed(self):
    if self._head96_position is None:
      raise RuntimeError("96-head is not installed")

  # ---- commands

  def _calculate_time(self, distance: float, max_speed: float, acceleration: float) -> float:
    if distance == 0:
      return 0.0
    # Distance to accelerate to max_speed
    d_acc = (max_speed**2) / (2 * acceleration)
    if distance < 2 * d_acc:
      # Max speed is not reached
      return 2 * math.sqrt(distance / acceleration)
    else:
      # Max speed is reached
      return (2 * max_speed / acceleration) + (distance - 2 * d_acc) / max_speed

  async def send_command(
    self,
    module: str,
    command: str,
    auto_id: bool = True,
    tip_pattern: list[bool] | None = None,
    write_timeout: int | None = None,
    read_timeout: int | None = None,
    wait: bool = True,
    fmt: Any | None = None,
    **kwargs,
  ):
    ret = None

    # Pre-process module to extract channel index for single channel direct commands
    channel_idx = None
    matched_module = module
    if module.startswith("P") and len(module) == 2 and module[1] in "123456789ABCDEFG":
      channel_idx = "123456789ABCDEFG".index(module[1])
      matched_module = "P*"

    match matched_module:
      case "C0":
        await self._simulate_c0_command(command, kwargs, tip_pattern=tip_pattern)
      case "P*":
        await self._simulate_p_channel_command(channel_idx, command, kwargs)
      case "H0":
        await self._simulate_h0_command(command, kwargs)
      case "X0":
        await self._simulate_x0_command(command, kwargs)
      case "R0":
        await self._simulate_r0_command(command, kwargs)

    return ret

  async def _simulate_c0_command(
    self,
    command: str,
    kwargs: dict[str, Any],
    tip_pattern: list[bool] | None = None,
  ):
    config = self.config
    match command:
      case "AS" | "DS" | "TP" | "TR":
        await self._simulate_pip_operation(command, kwargs, tip_pattern=tip_pattern)
      case "EP":  # 96 tip pickup

        async def cb_pickup():
          self._head96_position.has_tip = True

        await self._simulate_head96_action(kwargs, "za", config.tip_op_duration, cb_pickup)
      case "ER":  # 96 tip discard

        async def cb_discard():
          self._head96_position.has_tip = False

        await self._simulate_head96_action(kwargs, "za", config.tip_op_duration, cb_discard)
      case "EA":  # 96 aspirate
        await self._simulate_head96_aspirate(kwargs)
      case "ED":  # 96 dispense
        await self._simulate_head96_dispense(kwargs)
      case "EM":  # 96 move to coordinate
        await self._simulate_head96_move(kwargs)
      case "EI":  # 96 init
        await self._simulate_head96_init(kwargs)
      case "FI":  # iswap init stand-alone
        await self._simulate_iswap_init(kwargs)
      case "GI":  # iswap gripper init
        await self._simulate_iswap_gripper_init(kwargs)
      case "GF":  # iswap open gripper
        await self._simulate_iswap_open(kwargs)
      case "GC":  # iswap close gripper
        await self._simulate_iswap_close(kwargs)
      case "PG":  # iswap park
        await self._simulate_iswap_park(kwargs)
      case "PP":  # iswap get plate
        await self._simulate_iswap_plate_action(kwargs, is_get=True)
      case "PR":  # iswap put plate
        await self._simulate_iswap_plate_action(kwargs, is_get=False)
      case "FY":  # free iswap Y range
        await self._simulate_free_iswap_y_range(kwargs)

  async def _simulate_p_channel_command(
    self, channel_idx: int | None, command: str, kwargs: dict[str, Any]
  ):
    assert channel_idx is not None, "Missing channel index for P* command"
    match command:
      case "DS":
        await self._simulate_channel_move_dispensing_drive(channel_idx, kwargs)

  async def _simulate_h0_command(self, command: str, kwargs: dict[str, Any]):
    match command:
      case "YA":  # 96 move Y
        await self._simulate_head96_move_y(kwargs)
      case "ZA":  # 96 move Z
        await self._simulate_head96_move_z(kwargs)
      case "DQ":  # 96 move dispensing drive
        await self._simulate_head96_move_dispensing_drive(kwargs)

  async def _simulate_x0_command(self, command: str, kwargs: dict[str, Any]):
    match command:
      case "XP":  # iswap move X (gantry)
        await self._simulate_iswap_move_x(kwargs)

  async def _simulate_r0_command(self, command: str, kwargs: dict[str, Any]):
    match command:
      case "YA":  # iswap move Y
        await self._simulate_iswap_move_y(kwargs)
      case "ZA":  # iswap move Z
        await self._simulate_iswap_move_z(kwargs)
      case "PA":  # iswap parallel joint move
        await self._simulate_iswap_move_joints(kwargs)
      case "TP":  # iswap rotate wrist predefined
        await self._simulate_iswap_rotate_wrist_predefined(kwargs)
      case "PD":  # iswap rotate predefined
        await self._simulate_iswap_rotate_predefined(kwargs)

  def _parse_pip_command(
    self,
    *,
    command: str,
    kwargs: dict[str, Any],
    tip_pattern: list[bool] | None = None,
  ) -> tuple[list[_ParsedChannelOpParams | None], _ParsedGlobalOpParams]:
    n = self.num_channels

    # Use tip_pattern if provided, kwargs may provide shorter lists;
    # otherwise they must be equal in length
    a = len(tip_pattern) if tip_pattern is not None else None

    global_param_names = {"th"}
    channel_param_names = {
      "tm",
      "xp",
      "yp",
      "zx",
      "tz",
      "mc",
      "mv",
      "ms",
      "as_",
      "av",
      "ds",
      "dv",
    }
    require_list = {"tm", "xp", "yp"}

    parsed: dict[str, Any] = {}
    global_params = {}

    for k, v in kwargs.items():
      if k in global_param_names:
        if not isinstance(v, (bool, int, str)):
          raise TypeError(f"Expected scalar for {k}, got unexpected type: {v}")
        global_params[k] = int(v)
        continue

      if k not in channel_param_names:
        continue

      if k in require_list:
        if not isinstance(v, list):
          raise TypeError(f"Unexpected scalar value for {k}: {v}")
      else:
        # Can be list or scalar
        if not isinstance(v, list):
          if not isinstance(v, (bool, int, str)):
            raise TypeError(f"Unexpected type for scalar {k}: {type(v)}")
          parsed[k] = int(v)
          continue

      # It is a list (either required or optional)
      if not all(isinstance(vv, (bool, int, str)) for vv in v):
        raise TypeError(f"Unexpected value type in list for {k}: {v}")

      v_ints = [int(vv) for vv in v]
      m = len(v_ints)
      if tip_pattern is not None:
        assert a is not None
        if m < a:
          v_ints = self._to_list(v_ints, tip_pattern)
      else:
        if a is None:
          a = m
        elif m != a:
          raise ValueError(f"Mismatched argument length, {k} has {m} elements, but need {a}")
      parsed[k] = v_ints

    tm = [bool(v) for v in parsed["tm"]]
    if tip_pattern is None:
      tip_pattern = tm
    else:
      assert tip_pattern == tm

    assert a is not None

    # Flesh out scalars in channel_params to constant list of length a
    for k, v in parsed.items():
      if not isinstance(v, list):
        parsed[k] = [v] * a
      else:
        if len(v) != a:
          parsed[k] = self._to_list(v, tip_pattern)

    # Pre-pad result list with None up to `n` (num_channels)
    parsed_states: list[_ParsedChannelOpParams | None] = [None] * n

    for i, active in enumerate(tip_pattern):
      if not active:
        continue

      def get(key):
        val = parsed.get(key)
        return val[i] if val is not None else None

      xp = get("xp")
      yp = get("yp")

      assert xp is not None, f"Missing 'xp' for command {command}"
      assert yp is not None, f"Missing 'yp' for command {command}"

      op: _ChannelOpBase | None = None
      match command:
        case "AS" | "DS":
          zx = get("zx")
          lv = get(dict(AS="av", DS="dv").get(command))
          ls = get(dict(AS="as_", DS="ds").get(command))

          assert zx is not None, f"Missing 'zx' for command {command}"
          assert lv is not None, f"Missing volume for command {command}"
          assert ls is not None, f"Missing speed for command {command}"

          op = _AspirateDispenseOp(
            target_z=zx * _FW_STEP,
            volume=lv * _FW_VOL_STEP,
            speed=ls * _FW_VOL_STEP,
            mix_cycles=get("mc") or 0,
            mix_volume=get("mv") or 0.0,
            mix_speed=get("ms"),
          )
        case "TP" | "TR":
          tz = get("tz")

          assert tz is not None, f"Missing 'tz' for command {command}"
          op = _TipOp(target_z=tz * _FW_STEP)

      assert op is not None
      parsed_states[i] = _ParsedChannelOpParams(target_x_step=xp, target_y=yp * _FW_STEP, op=op)

    th = global_params.get("th")
    assert th is not None, f"Missing 'th' parameter for command {command}"

    global_op_params = _ParsedGlobalOpParams(minimum_traverse_height=float(th) * _FW_STEP)

    return parsed_states, global_op_params

  def _create_channel_tasks(
    self,
    *,
    target_x_step: int,
    channels_data: list[_ParsedChannelOpParams | None],
  ) -> list[_ChannelMoveOp]:
    n = self.num_channels
    target_y = [c.current_y for c in self.channels]
    active_channels_at_this_x = []
    inactive_channels_at_this_x = []

    for i, data in enumerate(channels_data):
      if data is not None and data.target_x_step == target_x_step:
        target_y[i] = data.target_y
        active_channels_at_this_x.append(i)
      else:
        inactive_channels_at_this_x.append(i)

    # Collision avoidance for active channels
    min_separation = self.config.min_channel_separation
    for idx, i in enumerate(active_channels_at_this_x):
      for j in active_channels_at_this_x[idx + 1 :]:
        if j < i:
          assert target_y[j] >= target_y[i] + (j - i) * min_separation, (
            f"Active channels {i} and {j} collide!"
          )

    # Collision avoidance for inactive channels
    for i in inactive_channels_at_this_x:
      y_min = -float("inf")
      y_max = float("inf")
      for j in active_channels_at_this_x:
        if j > i:
          y_min = max(y_min, target_y[j] + (i - j) * min_separation)
        elif j < i:
          y_max = min(y_max, target_y[j] - (j - i) * min_separation)

      target_y[i] = max(y_min, min(y_max, self.channels[i].current_y))

    # Form the objects
    tasks = []
    for i in range(n):
      data = channels_data[i]
      if i in active_channels_at_this_x:
        assert data is not None
        op = data.op
      else:
        op = None
      tasks.append(_ChannelMoveOp(target_y=target_y[i], op=op))

    return tasks

  async def _simulate_pip_operation(
    self,
    command: str,
    kwargs: dict[str, Any],
    tip_pattern: list[bool] | None = None,
  ):
    channels_data, global_params = self._parse_pip_command(
      command=command, kwargs=kwargs, tip_pattern=tip_pattern
    )

    # Find unique X targets for ACTIVE channels
    active_x_target_steps = set()
    for data in channels_data:
      if data is not None:
        active_x_target_steps.add(data.target_x_step)

    sorted_x_target_steps = sorted(list(active_x_target_steps))

    if not sorted_x_target_steps:
      return

    # 1. Initial Z move to traverse height for all channels
    async with anyio.create_task_group() as tg:
      for i in range(self.num_channels):

        async def _init_z_task(c_idx=i):
          t_z = global_params.minimum_traverse_height
          if self.channels[c_idx].current_z < t_z:
            z_init_distance = abs(self.channels[c_idx].current_z - t_z)
            z_init_time = self._calculate_time(
              z_init_distance,
              self.config.pip_z_drive.speed,
              self.config.pip_z_drive.acceleration,
            )
            await anyio.sleep(z_init_time)
            self.channels[c_idx].current_z = t_z

        tg.start_soon(_init_z_task)

    for target_x_step in sorted_x_target_steps:
      target_x = target_x_step * _FW_STEP

      # Create channel tasks for this X target
      channel_tasks = self._create_channel_tasks(
        target_x_step=target_x_step, channels_data=channels_data
      )

      x_reached = anyio.Event()

      async with anyio.create_task_group() as tg:
        # Spawn X gantry task
        async def _move_x():
          x_distance = abs(target_x - self._current_x)
          x_time = self._calculate_time(
            x_distance,
            self.config.x_drive.speed,
            self.config.x_drive.acceleration,
          )
          await anyio.sleep(x_time)
          self._current_x = target_x
          x_reached.set()

        tg.start_soon(_move_x)

        # Spawn tasks for all channels
        for i, task in enumerate(channel_tasks):

          async def _channel_task(c_idx=i, move_op=task):
            # Move Y
            y_distance = abs(move_op.target_y - self.channels[c_idx].current_y)
            y_time = self._calculate_time(
              y_distance,
              self.config.pip_y_drive.speed,
              self.config.pip_y_drive.acceleration,
            )
            await anyio.sleep(y_time)
            self.channels[c_idx].current_y = move_op.target_y

            # Wait for X
            await x_reached.wait()

            if move_op.op is not None:
              # Move Z down
              z_distance = abs(self.channels[c_idx].current_z - move_op.op.target_z)
              z_time = self._calculate_time(
                z_distance,
                self.config.pip_z_drive.speed,
                self.config.pip_z_drive.acceleration,
              )
              await anyio.sleep(z_time)
              self.channels[c_idx].current_z = move_op.op.target_z

              # Perform operation
              o_time = move_op.op.get_duration(self.config)
              await anyio.sleep(o_time)

              # Update simulated state
              if isinstance(move_op.op, _AspirateDispenseOp):
                if command == "AS":
                  self.channels[c_idx].dispensing_drive_position += move_op.op.volume
                elif command == "DS":
                  self.channels[c_idx].dispensing_drive_position -= move_op.op.volume
                self.channels[c_idx].dispensing_drive_position = max(
                  self.DISPENSING_DRIVE_VOL_LIMIT_BOTTOM,
                  min(
                    self.DISPENSING_DRIVE_VOL_LIMIT_TOP,
                    self.channels[c_idx].dispensing_drive_position,
                  ),
                )
              elif isinstance(move_op.op, _TipOp):
                if command == "TP":
                  self.channels[c_idx].has_tip = True
                elif command == "TR":
                  self.channels[c_idx].has_tip = False

              # Move Z back to traverse height
              z_return_distance = abs(move_op.op.target_z - global_params.minimum_traverse_height)
              z_return_time = self._calculate_time(
                z_return_distance,
                self.config.pip_z_drive.speed,
                self.config.pip_z_drive.acceleration,
              )
              await anyio.sleep(z_return_time)
              self.channels[c_idx].current_z = global_params.minimum_traverse_height

          tg.start_soon(_channel_task)

  @property
  def _iswap_t_straight(self) -> float:
    if self._iswap_information is None:
      return -45.0
    wrist_straight_increments = self._iswap_information.wrist_drive_predefined_increments[
      STARBackend.WristDriveOrientation.STRAIGHT
    ]
    return STARBackend._iswap_wrist_drive_increments_to_angle(
      wrist_straight_increments,
      self._iswap_information.wrist_deg_per_increment,
    )

  async def _simulate_head96_action(
    self,
    kwargs: dict[str, Any],
    action_z_key: str,
    action_duration: float,
    action_callback: Any | None = None,
  ):
    self._check_head96_installed()
    config = self.config
    x_pos = int(kwargs["xs"]) * _FW_STEP
    x_dir = int(kwargs["xd"])
    x_t = x_pos * (-1 if x_dir == 1 else 1)
    Xg_target = x_t - config.head96_x_offset

    Y_target = int(kwargs["yh"]) * _FW_STEP
    Z_action = int(kwargs[action_z_key]) * _FW_STEP
    Z_traverse = int(kwargs["zh"]) * _FW_STEP
    Z_end = int(kwargs["ze"]) * _FW_STEP

    # 1. Initial Z move to traverse height
    if self._head96_position.current_z < Z_traverse:
      dz = Z_traverse - self._head96_position.current_z
      t = self._calculate_time(dz, config.head96_z_drive.speed, config.head96_z_drive.acceleration)
      await anyio.sleep(t)
      self._head96_position.current_z = Z_traverse

    # 2. Parallel X, Y move
    dx = abs(Xg_target - self._current_x)
    dy = abs(Y_target - self._head96_position.current_y)
    tx = self._calculate_time(dx, config.x_drive.speed, config.x_drive.acceleration)
    ty = self._calculate_time(dy, config.head96_y_drive.speed, config.head96_y_drive.acceleration)
    await anyio.sleep(max(tx, ty))
    self._current_x = Xg_target
    self._head96_position.current_y = Y_target

    # 3. Z move down to action height
    dz = abs(self._head96_position.current_z - Z_action)
    t = self._calculate_time(dz, config.head96_z_drive.speed, config.head96_z_drive.acceleration)
    await anyio.sleep(t)
    self._head96_position.current_z = Z_action

    # 4. Action
    await anyio.sleep(action_duration)
    if action_callback is not None:
      await action_callback()

    # 5. Z move up to end height
    dz = abs(Z_end - self._head96_position.current_z)
    t = self._calculate_time(dz, config.head96_z_drive.speed, config.head96_z_drive.acceleration)
    await anyio.sleep(t)
    self._head96_position.current_z = Z_end

  async def _simulate_head96_aspirate(self, kwargs: dict[str, Any]):
    self._check_head96_installed()
    config = self.config
    V = int(kwargs["af"]) * _FW_VOL_STEP
    S = int(kwargs["ag"]) * _FW_VOL_STEP
    duration = max(config.min_op_duration, V / S)

    hc = int(kwargs.get("hc", 0))
    if hc > 0:
      hv = int(kwargs.get("hv", 0)) * _FW_VOL_STEP
      hs = int(kwargs.get("hs", 1200)) * _FW_VOL_STEP
      if hs > 0:
        duration += hc * 2 * (hv / hs)

    async def cb():
      self._head96_position.dispensing_drive_position += V
      self._head96_position.dispensing_drive_position = min(
        self.HEAD96_DISPENSING_DRIVE_VOL_LIMIT_TOP,
        self._head96_position.dispensing_drive_position,
      )

    await self._simulate_head96_action(kwargs, "zm", duration, cb)

  async def _simulate_head96_dispense(self, kwargs: dict[str, Any]):
    self._check_head96_installed()
    config = self.config
    V = int(kwargs["df"]) * _FW_VOL_STEP
    S = int(kwargs["dg"]) * _FW_VOL_STEP
    duration = max(config.min_op_duration, V / S)

    hc = int(kwargs.get("hc", 0))
    if hc > 0:
      hv = int(kwargs.get("hv", 0)) * _FW_VOL_STEP
      hs = int(kwargs.get("hs", 1200)) * _FW_VOL_STEP
      if hs > 0:
        duration += hc * 2 * (hv / hs)

    async def cb():
      self._head96_position.dispensing_drive_position -= V
      self._head96_position.dispensing_drive_position = max(
        self.HEAD96_DISPENSING_DRIVE_VOL_LIMIT_BOTTOM,
        self._head96_position.dispensing_drive_position,
      )

    await self._simulate_head96_action(kwargs, "zm", duration, cb)

  async def _simulate_head96_move(self, kwargs: dict[str, Any]):
    self._check_head96_installed()
    config = self.config
    x_pos = int(kwargs["xs"]) * _FW_STEP
    x_dir = int(kwargs["xd"])
    x_t = x_pos * (-1 if x_dir == 1 else 1)
    Xg_target = x_t - config.head96_x_offset

    Y_target = int(kwargs["yh"]) * _FW_STEP
    Z_target = int(kwargs["za"]) * _FW_STEP
    Z_traverse = int(kwargs["zh"]) * _FW_STEP

    # 1. Initial Z move to traverse height
    if self._head96_position.current_z < Z_traverse:
      dz = Z_traverse - self._head96_position.current_z
      t = self._calculate_time(dz, config.head96_z_drive.speed, config.head96_z_drive.acceleration)
      await anyio.sleep(t)
      self._head96_position.current_z = Z_traverse

    # 2. Parallel X, Y move to target
    dx = abs(Xg_target - self._current_x)
    dy = abs(Y_target - self._head96_position.current_y)

    tx = self._calculate_time(dx, config.x_drive.speed, config.x_drive.acceleration)
    ty = self._calculate_time(dy, config.head96_y_drive.speed, config.head96_y_drive.acceleration)

    await anyio.sleep(max(tx, ty))
    self._current_x = Xg_target
    self._head96_position.current_y = Y_target

    # 3. Z move to target height
    dz = abs(Z_target - self._head96_position.current_z)
    tz = self._calculate_time(dz, config.head96_z_drive.speed, config.head96_z_drive.acceleration)
    await anyio.sleep(tz)
    self._head96_position.current_z = Z_target

  async def _simulate_head96_init(self, kwargs: dict[str, Any]):
    self._check_head96_installed()
    config = self.config
    x_pos = int(kwargs["xs"]) * _FW_STEP
    x_dir = int(kwargs["xd"])
    x_t = x_pos * (-1 if x_dir == 1 else 1)
    Xg_target = x_t - config.head96_x_offset

    Y_target = int(kwargs["yh"]) * _FW_STEP
    Z_target = int(kwargs["ze"]) * _FW_STEP

    await anyio.sleep(config.module_init_time)

    self._current_x = Xg_target
    self._head96_position.current_y = Y_target
    self._head96_position.current_z = Z_target
    self._head96_position.has_tip = False
    self._head96_position.dispensing_drive_position = 0.0

  async def _simulate_head96_move_y(self, kwargs: dict[str, Any]):
    self._check_head96_installed()
    ya_val = int(kwargs["ya"])
    Y_target = self._head96_y_drive_increment_to_mm(ya_val)

    yv_val = int(kwargs["yv"])
    speed_mm = self._head96_y_drive_increment_to_mm(yv_val)

    yr_val = int(kwargs["yr"])
    accel_mm = self._head96_y_drive_increment_to_mm(yr_val)

    dy = abs(Y_target - self._head96_position.current_y)
    t = self._calculate_time(dy, speed_mm, accel_mm)
    await anyio.sleep(t)
    self._head96_position.current_y = Y_target

  async def _simulate_head96_move_z(self, kwargs: dict[str, Any]):
    self._check_head96_installed()
    za_val = int(kwargs["za"])
    Z_target = self._head96_z_drive_increment_to_mm(za_val)

    zv_val = int(kwargs["zv"])
    speed_mm = self._head96_z_drive_increment_to_mm(zv_val)

    zr_val = int(kwargs["zr"])
    accel_mm = self._head96_z_drive_increment_to_mm(zr_val)

    dz = abs(Z_target - self._head96_position.current_z)
    t = self._calculate_time(dz, speed_mm, accel_mm)
    await anyio.sleep(t)
    self._head96_position.current_z = Z_target

  async def _simulate_head96_move_dispensing_drive(self, kwargs: dict[str, Any]):
    self._check_head96_installed()
    dq_val = int(kwargs["dq"])
    V_target = self._head96_dispensing_drive_increment_to_uL(dq_val)

    dv_val = int(kwargs["dv"])
    speed_uL = self._head96_dispensing_drive_increment_to_uL(dv_val)

    dr_val = int(kwargs["dr"])
    accel_uL = self._head96_dispensing_drive_increment_to_uL(dr_val)

    V_current = self._head96_position.dispensing_drive_position
    dv = abs(V_target - V_current)
    t = self._calculate_time(dv, speed_uL, accel_uL)
    await anyio.sleep(t)
    self._head96_position.dispensing_drive_position = V_target

  async def _simulate_channel_move_dispensing_drive(self, channel_idx: int, kwargs: dict[str, Any]):
    ds_val = int(kwargs["ds"])
    dt_val = int(kwargs["dt"])
    dv_val = int(kwargs["dv"])
    dr_val = int(kwargs["dr"])

    relative_vol = STARBackend.dispensing_drive_increment_to_volume(ds_val)
    direction = 1.0 if dt_val == 0 else -1.0
    vol_change = relative_vol * direction

    speed_incr = dv_val
    accel_incr = dr_val * 1000.0
    t = self._calculate_time(ds_val, speed_incr, accel_incr)
    await anyio.sleep(t)

    self.channels[channel_idx].dispensing_drive_position += vol_change
    self.channels[channel_idx].dispensing_drive_position = max(
      self.DISPENSING_DRIVE_VOL_LIMIT_BOTTOM,
      min(
        self.DISPENSING_DRIVE_VOL_LIMIT_TOP,
        self.channels[channel_idx].dispensing_drive_position,
      ),
    )

  async def _simulate_iswap_init(self, kwargs: dict[str, Any]):
    self._check_iswap_installed()
    await anyio.sleep(self.config.module_init_time)
    self._iswap_parked = True
    self._iswap_state.holding_resource = False

    self._iswap_state.current_y = self.iswap_information.rotation_drive_y_max
    self._iswap_state.current_z = (
      self._iswap_traversal_height + STARBackend.iswap_rotation_drive_z_offset_above_finger_mm
    )
    FRONT_incr = self.iswap_information.rotation_drive_predefined_increments[
      STARBackend.RotationDriveOrientation.FRONT
    ]
    self._iswap_state.current_w = FRONT_incr * self.iswap_information.rotation_deg_per_increment
    self._iswap_state.current_t = self._iswap_t_straight
    self._iswap_state.current_g = 90.0

  async def _simulate_iswap_gripper_init(self, kwargs: dict[str, Any]):
    self._check_iswap_installed()
    await anyio.sleep(self.config.module_init_time)
    self._iswap_state.current_g = 90.0

  async def _simulate_iswap_open(self, kwargs: dict[str, Any]):
    self._check_iswap_installed()
    go_val = int(kwargs["go"])
    G_target = go_val * _FW_STEP
    await anyio.sleep(self.config.iswap_gripper_op_duration)
    self._iswap_state.current_g = G_target
    self._iswap_state.holding_resource = False

  async def _simulate_iswap_close(self, kwargs: dict[str, Any]):
    self._check_iswap_installed()
    gb_val = int(kwargs["gb"])
    G_target = gb_val * _FW_STEP
    await anyio.sleep(self.config.iswap_gripper_op_duration)
    self._iswap_state.current_g = G_target
    self._iswap_state.holding_resource = True

  async def _simulate_iswap_park(self, kwargs: dict[str, Any]):
    self._check_iswap_installed()
    config = self.config

    # Narrow contract: raise if currently holding a resource!
    if self._iswap_state.holding_resource:
      raise RuntimeError("Cannot park iSWAP while holding a resource")

    th_val = int(kwargs["th"])
    Z_park = th_val * _FW_STEP + STARBackend.iswap_rotation_drive_z_offset_above_finger_mm

    # Look up park stop angles from iswap_information predefined stops table
    rot_stops = self.iswap_information.rotation_drive_predefined_increments
    wrist_stops = self.iswap_information.wrist_drive_predefined_increments
    rot_scale = self.iswap_information.rotation_deg_per_increment
    wrist_scale = self.iswap_information.wrist_deg_per_increment

    W_park = rot_stops[STARBackend.RotationDriveOrientation.PARKED_RIGHT] * rot_scale
    T_park = wrist_stops[STARBackend.WristDriveOrientation.RIGHT] * wrist_scale
    Y_park = self.iswap_information.rotation_drive_y_max

    # 1. Step 1: Safe Z lift to traverse height
    dz = abs(Z_park - self._iswap_state.current_z)
    tz = self._calculate_time(dz, config.iswap_z_drive.speed, config.iswap_z_drive.acceleration)
    await anyio.sleep(tz)
    self._iswap_state.current_z = Z_park

    # 2. Step 2: Move Y to safe intermediate position Y = Y_park - 200.0 mm
    Y_safe = Y_park - 200.0
    dy1 = abs(Y_safe - self._iswap_state.current_y)
    ty1 = self._calculate_time(dy1, config.iswap_y_drive.speed, config.iswap_y_drive.acceleration)
    await anyio.sleep(ty1)
    self._iswap_state.current_y = Y_safe

    # 3. Step 3: Parallel W/T joint moves
    dw = abs(W_park - self._iswap_state.current_w)
    dt = abs(T_park - self._iswap_state.current_t)

    tw = self._calculate_time(
      dw,
      config.iswap_rotation_drive.speed,
      config.iswap_rotation_drive.acceleration,
    )
    tt = self._calculate_time(
      dt,
      config.iswap_wrist_drive.speed,
      config.iswap_wrist_drive.acceleration,
    )

    t_joints = max(tw, tt)
    await anyio.sleep(t_joints)
    self._iswap_state.current_w = W_park
    self._iswap_state.current_t = T_park

    # 4. Step 4: Final Y move to park limit
    dy2 = abs(Y_park - self._iswap_state.current_y)
    ty2 = self._calculate_time(dy2, config.iswap_y_drive.speed, config.iswap_y_drive.acceleration)
    await anyio.sleep(ty2)

    # Update final states
    self._iswap_parked = True
    self._iswap_state.holding_resource = False
    self._iswap_state.current_y = Y_park
    self._iswap_state.current_w = W_park
    self._iswap_state.current_t = T_park

  async def _simulate_iswap_move_x(self, kwargs: dict[str, Any]):
    self._check_iswap_installed()
    config = self.config
    la_val = int(kwargs["la"])
    Xg_target = la_val * _FW_STEP

    lr_val = int(kwargs.get("lr", 3))
    accel = config.x_drive.acceleration * (lr_val / 3.0)

    dx = abs(Xg_target - self._current_x)
    t = self._calculate_time(dx, config.x_drive.speed, accel)
    await anyio.sleep(t)
    self._current_x = Xg_target

  async def _simulate_iswap_move_y(self, kwargs: dict[str, Any]):
    self._check_iswap_installed()
    config = self.config
    ya_val = int(kwargs["ya"])
    Y_target = self.iswap_y_drive_increment_to_mm(ya_val, self.iswap_information.y_mm_per_increment)

    yv_val = int(kwargs["yv"])
    speed_mm = self.iswap_y_drive_increment_to_mm(yv_val, self.iswap_information.y_mm_per_increment)

    yr_val = int(kwargs.get("yr", 2))
    accel = config.iswap_y_drive.acceleration * (yr_val / 2.0)

    dy = abs(Y_target - self._iswap_state.current_y)
    t = self._calculate_time(dy, speed_mm, accel)
    await anyio.sleep(t)
    self._iswap_state.current_y = Y_target

  async def _simulate_iswap_move_z(self, kwargs: dict[str, Any]):
    self._check_iswap_installed()
    za_val = int(kwargs["za"])
    Z_finger = self.iswap_z_drive_increment_to_mm(za_val, self.iswap_information.z_mm_per_increment)
    Z_target = Z_finger + STARBackend.iswap_rotation_drive_z_offset_above_finger_mm

    zv_val = int(kwargs["zv"])
    speed_mm = self.iswap_z_drive_increment_to_mm(zv_val, self.iswap_information.z_mm_per_increment)

    zr_val = int(kwargs["zr"])
    accel_mm = self.iswap_z_drive_increment_to_mm(
      zr_val * 1000, self.iswap_information.z_mm_per_increment
    )

    dz = abs(Z_target - self._iswap_state.current_z)
    t = self._calculate_time(dz, speed_mm, accel_mm)
    await anyio.sleep(t)
    self._iswap_state.current_z = Z_target

  async def _simulate_iswap_move_joints(self, kwargs: dict[str, Any]):
    self._check_iswap_installed()
    wa_val = int(kwargs["wa"])
    ta_val = int(kwargs["ta"])

    wv_val = int(kwargs["wv"])
    wr_val = int(kwargs["wr"])
    tv_val = int(kwargs["tv"])
    tr_val = int(kwargs["tr"])

    W_target = wa_val * self.iswap_information.rotation_deg_per_increment
    T_target = ta_val * self.iswap_information.wrist_deg_per_increment

    dw = abs(W_target - self._iswap_state.current_w)
    dt = abs(T_target - self._iswap_state.current_t)

    speed_w = wv_val * self.iswap_information.rotation_deg_per_increment
    accel_w = wr_val * _FW_ACCEL_SCALE * self.iswap_information.rotation_deg_per_increment

    speed_t = tv_val * self.iswap_information.wrist_deg_per_increment
    accel_t = tr_val * _FW_ACCEL_SCALE * self.iswap_information.wrist_deg_per_increment

    tw = self._calculate_time(dw, speed_w, accel_w)
    tt = self._calculate_time(dt, speed_t, accel_t)

    await anyio.sleep(max(tw, tt))
    self._iswap_state.current_w = W_target
    self._iswap_state.current_t = T_target

  async def _simulate_iswap_rotate_wrist_predefined(self, kwargs: dict[str, Any]):
    self._check_iswap_installed()
    config = self.config
    tp_val = int(kwargs["tp"])
    orientation = STARBackend.WristDriveOrientation(tp_val)
    T_target_incr = self.iswap_information.wrist_drive_predefined_increments[orientation]
    T_target = T_target_incr * self.iswap_information.wrist_deg_per_increment

    dt = abs(T_target - self._iswap_state.current_t)

    tt = self._calculate_time(
      dt,
      config.iswap_wrist_drive.speed,
      config.iswap_wrist_drive.acceleration,
    )
    await anyio.sleep(tt)
    self._iswap_state.current_t = T_target

  async def _simulate_iswap_rotate_predefined(self, kwargs: dict[str, Any]):
    self._check_iswap_installed()
    pd_val = int(kwargs["pd"])
    rotation_val = pd_val // 10  # 1: left, 2: front 3: right
    grip_val = pd_val % 10  # 1: front, 2: right, 3: back, 4: left

    # 1. Quarters arithmetic (0: arm faces to x+ = right)
    rotation_quarter = (rotation_val - 3) % 4
    grip_quarter = (grip_val - 4) % 4  # grip_val=LEFT means the arm faces right)
    wrist_quarter = (grip_quarter - rotation_quarter) % 4

    # 2. Map to standard orientations
    rotation_orientation = {
      2: STARBackend.RotationDriveOrientation.LEFT,
      3: STARBackend.RotationDriveOrientation.FRONT,
      0: STARBackend.RotationDriveOrientation.RIGHT,
    }[rotation_quarter]

    wrist_orientation = {
      0: STARBackend.WristDriveOrientation.STRAIGHT,
      1: STARBackend.WristDriveOrientation.LEFT,
      2: STARBackend.WristDriveOrientation.REVERSE,
      3: STARBackend.WristDriveOrientation.RIGHT,
    }[wrist_quarter]

    # 3. Look up exact target angles from iswap_information predefined stops
    W_target_incr = self.iswap_information.rotation_drive_predefined_increments[
      rotation_orientation
    ]
    W_target = W_target_incr * self.iswap_information.rotation_deg_per_increment

    T_target_incr = self.iswap_information.wrist_drive_predefined_increments[wrist_orientation]
    T_target = T_target_incr * self.iswap_information.wrist_deg_per_increment

    wv_val = int(kwargs["wv"])
    wr_val = int(kwargs["wr"])
    tv_val = int(kwargs["tv"])
    tr_val = int(kwargs["tr"])

    dw = abs(W_target - self._iswap_state.current_w)
    dt = abs(T_target - self._iswap_state.current_t)

    speed_w = wv_val * self.iswap_information.rotation_deg_per_increment
    accel_w = wr_val * _FW_ACCEL_SCALE * self.iswap_information.rotation_deg_per_increment

    speed_t = tv_val * self.iswap_information.wrist_deg_per_increment
    accel_t = tr_val * _FW_ACCEL_SCALE * self.iswap_information.wrist_deg_per_increment

    tw = self._calculate_time(dw, speed_w, accel_w)
    tt = self._calculate_time(dt, speed_t, accel_t)

    await anyio.sleep(max(tw, tt))
    self._iswap_state.current_w = W_target
    self._iswap_state.current_t = T_target

  async def _simulate_iswap_plate_action(
    self,
    kwargs: dict[str, Any],
    is_get: bool,
  ):
    self._check_iswap_installed()
    config = self.config
    x_pos = int(kwargs["xs"]) * _FW_STEP
    x_dir = int(kwargs["xd"])
    x_t = x_pos * (-1 if x_dir == 1 else 1)

    y_pos = int(kwargs["yj"]) * _FW_STEP
    y_dir = int(kwargs["yd"])
    y_t = y_pos * (-1 if y_dir == 1 else 1)

    z_pos = int(kwargs["zj"]) * _FW_STEP
    z_dir = int(kwargs["zd"])
    z_t = z_pos * (-1 if z_dir == 1 else 1)

    grip_direction = int(kwargs["gr"])
    Z_traverse = (
      int(kwargs["th"]) * _FW_STEP + STARBackend.iswap_rotation_drive_z_offset_above_finger_mm
    )
    Z_end = (
      int(kwargs["te"]) * _FW_STEP  # codespell:ignore te
      + STARBackend.iswap_rotation_drive_z_offset_above_finger_mm
    )

    L1 = self.iswap_information.link_1_length
    L2 = self.iswap_information.link_2_length
    T_straight = self._iswap_t_straight
    kg = self.iswap_information.rotation_drive_x_offset

    X_b_min = 90.0 - kg
    X_b_max = 1350.0 - kg

    # Look up stop angles from iswap_information predefined stops table
    rot_stops = self.iswap_information.rotation_drive_predefined_increments
    wrist_stops = self.iswap_information.wrist_drive_predefined_increments
    rot_scale = self.iswap_information.rotation_deg_per_increment
    wrist_scale = self.iswap_information.wrist_deg_per_increment

    W_front = rot_stops[STARBackend.RotationDriveOrientation.FRONT] * rot_scale
    W_right = rot_stops[STARBackend.RotationDriveOrientation.RIGHT] * rot_scale
    W_left = rot_stops[STARBackend.RotationDriveOrientation.LEFT] * rot_scale

    T_left = wrist_stops[STARBackend.WristDriveOrientation.LEFT] * wrist_scale
    T_right = wrist_stops[STARBackend.WristDriveOrientation.RIGHT] * wrist_scale
    T_reverse = wrist_stops[STARBackend.WristDriveOrientation.REVERSE] * wrist_scale

    # Solve the target W and T joint angles based on physical iSWAP SCARA kinematics.
    # CRITICAL PHYSICAL SAFETY CONSTRAINTS (Hamilton STAR Real Hardware Mechanics):
    # 1. Straight-wrist configurations (30cm reach) are NOT preferred because they pose a severe
    #    collision risk with deck elements. The hardware chooses the "REVERSED" wrist
    #    (folded back on itself) where the wrist is bent by 180 deg (T_reverse), minimizing the reach profile.
    # 2. For FRONT (1), LEFT (4), and RIGHT (2), the hardware strictly uses the compact REVERSED wrist (T_reverse) stops.
    # 3. For BACK (3) (points FRONT, 3 quarters), a 180 deg REVERSED fold would require the W drive
    #    to point BACK, which is physically impossible (no BACK stop exists). Therefore, it supports
    #    TWO redundant 90 deg bent paths (Option A: Right/Right and Option B: Left/Left) and dynamically
    #    selects between them based on the carriage gantry X-rail physical limits.
    match grip_direction:
      case 1:
        # Grip FRONT (1): Approaches from FRONT (-Y), points BACK (1 quarter).
        # Uses the compact REVERSED wrist (T_reverse) with Link 1 pointing FRONT (W_front).
        W_target = W_front
        T_target = T_reverse
      case 2:
        # Grip RIGHT (2): Approaches from RIGHT (+X), points LEFT (2 quarters).
        # Uses the compact REVERSED wrist (T_reverse) with Link 1 pointing RIGHT (W_right).
        W_target = W_right
        T_target = T_reverse
      case 3:
        # Grip BACK (3): Approaches from BACK (+Y), points FRONT (3 quarters).
        # Supports two redundant 90 deg bent choices:
        # - Option A (Right-handed): Link 1 RIGHT (W_right), Wrist RIGHT (T_right)
        # - Option B (Left-handed): Link 1 LEFT (W_left), Wrist LEFT (T_left)
        # The hardware dynamically selects based on X-rail travel limits.
        W_target = W_right
        T_target = T_right

        # Test temporary gantry target position for safety limits
        alpha_1 = W_target - 90.0
        alpha_2 = alpha_1 + (T_target - T_straight)
        alpha_1_rad = math.radians(alpha_1)
        alpha_2_rad = math.radians(alpha_2)
        X_b_target_test = x_t - L1 * math.cos(alpha_1_rad) - L2 * math.cos(alpha_2_rad)

        if not (X_b_min <= X_b_target_test <= X_b_max):
          # Fall back to Option B (Left-handed)
          W_target = W_left
          T_target = T_left
      case 4:
        # Grip LEFT (4): Approaches from LEFT (-X), points RIGHT (0 quarters).
        # Uses the compact REVERSED wrist (T_reverse) with Link 1 pointing LEFT (W_left).
        W_target = W_left
        T_target = T_reverse
      case _:
        raise ValueError(f"Invalid grip_direction: {grip_direction}")

    # Solve the generic IK for final gantry carriage target positions
    alpha_1 = W_target - 90.0
    alpha_2 = alpha_1 + (T_target - T_straight)
    alpha_1_rad = math.radians(alpha_1)
    alpha_2_rad = math.radians(alpha_2)

    X_b_target = x_t - L1 * math.cos(alpha_1_rad) - L2 * math.cos(alpha_2_rad)
    Y_b_target = y_t - L1 * math.sin(alpha_1_rad) - L2 * math.sin(alpha_2_rad)

    if not (X_b_min <= X_b_target <= X_b_max):
      raise ValueError(f"Target X is unreachable: X_arm={X_b_target + kg} mm")
    Y_b_min = self.extended_conf.left_arm_min_y_position
    Y_b_max = self.iswap_information.rotation_drive_y_max
    if not (Y_b_min <= Y_b_target <= Y_b_max):
      raise ValueError(f"Target Y is unreachable: Y_rotation={Y_b_target} mm")

    Z_b_target = z_t + STARBackend.iswap_rotation_drive_z_offset_above_finger_mm

    if self._iswap_state.current_z < Z_traverse:
      dz = Z_traverse - self._iswap_state.current_z
      t = self._calculate_time(dz, config.iswap_z_drive.speed, config.iswap_z_drive.acceleration)
      await anyio.sleep(t)
      self._iswap_state.current_z = Z_traverse

    dx = abs(X_b_target - (self._current_x - kg))
    dy = abs(Y_b_target - self._iswap_state.current_y)
    dw = abs(W_target - self._iswap_state.current_w)
    dt = abs(T_target - self._iswap_state.current_t)

    tx = self._calculate_time(dx, config.x_drive.speed, config.x_drive.acceleration)
    ty = self._calculate_time(dy, config.iswap_y_drive.speed, config.iswap_y_drive.acceleration)
    tw = self._calculate_time(
      dw,
      config.iswap_rotation_drive.speed,
      config.iswap_rotation_drive.acceleration,
    )
    tt = self._calculate_time(
      dt,
      config.iswap_wrist_drive.speed,
      config.iswap_wrist_drive.acceleration,
    )

    await anyio.sleep(max(tx, ty, tw, tt))
    self._current_x = X_b_target + kg
    self._iswap_state.current_y = Y_b_target
    self._iswap_state.current_w = W_target
    self._iswap_state.current_t = T_target

    dz = abs(self._iswap_state.current_z - Z_b_target)
    t = self._calculate_time(dz, config.iswap_z_drive.speed, config.iswap_z_drive.acceleration)
    await anyio.sleep(t)
    self._iswap_state.current_z = Z_b_target

    await anyio.sleep(config.iswap_gripper_op_duration)
    if is_get:
      gb_val = int(kwargs["gb"])
      self._iswap_state.current_g = gb_val * _FW_STEP
      self._iswap_state.holding_resource = True
    else:
      go_val = int(kwargs["go"])
      self._iswap_state.current_g = go_val * _FW_STEP
      self._iswap_state.holding_resource = False

    dz = abs(Z_end - self._iswap_state.current_z)
    t = self._calculate_time(dz, config.iswap_z_drive.speed, config.iswap_z_drive.acceleration)
    await anyio.sleep(t)
    self._iswap_state.current_z = Z_end

    self._iswap_parked = False

  async def _simulate_free_iswap_y_range(self, kwargs: dict[str, Any]):
    config = self.config
    target_y = [0.0] * self.num_channels
    min_y = self.extended_conf.left_arm_min_y_position

    # Calculate target packed Y positions from front to back
    target_y[self.num_channels - 1] = min_y
    for i in range(self.num_channels - 2, -1, -1):
      target_y[i] = target_y[i + 1] + self._channels_minimum_y_spacing[i]

    # Calculate travel times
    max_t = 0.0
    for i in range(self.num_channels):
      dy = abs(target_y[i] - self.channels[i].current_y)
      dz = abs(config.z_safety_height - self.channels[i].current_z)

      ty = self._calculate_time(dy, config.pip_y_drive.speed, config.pip_y_drive.acceleration)
      tz = self._calculate_time(dz, config.pip_z_drive.speed, config.pip_z_drive.acceleration)

      max_t = max(max_t, ty + tz)

    await anyio.sleep(max_t)

    # Update states
    for i in range(self.num_channels):
      self.channels[i].current_y = target_y[i]
      self.channels[i].current_z = config.z_safety_height
