import asyncio
import datetime
import enum
import functools
import logging
import re
import sys
import warnings
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import (
  Any,
  Awaitable,
  Callable,
  Coroutine,
  Dict,
  List,
  Literal,
  Optional,
  Sequence,
  Tuple,
  TypedDict,
  TypeVar,
  Union,
  cast,
)

if sys.version_info < (3, 10):
  from typing_extensions import Concatenate, ParamSpec
else:
  from typing import Concatenate, ParamSpec

from typing import TYPE_CHECKING

from pylabrobot import audio
from pylabrobot.hamilton.liquid_handlers.star.pip_backend import STARPIPBackend

if TYPE_CHECKING:
  from pylabrobot.hamilton.liquid_handlers.star.autoload import STARAutoload
  from pylabrobot.hamilton.liquid_handlers.star.cover import STARCover
  from pylabrobot.hamilton.liquid_handlers.star.head96_backend import STARHead96Backend
  from pylabrobot.hamilton.liquid_handlers.star.iswap import iSWAPBackend
  from pylabrobot.hamilton.liquid_handlers.star.wash_station import STARWashStation
  from pylabrobot.hamilton.liquid_handlers.star.x_arm import STARXArm
from pylabrobot.hamilton.liquid_handlers.star.errors import (
  CommandSyntaxError,  # noqa: F401  (re-exported for STAR_tests)
  HamiltonNoTipError,  # noqa: F401  (re-exported for STAR_tests)
  HardwareError,  # noqa: F401  (re-exported for STAR_tests)
  STARFirmwareError,
  UnknownHamiltonError,  # noqa: F401  (re-exported for STAR_tests)
  convert_star_firmware_error_to_plr_error,
  star_firmware_string_to_error,
)
from pylabrobot.hamilton.liquid_handlers.star.fw_parsing import parse_star_fw_string
from pylabrobot.hamilton.liquid_handlers.star.pip_channel import (
  PressureLLDMode as _NewPressureLLDMode,
)
from pylabrobot.legacy.liquid_handling.backends.hamilton.base import (
  HamiltonLiquidHandler,
)
from pylabrobot.legacy.liquid_handling.backends.hamilton.planning import group_by_x_batch_by_xy
from pylabrobot.legacy.liquid_handling.channel_positioning import (
  MIN_SPACING_EDGE,
  get_wide_single_resource_liquid_op_offsets,
)
from pylabrobot.legacy.liquid_handling.liquid_classes.hamilton import (
  HamiltonLiquidClass,
  get_star_liquid_class,
)
from pylabrobot.legacy.liquid_handling.standard import (
  Drop,
  DropTipRack,
  GripDirection,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  PipettingOp,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import (
  Carrier,
  Container,
  Coordinate,
  Plate,
  Resource,
  Tip,
  TipRack,
  Well,
)
from pylabrobot.resources.barcode import Barcode, Barcode1DSymbology
from pylabrobot.resources.hamilton import (
  HamiltonTip,
  TipDropMethod,
  TipPickupMethod,
  TipSize,
)
from pylabrobot.resources.hamilton.hamilton_decks import (
  HamiltonCoreGrippers,
  rails_for_x_coordinate,
)
from pylabrobot.resources.liquid import Liquid
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.trash import Trash

T = TypeVar("T")

logger = logging.getLogger("pylabrobot")

_P = ParamSpec("_P")
_R = TypeVar("_R")


def need_iswap_parked(
  method: Callable[Concatenate["STARBackend", _P], Coroutine[Any, Any, _R]],
) -> Callable[Concatenate["STARBackend", _P], Coroutine[Any, Any, _R]]:
  """Ensure that the iSWAP is in parked position before running command.

  If the iSWAP is not parked, it get's parked before running the command.
  """

  @functools.wraps(method)
  async def wrapper(self: "STARBackend", *args, **kwargs):
    await self.driver.ensure_iswap_parked()
    return await method(self, *args, **kwargs)

  return wrapper


def _requires_head96(
  method: Callable[Concatenate["STARBackend", _P], Coroutine[Any, Any, _R]],
) -> Callable[Concatenate["STARBackend", _P], Coroutine[Any, Any, _R]]:
  """Ensure that a 96-head is installed before running the command."""

  @functools.wraps(method)
  async def wrapper(self: "STARBackend", *args, **kwargs):
    if not self.extended_conf.left_x_drive.core_96_head_installed:
      raise RuntimeError(
        "This command requires a 96-head, but none is installed. "
        "Check your instrument configuration."
      )
    return await method(self, *args, **kwargs)

  return wrapper


def _convert_immersion_depth(
  immersion_depth: Optional[List[float]],
  immersion_depth_direction: Optional[List[int]],
) -> Optional[List[float]]:
  """Convert legacy (unsigned depth + direction flag) to new (signed depth).

  New API: positive = go deeper, negative = go up.
  Legacy: immersion_depth is unsigned, direction 0 = deeper, 1 = up.
  """
  if immersion_depth is None:
    return None
  if immersion_depth_direction is None:
    return immersion_depth  # already correct sign convention
  return [
    d * (-1 if direction == 1 else 1)
    for d, direction in zip(immersion_depth, immersion_depth_direction)
  ]


def _dispensing_mode_for_op(empty: bool, jet: bool, blow_out: bool) -> int:
  """from docs:
  0 = Partial volume in jet mode
  1 = Blow out in jet mode, called "empty" in the VENUS liquid editor
  2 = Partial volume at surface
  3 = Blow out at surface, called "empty" in the VENUS liquid editor
  4 = Empty tip at fix position
  """

  if empty:
    return 4
  if jet:
    return 1 if blow_out else 0
  else:
    return 3 if blow_out else 2


@dataclass
class DriveConfiguration:
  """Configuration for an X drive (left or right).

  Combines byte 1 (xl/xr) and byte 2 (xn/xo) into a single object.
  Note: the installed modules on left and right drives must be different.
  """

  pip_installed: bool = False
  iswap_installed: bool = False
  core_96_head_installed: bool = False
  nano_pipettor_installed: bool = False
  dispensing_head_384_installed: bool = False
  xl_channels_installed: bool = False
  tube_gripper_installed: bool = False
  imaging_channel_installed: bool = False
  robotic_channel_installed: bool = False


@dataclass
class MachineConfiguration:
  """Response from RM (Request Machine Configuration) command [SFCO.0035]."""

  # kb byte (configuration data 1)
  pip_type_1000ul: bool = False
  """Bit 0: PIP Type. False = 300ul, True = 1000ul."""
  kb_iswap_installed: bool = False
  """Bit 1: ISWAP. False = none, True = installed."""
  main_front_cover_monitoring_installed: bool = False
  """Bit 2: Main front cover monitoring. False = none, True = installed."""
  auto_load_installed: bool = False
  """Bit 3: Auto load. False = none, True = installed."""
  wash_station_1_installed: bool = False
  """Bit 4: Wash station 1. False = none, True = installed."""
  wash_station_2_installed: bool = False
  """Bit 5: Wash station 2. False = none, True = installed."""
  temp_controlled_carrier_1_installed: bool = False
  """Bit 6: Temperature controlled carrier 1. False = none, True = installed."""
  temp_controlled_carrier_2_installed: bool = False
  """Bit 7: Temperature controlled carrier 2. False = none, True = installed."""

  num_pip_channels: int = 0
  """Number of PIP channels (kp). Range: 0..16."""


@dataclass
class ExtendedConfiguration:
  """Response from QM (Request Extended Configuration) command.

  This command returns the full instrument configuration matching the AK
  (Set Instrument Configuration) [SFCO.0026] parameter set.
  """

  # ka (configuration data 2, 24-bit)
  left_x_drive_large: bool = False
  """Bit 0: Left X drive. False = small, True = large."""
  ka_core_96_head_installed: bool = False
  """Bit 1: CoRe 96 Head. False = none, True = installed."""
  right_x_drive_large: bool = False
  """Bit 2: Right X drive. False = small, True = large."""
  pump_station_1_installed: bool = False
  """Bit 3: Pump station 1. False = none, True = installed."""
  pump_station_2_installed: bool = False
  """Bit 4: Pump station 2. False = none, True = installed."""
  wash_station_1_type_cr: bool = False
  """Bit 5: Type wash station 1. False = G3, True = CR."""
  wash_station_2_type_cr: bool = False
  """Bit 6: Type wash station 2. False = G3, True = CR."""
  left_cover_installed: bool = False
  """Bit 7: Left cover. False = none, True = installed."""
  right_cover_installed: bool = False
  """Bit 8: Right cover. False = none, True = installed."""
  additional_front_cover_monitoring_installed: bool = False
  """Bit 9: Additional front cover monitoring. False = none, True = installed."""
  pump_station_3_installed: bool = False
  """Bit 10: Pump station 3. False = none, True = installed."""
  multi_channel_nano_pipettor_installed: bool = False
  """Bit 11: Multi channel nano pipettor. False = none, True = installed."""
  dispensing_head_384_installed: bool = False
  """Bit 12: 384 dispensing head. False = none, True = installed."""
  xl_channels_installed: bool = False
  """Bit 13: XL channels. False = none, True = installed."""
  tube_gripper_installed: bool = False
  """Bit 14: Tube gripper. False = none, True = installed."""
  waste_direction_left: bool = False
  """Bit 15: Waste direction. False = right, True = left."""
  iswap_gripper_wide: bool = False
  """Bit 16: iSWAP gripper size. False = small, True = wide."""
  additional_channel_nano_pipettor_installed: bool = False
  """Bit 17: Additional channel nano pipettor. False = none, True = installed."""
  imaging_channel_installed: bool = False
  """Bit 18: Imaging channel. False = none, True = installed."""
  robotic_channel_installed: bool = False
  """Bit 19: Robotic channel. False = none, True = installed."""
  channel_order_ox_first: bool = False
  """Bit 20: Channel order. False = XL first, True = OX first."""
  x0_interface_ham_can: bool = False
  """Bit 21: X0 interface. False = other, True = Ham CAN."""
  park_heads_with_iswap_off: bool = False
  """Bit 22: Park heads with iSWAP. False = on, True = off."""

  # ke (configuration data 3, 32-bit)
  configuration_data_3: int = 0
  """Raw configuration data 3 (ke, 32-bit). Bit definitions are undocumented."""

  instrument_size_slots: int = 54
  """Instrument size in slots, X range (xt). Default: 54."""
  auto_load_size_slots: int = 54
  """Auto load size in slots (xa). Default: 54."""
  tip_waste_x_position: float = 1340.0
  """Tip waste X-position [mm] (xw). Default: 1340.0."""
  left_x_drive: DriveConfiguration = field(default_factory=DriveConfiguration)
  """Left X drive configuration (xl + xn)."""
  right_x_drive: DriveConfiguration = field(default_factory=DriveConfiguration)
  """Right X drive configuration (xr + xo)."""
  min_iswap_collision_free_position: float = 350.0
  """Minimal iSWAP collision free position for direct X access [mm] (xm). Default: 350.0."""
  max_iswap_collision_free_position: float = 1140.0
  """Maximal iSWAP collision free position for direct X access [mm] (xx). Default: 1140.0."""
  left_x_arm_width: float = 370.0
  """Width of left X arm [mm] (xu). Default: 370.0."""
  right_x_arm_width: float = 370.0
  """Width of right X arm [mm] (xv). Default: 370.0."""
  num_xl_channels: int = 0
  """Number of XL channels (kc). Range: 0..8."""
  num_robotic_channels: int = 0
  """Number of Robotic channels (kr). Range: 0..8."""
  min_raster_pitch_pip_channels: float = 9.0
  """Minimal raster pitch of PIP channels [mm] (ys). Default: 9.0."""
  min_raster_pitch_xl_channels: float = 36.0
  """Minimal raster pitch of XL channels [mm] (kl). Default: 36.0."""
  min_raster_pitch_robotic_channels: float = 36.0
  """Minimal raster pitch of Robotic channels [mm] (km). Default: 36.0."""
  pip_maximal_y_position: float = 606.5
  """PIP maximal Y position [mm] (ym). Default: 606.5."""
  left_arm_min_y_position: float = 6.0
  """Left arm minimal Y position [mm] (yu). Default: 6.0."""
  right_arm_min_y_position: float = 6.0
  """Right arm minimal Y position [mm] (yx). Default: 6.0."""


@dataclass
class Head96Information:
  """Information about the installed 96-head."""

  StopDiscType = Literal["core_i", "core_ii"]
  InstrumentType = Literal["legacy", "FM-STAR"]
  HeadType = Literal["Low volume head", "High volume head", "96 head II", "96 head TADM", "unknown"]

  fw_version: datetime.date
  supports_clot_monitoring_clld: bool
  stop_disc_type: StopDiscType
  instrument_type: InstrumentType
  head_type: HeadType


class STARBackend(HamiltonLiquidHandler):
  """Interface for the Hamilton STARBackend."""

  PIP_X_MIN_WITH_LEFT_SIDE_PANEL: float = 320.0
  HEAD96_X_MIN_WITH_LEFT_SIDE_PANEL: float = 0.0

  def __init__(
    self,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
    left_side_panel_installed: bool = False,
  ):
    """Create a new STAR interface.

    Args:
      device_address: the USB device address of the Hamilton STARBackend. Only useful if using more than
        one Hamilton machine over USB.
      serial_number: the serial number of the Hamilton STARBackend. Only useful if using more than one
        Hamilton machine over USB.
      packet_read_timeout: timeout in seconds for reading a single packet.
      read_timeout: timeout in seconds for reading a full response.
      write_timeout: timeout in seconds for writing a command.
      left_side_panel_installed: if True, restrict PIP channels to x >= 320mm and
        the 96-head to x >= 0mm to prevent collisions with the left side panel.
    """

    super().__init__(
      device_address=device_address,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      id_product=0x8000,
      serial_number=serial_number,
    )

    from pylabrobot.hamilton.liquid_handlers.star.driver import STARDriver

    # Deck arrives via set_deck() (legacy flow), so construct the driver without one
    # and attach it in set_deck(). STARDriver.setup() asserts deck is set.
    self.driver = STARDriver(
      deck=None,  # type: ignore[arg-type]
      device_address=device_address,
      serial_number=serial_number,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      left_side_panel_installed=left_side_panel_installed,
    )

    self.left_side_panel_installed = left_side_panel_installed
    self._machine_conf: Optional[MachineConfiguration] = None

    self._num_channels: Optional[int] = None
    self._channels_minimum_y_spacing: List[float] = [9.0] * 8
    self._core_parked: Optional[bool] = None
    self._extended_conf: Optional[ExtendedConfiguration] = None
    self.core_adjustment = Coordinate.zero()
    self._unsafe = UnSafe(self)

    self._iswap_version: Optional[str] = None  # loaded lazily

    self._default_1d_symbology: Barcode1DSymbology = "Code 128 (Subset B and C)"

    self._setup_done = False

  @property
  def left_x_arm(self):
    return self.driver.left_x_arm

  @property
  def iswap(self):
    return self.driver.iswap

  @property
  def _pip(self) -> STARPIPBackend:
    """Typed access to the STAR PIP backend."""
    return self.driver.pip  # type: ignore[return-value]

  @property
  def _iswap(self) -> "iSWAPBackend":
    """Typed access to the iSWAP backend (asserts not None)."""
    assert self.driver.iswap is not None, "iSWAP is not installed"
    return self.driver.iswap

  @property
  def _left_x_arm(self) -> "STARXArm":
    """Typed access to the left X arm (asserts not None)."""
    assert self.driver.left_x_arm is not None, "Left X arm is not available"
    return self.driver.left_x_arm

  @property
  def _autoload(self) -> "STARAutoload":
    """Typed access to the autoload subsystem (asserts not None)."""
    assert self.driver.autoload is not None, "Autoload is not installed"
    return self.driver.autoload

  @property
  def _wash_station(self) -> "STARWashStation":
    """Typed access to the wash station (asserts not None)."""
    assert self.driver.wash_station is not None, "Wash station is not installed"
    return self.driver.wash_station

  @property
  def _star_head96(self) -> "STARHead96Backend":
    """Typed access to the Head96 backend (asserts not None)."""
    assert self.driver.head96 is not None, "96-head is not installed"
    return self.driver.head96  # type: ignore[return-value]

  @property
  def _cover(self) -> "STARCover":
    """Typed access to the cover (asserts not None)."""
    assert self.driver.cover is not None, "Cover is not available"
    return self.driver.cover

  @property
  def _write_and_read_command(self):
    return self.driver._write_and_read_command

  @_write_and_read_command.setter
  def _write_and_read_command(self, value):
    self.driver._write_and_read_command = value  # type: ignore[method-assign]

  def _min_spacing_between(self, i: int, j: int) -> float:
    """Return the firmware-safe minimum Y spacing between channels *i* and *j*.

    Uses max() of both channels' spacings for firmware safety (conservative).
    For adjacent channels, ceiling-rounded to 0.1mm.
    For non-adjacent channels, the sum of all intermediate adjacent-pair spacings.
    """
    lo, hi = min(i, j), max(i, j)
    if hi - lo == 1:
      import math

      spacing = max(self._channels_minimum_y_spacing[lo], self._channels_minimum_y_spacing[hi])
      return math.ceil(spacing * 10) / 10
    return sum(self._min_spacing_between(k, k + 1) for k in range(lo, hi))

  def _ops_to_fw_positions(
    self, ops: Sequence[PipettingOp], use_channels: List[int]
  ) -> Tuple[List[int], List[int], List[bool]]:
    x_positions, y_positions, channels_involved = super()._ops_to_fw_positions(ops, use_channels)
    if self.left_side_panel_installed:
      min_x = round(self.PIP_X_MIN_WITH_LEFT_SIDE_PANEL * 10)
      for x, involved in zip(x_positions, channels_involved):
        if involved and x < min_x:
          raise ValueError(
            f"PIP channel x={x / 10}mm is below the minimum "
            f"{self.PIP_X_MIN_WITH_LEFT_SIDE_PANEL}mm (left side panel is installed)"
          )
    return x_positions, y_positions, channels_involved

  @property
  def machine_conf(self) -> MachineConfiguration:
    """Machine configuration."""
    if self._machine_conf is None:
      raise RuntimeError("has not loaded machine_conf, forgot to call `setup`?")
    return self._machine_conf

  @property
  def autoload_installed(self) -> bool:
    """Deprecated. Use `machine_conf.auto_load_installed`."""
    warnings.warn(
      "autoload_installed is deprecated. Use `machine_conf.auto_load_installed` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.machine_conf.auto_load_installed

  @property
  def iswap_installed(self) -> bool:
    """Deprecated. Use `extended_conf.left_x_drive.iswap_installed`."""
    warnings.warn(
      "iswap_installed is deprecated. Use `extended_conf.left_x_drive.iswap_installed` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.extended_conf.left_x_drive.iswap_installed

  @property
  def core96_head_installed(self) -> bool:
    """Deprecated. Use `extended_conf.left_x_drive.core_96_head_installed`."""
    warnings.warn(
      "core96_head_installed is deprecated. Use "
      "`extended_conf.left_x_drive.core_96_head_installed` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.extended_conf.left_x_drive.core_96_head_installed

  @property
  def num_arms(self) -> int:
    return 1 if self.extended_conf.left_x_drive.iswap_installed else 0

  @property
  def head96_installed(self) -> Optional[bool]:
    return self.extended_conf.left_x_drive.core_96_head_installed

  @property
  def unsafe(self) -> "UnSafe":
    """Actions that have a higher risk of damaging the robot. Use with care!"""
    return self._unsafe

  @property
  def num_channels(self) -> int:
    """The number of pipette channels present on the robot."""
    if self._num_channels is None:
      raise RuntimeError("has not loaded num_channels, forgot to call `setup`?")
    return self._num_channels

  def set_minimum_traversal_height(self, traversal_height: float):
    raise NotImplementedError(
      "set_minimum_traversal_height is deprecated. use set_minimum_channel_traversal_height or "
      "set_minimum_iswap_traversal_height instead."
    )

  def set_minimum_channel_traversal_height(self, traversal_height: float):
    """Set the minimum traversal height for the pip channels.

    This refers to the bottom of the pipetting channel when no tip is present, or the bottom of the
    tip when a tip is present. This value will be used as the default value for the
    `minimal_traverse_height_at_begin_of_command` and `minimal_height_at_command_end` parameters
    unless they are explicitly set.
    """

    assert 0 < traversal_height < 285, "Traversal height must be between 0 and 285 mm"

    self._pip.traversal_height = traversal_height

  def set_minimum_iswap_traversal_height(self, traversal_height: float):
    """Set the minimum traversal height for the iswap."""

    assert 0 < traversal_height < 285, "Traversal height must be between 0 and 285 mm"

    self._iswap.traversal_height = traversal_height

  @contextmanager
  def iswap_minimum_traversal_height(self, traversal_height: float):
    """Deprecated: use ``self._iswap.use_traversal_height()``."""
    with self._iswap.use_traversal_height(traversal_height):
      yield

  @property
  def iswap_traversal_height(self) -> float:
    return self._iswap.traversal_height

  @property
  def module_id_length(self):
    return 2

  @property
  def extended_conf(self) -> ExtendedConfiguration:
    """Extended configuration."""
    if self._extended_conf is None:
      raise RuntimeError("has not loaded extended_conf, forgot to call `setup`?")
    return self._extended_conf

  @property
  def iswap_parked(self) -> bool:
    if self.driver.iswap is not None:
      return self._iswap.parked
    return False

  @property
  def core_parked(self) -> bool:
    return self._core_parked is True

  async def get_iswap_version(self) -> str:
    """Lazily load the iSWAP version. Use cached value if available."""
    if self._iswap_version is None:
      self._iswap_version = await self.request_iswap_version()
    return self._iswap_version

  async def request_pip_channel_version(self, channel: int) -> str:
    """Deprecated: use ``star.pip.backend.channels[n].request_firmware_version()``."""
    pip_channel = self._pip_channels[channel]
    resp = await pip_channel.send_command(
      module=pip_channel.module_id,
      command="RF",
      fmt="rf" + "&" * 17,
    )
    return str(resp["rf"])

  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    """Get the id from a firmware response."""
    parsed = parse_star_fw_string(resp, "id####")
    if "id" in parsed and parsed["id"] is not None:
      return int(parsed["id"])
    return None

  def check_fw_string_error(self, resp: str):
    """Raise an error if the firmware response is an error response.

    Raises:
      ValueError: if the format string is incompatible with the response.
      HamiltonException: if the response contains an error.
    """

    # Parse errors.
    module = resp[:2]
    if module == "C0":
      # C0 sends errors as er##/##. P1 raises errors as er## where the first group is the error
      # code, and the second group is the trace information.
      # Beyond that, specific errors may be added for individual channels and modules. These
      # are formatted as P1##/## H0##/##, etc. These items are added programmatically as
      # named capturing groups to the regex.

      exp = r"er(?P<C0>[0-9]{2}/[0-9]{2})"
      for module in [
        "X0",
        "I0",
        "W1",
        "W2",
        "T1",
        "T2",
        "R0",
        "P1",
        "P2",
        "P3",
        "P4",
        "P5",
        "P6",
        "P7",
        "P8",
        "P9",
        "PA",
        "PB",
        "PC",
        "PD",
        "PE",
        "PF",
        "PG",
        "H0",
        "HW",
        "HU",
        "HV",
        "N0",
        "D0",
        "NP",
        "M1",
      ]:
        exp += f" ?(?:{module}(?P<{module}>[0-9]{{2}}/[0-9]{{2}}))?"
      errors = re.search(exp, resp)
    else:
      # Other modules send errors as er##, and do not contain slave errors.
      exp = f"er(?P<{module}>[0-9]{{2}})"
      errors = re.search(exp, resp)

    if errors is not None:
      # filter None elements
      errors_dict = {k: v for k, v in errors.groupdict().items() if v is not None}
      # filter 00 and 00/00 elements, which mean no error.
      errors_dict = {k: v for k, v in errors_dict.items() if v not in ["00", "00/00"]}

    has_error = not (errors is None or len(errors_dict) == 0)
    if has_error:
      he = star_firmware_string_to_error(error_code_dict=errors_dict, raw_response=resp)

      # If there is a faulty parameter error, request which parameter that is.
      for module_name, error in he.errors.items():
        if error.message == "Unknown parameter":
          # temp. disabled until we figure out how to handle async in parse response (the
          # background thread does not have an event loop, and I'm not sure if it should.)
          # vp = await self.send_command(module=error.raw_module, command="VP", fmt="vp&&")["vp"]
          # he[module_name].message += f" ({vp})"

          he.errors[
            module_name
          ].message += " (call lh.backend.request_name_of_last_faulty_parameter)"

      raise he

  def _parse_response(self, resp: str, fmt: str) -> dict:
    """Parse a response from the machine."""
    return parse_star_fw_string(resp, fmt)

  def _parse_firmware_version_datetime(self, fw_version: str) -> datetime.date:
    """Extract datetime from firmware version string.

    Args:
      fw_version: Firmware version string (e.g., "v2021.03.15" or "2023_Q2_v1.4")

    Returns:
      A datetime object representing the extracted date
    """

    # Prefer full date patterns like YYYY.MM.DD / YYYY_MM_DD / YYYY-MM-DD
    date_match = re.search(r"\b(20\d{2})[._-](\d{2})[._-](\d{2})\b", fw_version)
    if date_match:
      y, m, d = map(int, date_match.groups())
      return datetime.date(y, m, d)

    # Handle quarter formats like 2023_Q2 -> first day of the quarter
    q_match = re.search(r"\b(20\d{2})_Q([1-4])\b", fw_version, flags=re.IGNORECASE)
    if q_match:
      y = int(q_match.group(1))
      q = int(q_match.group(2))
      month = (q - 1) * 3 + 1
      return datetime.date(y, month, 1)

    # Fall back to year only -> Jan 1st of that year, or None
    year_match = re.search(r"\b(20\d{2})\b", fw_version)
    if year_match is None:
      raise ValueError(f"Could not parse year from firmware version string: '{fw_version}'")
    return datetime.date(int(year_match.group(1)), 1, 1)

  def set_deck(self, deck):
    super().set_deck(deck)
    self.driver.deck = deck  # type: ignore[assignment]

  async def setup(
    self,
    skip_instrument_initialization=False,
    skip_pip=False,
    skip_autoload=False,
    skip_iswap=False,
    skip_core96_head=False,
  ):
    """Creates a USB connection and finds read/write interfaces.

    Args:
      skip_autoload: if True, skip initializing the autoload module, if applicable.
      skip_iswap: if True, skip initializing the iSWAP module, if applicable.
      skip_core96_head: if True, skip initializing the CoRe 96 head module, if applicable.
    """

    # Let the driver own the USB connection and query machine config.
    await self.driver.setup()

    # Sync legacy state from driver.
    self.id_ = 0
    self._machine_conf = self.driver.machine_conf  # type: ignore[assignment]
    self._extended_conf = self.driver.extended_conf  # type: ignore[assignment]
    self._head96_information: Optional[Head96Information] = None

    initialized = await self.request_instrument_initialization_status()

    if not initialized:
      if not skip_instrument_initialization:
        logger.info("Running backend initialization procedure.")

        await self.pre_initialize_instrument()
    else:
      # pre_initialize only runs when the robot is not initialized
      # pre_initialize will move all channels to Z safety
      # so if we skip pre_initialize, we need to raise the channels ourselves
      await self.move_all_channels_in_z_safety()
      if self.extended_conf.left_x_drive.core_96_head_installed:
        await self.move_core_96_to_safe_position()

    tip_presences = await self.request_tip_presence()
    self._num_channels = len(tip_presences)

    async def set_up_pip():
      if (not initialized or any(tip_presences)) and not skip_pip:
        await self.initialize_pip()
      self._channels_minimum_y_spacing = await self.channels_request_y_minimum_spacing()

    async def set_up_autoload():
      if self.machine_conf.auto_load_installed and not skip_autoload:
        autoload_initialized = await self.request_autoload_initialization_status()
        if not autoload_initialized:
          await self.initialize_autoload()

        await self.park_autoload()

    async def set_up_iswap():
      if self.extended_conf.left_x_drive.iswap_installed and not skip_iswap:
        iswap_initialized = await self.request_iswap_initialization_status()
        if not iswap_initialized:
          await self.initialize_iswap()

        await self.park_iswap(
          minimum_traverse_height_at_beginning_of_a_command=int(self._iswap.traversal_height * 10)
        )

    async def set_up_core96_head():
      if self.extended_conf.left_x_drive.core_96_head_installed and not skip_core96_head:
        # Initialize 96-head
        core96_head_initialized = await self.request_core_96_head_initialization_status()
        if not core96_head_initialized:
          await self.initialize_core_96_head(
            trash96=self.deck.get_trash_area96(),
            z_position_at_the_command_end=self._pip.traversal_height,
          )

        # Cache firmware version and configuration for version-specific behavior
        fw_version = await self.head96_request_firmware_version()
        configuration_96head = await self._head96_request_configuration()
        head96_type = await self.head96_request_type()

        self._head96_information = Head96Information(
          fw_version=fw_version,
          supports_clot_monitoring_clld=bool(int(configuration_96head[0])),
          stop_disc_type="core_i" if configuration_96head[1] == "0" else "core_ii",
          instrument_type="legacy" if configuration_96head[2] == "0" else "FM-STAR",
          head_type=head96_type,
        )

    async def set_up_arm_modules():
      await set_up_pip()
      await set_up_iswap()
      await set_up_core96_head()

    await asyncio.gather(set_up_autoload(), set_up_arm_modules())

    # After setup, STAR will have thrown out anything mounted on the pipetting channels, including
    # the core grippers.
    self._core_parked = True

    self._pip_channels = self._pip.channels

    self._setup_done = True

  async def send_command(
    self,
    module,
    command,
    auto_id=True,
    tip_pattern=None,
    write_timeout=None,
    read_timeout=None,
    wait=True,
    fmt=None,
    **kwargs,
  ):
    return await self.driver.send_command(
      module=module,
      command=command,
      auto_id=auto_id,
      tip_pattern=tip_pattern,
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      wait=wait,
      fmt=fmt,
      **kwargs,
    )

  async def stop(self):
    await self.driver.stop()
    self._setup_done = False

  @property
  def setup_done(self) -> bool:
    return self._setup_done

  # ============== LiquidHandlerBackend methods ==============

  # # # # Single-Channel Pipette Commands # # # #

  # # # Machine Query (MEM-READ) Commands: Single-Channel # # #

  async def channel_request_y_minimum_spacing(self, channel_idx: int) -> float:
    """Request the minimum Y spacing for a given channel.

    Args:
      channel_idx: the channel index to query. (0-indexed)

    Returns:
      The minimum Y spacing in mm.
    """

    if not 0 <= channel_idx <= self.num_channels - 1:
      raise ValueError(
        f"channel_idx must be between 0 and {self.num_channels - 1}, got {channel_idx}."
      )

    resp = await self.send_command(
      module=self.channel_id(channel_idx),
      command="VY",
      fmt="yc### (n)",
    )
    return self.y_drive_increment_to_mm(resp["yc"][1])

  async def channels_request_y_minimum_spacing(self) -> List[float]:
    """Query the minimum Y spacing for all channels in parallel.

    Each channel is addressed on its own module (P1, P2, ...), so the queries
    can run concurrently.

    Returns:
      A list of exact (unrounded) minimum Y spacings in mm, one per channel,
      indexed by channel number.
    """
    return list(
      await asyncio.gather(
        *(
          self.channel_request_y_minimum_spacing(channel_idx=idx)
          for idx in range(self.num_channels)
        )
      )
    )

  def can_reach_position(self, channel_idx: int, position: Coordinate) -> bool:
    """Check if a position is reachable by a channel (center-based)."""
    if not (0 <= channel_idx < self.num_channels):
      raise ValueError(f"Channel {channel_idx} is out of range for this robot.")

    # frontmost channel can go to y=6, every channel behind it constrains its min Y
    spacings = self._channels_minimum_y_spacing
    min_y_pos = self.extended_conf.left_arm_min_y_position + sum(spacings[channel_idx + 1 :])
    if position.y < min_y_pos:
      return False

    # backmost channel max Y from config, every channel in front constrains its max Y
    max_y_pos = self.extended_conf.pip_maximal_y_position - sum(spacings[:channel_idx])
    if position.y > max_y_pos:
      return False

    return True

  def ensure_can_reach_position(
    self, use_channels: List[int], ops: Sequence[PipettingOp], op_name: str
  ):
    locs = [(op.resource.get_location_wrt(self.deck, y="c") + op.offset) for op in ops]
    cant_reach = [
      channel_idx
      for channel_idx, loc in zip(use_channels, locs)
      if not self.can_reach_position(channel_idx, loc)
    ]
    if len(cant_reach) > 0:
      raise ValueError(
        f"Channels {cant_reach} cannot reach their target positions in '{op_name}' operation.\n"
        "Robots with more than 8 channels have limited Y-axis reach per channel; they don't have random access to the full deck area.\n"
        "Try the operation with different channels or a different target position (i.e. different labware placement)."
      )

  class ChannelCycleCounts(TypedDict):
    tip_pick_up_cycles: int
    tip_discard_cycles: int
    aspiration_cycles: int
    dispensing_cycles: int

  async def channel_request_cycle_counts(self, channel_idx: int) -> ChannelCycleCounts:
    """Deprecated: use ``star.pip.backend.channels[n].request_cycle_counts()``."""
    return await self._pip_channels[channel_idx].request_cycle_counts()  # type: ignore[return-value]

  async def channels_request_cycle_counts(self) -> List[ChannelCycleCounts]:
    """Request cycle counters for all channels.

    Returns:
      A list of dicts (one per channel, ordered by channel index), each with keys
      ``tip_pick_up_cycles``, ``tip_discard_cycles``, ``aspiration_cycles``,
      and ``dispensing_cycles``.
    """

    return list(
      await asyncio.gather(
        *(self.channel_request_cycle_counts(channel_idx=idx) for idx in range(self.num_channels))
      )
    )

  # # # ACTION Commands # # #

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    begin_tip_pick_up_process: Optional[float] = None,
    end_tip_pick_up_process: Optional[float] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    pickup_method: Optional[TipPickupMethod] = None,
  ):
    """Deprecated: use ``star.pip.backend.pick_up_tips()``."""
    from pylabrobot.capabilities.liquid_handling.standard import Pickup as NewPickup

    PickUpTipsParams = self._pip.PickUpTipsParams

    new_ops = [NewPickup(resource=op.resource, offset=op.offset, tip=op.tip) for op in ops]
    params = PickUpTipsParams(
      minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command
      or self._pip.traversal_height,
      pickup_method=pickup_method,
      begin_tip_pick_up_process=begin_tip_pick_up_process,
      end_tip_pick_up_process=end_tip_pick_up_process,
    )
    return await self._pip.pick_up_tips(new_ops, use_channels, backend_params=params)

  async def drop_tips(
    self,
    ops: List[Drop],
    use_channels: List[int],
    drop_method: Optional[TipDropMethod] = None,
    begin_tip_deposit_process: Optional[float] = None,
    end_tip_deposit_process: Optional[float] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    z_position_at_end_of_a_command: Optional[float] = None,
  ):
    """Deprecated: use ``star.pip.backend.drop_tips()``."""
    from pylabrobot.capabilities.liquid_handling.standard import TipDrop as NewTipDrop

    DropTipsParams = self._pip.DropTipsParams

    new_ops = [NewTipDrop(resource=op.resource, offset=op.offset, tip=op.tip) for op in ops]  # type: ignore[arg-type]
    params = DropTipsParams(
      drop_method=drop_method,
      minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command
      or self._pip.traversal_height,
      z_position_at_end_of_a_command=z_position_at_end_of_a_command or self._pip.traversal_height,
      begin_tip_deposit_process=begin_tip_deposit_process,
      end_tip_deposit_process=end_tip_deposit_process,
    )
    return await self._pip.drop_tips(new_ops, use_channels, backend_params=params)

  def _assert_valid_resources(self, resources: Sequence[Resource]) -> None:
    """Assert that resources are in a valid location for pipetting."""
    for resource in resources:
      if resource.get_location_wrt(self.deck).z < 100:
        raise ValueError(
          f"Resource {resource} is too low: {resource.get_location_wrt(self.deck).z} < 100"
        )

  class LLDMode(enum.Enum):
    """Liquid level detection mode."""

    OFF = 0
    GAMMA = 1
    PRESSURE = 2
    DUAL = 3
    Z_TOUCH_OFF = 4

  class PressureLLDMode(enum.Enum):
    """Pressure liquid level detection mode."""

    LIQUID = 0
    FOAM = 1

  async def _move_to_traverse_height(
    self, channels: Optional[List[int]] = None, traverse_height: Optional[float] = None
  ):
    """Move channels to a specified traverse height, if given, otherwise move to full Z safety.

    Args:
      channels: Channels to move. If None, all channels are moved.
      traverse_height: Absolute Z position in mm. If None, move to full Z safety.
    """
    if traverse_height is None:
      await self.move_all_channels_in_z_safety()
    else:
      if channels is None:
        channels = list(range(self.num_channels))
      await self.position_channels_in_z_direction(
        {channel: traverse_height for channel in channels}
      )

  async def _probe_liquid_heights_batch(
    self,
    containers: List[Container],
    use_channels: List[int],
    lld_mode: LLDMode = LLDMode.GAMMA,
    search_speed: float = 10.0,
    n_replicates: int = 1,
  ) -> List[float]:
    """Helper for probe_liquid_heights that performs a single batch of liquid level detection using a set of channels.

    Assumes channels are moved to the appropriate traverse height before calling, and does not move channels after completion.
    """

    tip_lengths = [await self.request_tip_len_on_channel(channel_idx=idx) for idx in use_channels]

    detect_func: Callable[..., Any]
    if lld_mode == self.LLDMode.GAMMA:
      detect_func = self._move_z_drive_to_liquid_surface_using_clld
    else:
      detect_func = self._search_for_surface_using_plld

    # Compute Z search bounds for this batch
    batch_lowest_immers = [
      container.get_absolute_location("c", "c", "cavity_bottom").z
      + tip_len
      - self.DEFAULT_TIP_FITTING_DEPTH
      for container, tip_len in zip(containers, tip_lengths)
    ]
    batch_start_pos = [
      container.get_absolute_location("c", "c", "t").z
      + tip_len
      - self.DEFAULT_TIP_FITTING_DEPTH
      + 5
      for container, tip_len in zip(containers, tip_lengths)
    ]

    absolute_heights_measurements: Dict[int, List[Optional[float]]] = {
      idx: [] for idx in range(len(use_channels))
    }

    # Run n_replicates detection loop for this batch
    for _ in range(n_replicates):
      errors = await asyncio.gather(
        *[
          detect_func(
            channel_idx=channel,
            lowest_immers_pos=lip,
            start_pos_search=sps,
            channel_speed=search_speed,
          )
          for channel, lip, sps in zip(use_channels, batch_lowest_immers, batch_start_pos)
        ],
        return_exceptions=True,
      )

      # Get heights for ALL channels, handling failures for channels with no liquid
      current_absolute_liquid_heights = await self.request_pip_height_last_lld()
      for idx, (channel_idx, error) in enumerate(zip(use_channels, errors)):
        if isinstance(error, STARFirmwareError):
          error_msg = str(error).lower()
          if "no liquid level found" in error_msg or "no liquid was present" in error_msg:
            height = None
            msg = (
              f"Operation {idx} (channel {channel_idx}): No liquid detected. Could be because there is "
              f"no liquid in container {containers[idx].name} or liquid level "
              f"is too low."
            )
            if lld_mode == self.LLDMode.GAMMA:
              msg += " Consider using pressure-based LLD if liquid is believed to exist."
            logger.warning(msg)
          else:
            raise error
        elif isinstance(error, Exception):
          raise error
        else:
          height = current_absolute_liquid_heights[channel_idx]
        absolute_heights_measurements[idx].append(height)

    # Compute liquid heights relative to well bottom
    relative_to_well: List[float] = []
    inconsistent_ops: List[str] = []

    for idx, container in enumerate(containers):
      measurements = absolute_heights_measurements[idx]
      valid = [m for m in measurements if m is not None]
      cavity_bottom = container.get_absolute_location("c", "c", "cavity_bottom").z

      if len(valid) == 0:
        relative_to_well.append(0.0)
      elif len(valid) == len(measurements):
        relative_to_well.append(sum(valid) / len(valid) - cavity_bottom)
      else:
        inconsistent_ops.append(
          f"Operation {idx}: {len(valid)}/{len(measurements)} replicates detected liquid"
        )

    if inconsistent_ops:
      raise RuntimeError(
        "Inconsistent liquid detection across replicates. "
        "This may indicate liquid levels near the detection limit:\n" + "\n".join(inconsistent_ops)
      )

    return relative_to_well

  def _get_maximum_minimum_spacing_between_channels(self, use_channels: List[int]) -> float:
    """Get the maximum of the set of minimum spacing requirements between the channels being used"""
    sorted_channels = sorted(use_channels)
    max_channel_spacing = max(
      self._min_spacing_between(hi, lo) for hi, lo in zip(sorted_channels[1:], sorted_channels[:-1])
    )
    return max_channel_spacing

  def _compute_channels_in_resource_locations(
    self,
    resources: Sequence[Resource],
    use_channels: List[int],
    offsets: Optional[List[Coordinate]],
  ) -> List[Coordinate]:
    """Compute absolute locations of resources with given offsets."""

    # If no offset is provided but we can fit all channels inside a single resource,
    # compute the offsets to make that happen using wide spacing.
    if offsets is None:
      if len(set(resources)) == 1 and len(use_channels) == len(set(use_channels)):
        container_size_y = resources[0].get_absolute_size_y()
        # For non-consecutive channels (e.g. [0,1,2,5,6,7]), we must account for
        # phantom intermediate channels (3,4) that physically exist between them.
        # Compute offsets for the full channel range (min to max), then pick only
        # the offsets corresponding to the actual channels being used.
        max_channel_spacing = self._get_maximum_minimum_spacing_between_channels(use_channels)
        num_channels_in_span = max(use_channels) - min(use_channels) + 1
        min_required = MIN_SPACING_EDGE * 2 + (num_channels_in_span - 1) * max_channel_spacing
        if container_size_y >= min_required:
          all_offsets = get_wide_single_resource_liquid_op_offsets(
            resource=resources[0],
            num_channels=num_channels_in_span,
            min_spacing=max_channel_spacing,
          )
          min_ch = min(use_channels)
          offsets = [all_offsets[ch - min_ch] for ch in use_channels]
        # else: container too small to fit all channels — fall back to center offsets.
        # Y sub-batching will serialize channels that can't coexist.

    offsets = offsets or [Coordinate.zero()] * len(resources)

    # Compute positions for all resources
    resource_locations = [
      resource.get_location_wrt(self.deck, x="c", y="c", z="b") + offset
      for resource, offset in zip(resources, offsets)
    ]

    return resource_locations

  async def execute_batched(  # TODO: any hamilton liquid handler
    self,
    func: Callable[[List[int]], Awaitable[None]],
    resources: List[Container],
    use_channels: Optional[List[int]] = None,
    resource_offsets: Optional[List[Coordinate]] = None,
    min_traverse_height_during_command: Optional[float] = None,
  ):
    if use_channels is None:
      use_channels = list(range(len(resources)))

    # precompute locations and batches
    locations = self._compute_channels_in_resource_locations(
      resources, use_channels, resource_offsets
    )
    x_batches = group_by_x_batch_by_xy(
      locations=locations,
      use_channels=use_channels,
      min_spacing_between_channels=self._min_spacing_between,
    )

    # loop over batches. keep track of channels used in previous batch to ensure they are raised to traverse height before next batch
    prev_channels: Optional[List[int]] = None

    try:
      for x_value, x_batch in x_batches.items():
        if prev_channels is not None:
          await self._move_to_traverse_height(
            channels=prev_channels, traverse_height=min_traverse_height_during_command
          )
        await self.move_channel_x(0, x_value)

        for y_batch in x_batch:
          if prev_channels is not None:
            await self._move_to_traverse_height(
              channels=prev_channels, traverse_height=min_traverse_height_during_command
            )
          await self.position_channels_in_y_direction(
            {use_channels[idx]: locations[idx].y for idx in y_batch},
          )

          await func(y_batch)

          prev_channels = [use_channels[idx] for idx in y_batch]
    except Exception:
      await self.move_all_channels_in_z_safety()
      raise
    except BaseException:
      await self.move_all_channels_in_z_safety()
      raise

  async def probe_liquid_heights(
    self,
    containers: List[Container],
    use_channels: Optional[List[int]] = None,
    resource_offsets: Optional[List[Coordinate]] = None,
    lld_mode: LLDMode = LLDMode.GAMMA,
    search_speed: float = 10.0,
    n_replicates: int = 1,
    # Traverse height parameters (None = full Z safety, float = absolute Z position in mm)
    min_traverse_height_at_beginning_of_command: Optional[float] = None,
    min_traverse_height_during_command: Optional[float] = None,
    z_position_at_end_of_command: Optional[float] = None,
    # Deprecated
    move_to_z_safety_after: Optional[bool] = None,
  ) -> List[float]:
    """Probe liquid surface heights in containers using liquid level detection.

    Performs capacitive or pressure-based liquid level detection (LLD) by moving channels to
    container positions and sensing the liquid surface. Heights are measured from the bottom
    of each container's cavity.

    Args:
      containers: List of Container objects to probe, one per channel.
      use_channels: Channel indices to use for probing (0-indexed).
      resource_offsets: Optional XYZ offsets from container centers. Auto-calculated for single
        containers with odd channel counts to avoid center dividers. Defaults to container centers.
      lld_mode: Detection mode - LLDMode(1) for capacitive, LLDMode(2) for pressure-based.
        Defaults to capacitive.
      search_speed: Z-axis search speed in mm/s. Default 10.0 mm/s.
      n_replicates: Number of measurements per channel. Default 1.
      min_traverse_height_at_beginning_of_command: Absolute Z height (mm) to move involved
        channels to before the first batch. None (default) uses full Z safety.
      min_traverse_height_during_command: Absolute Z height (mm) to move involved channels to
        between batches (X groups and Y sub-batches). None (default) uses full Z safety.
      z_position_at_end_of_command: Absolute Z height (mm) to move involved channels to after
        probing. None (default) uses full Z safety.

    Returns:
      Mean of measured liquid heights for each container (mm from cavity bottom).

    Raises:
      RuntimeError: If channels lack tips.

    Notes:
      - All specified channels must have tips attached
      - Containers at different X positions are probed in sequential groups (single X carriage)
      - For single containers with no-go zones, Y-offsets are computed to avoid
        obstructed regions (e.g. center dividers in troughs)
    """

    if move_to_z_safety_after is not None:
      warnings.warn(
        "The 'move_to_z_safety_after' parameter is deprecated and will be removed in a future release. "
        "Use 'z_position_at_end_of_command' with an appropriate Z height instead. If not set, "
        "the default behavior will be to move to full Z safety after the command.",
        DeprecationWarning,
      )

    # Validate parameters.
    if use_channels is None:
      use_channels = list(range(len(containers)))
    if len(use_channels) == 0:
      raise ValueError("use_channels must not be empty.")
    if not all(0 <= ch < self.num_channels for ch in use_channels):
      raise ValueError(
        f"All use_channels must be integers in range [0, {self.num_channels - 1}], "
        f"got {use_channels}."
      )

    if lld_mode not in {self.LLDMode.GAMMA, self.LLDMode.PRESSURE}:
      raise ValueError(f"LLDMode must be 1 (capacitive) or 2 (pressure-based), is {lld_mode}")

    if not len(containers) == len(use_channels):
      raise ValueError(
        "Length of containers and use_channels must match, "
        f"got lengths {len(containers)}, {len(use_channels)}."
      )

    # Validate resource_offsets length (if provided) to avoid silent truncation in downstream zips.
    if resource_offsets is not None and len(resource_offsets) != len(containers):
      raise ValueError(
        "Length of resource_offsets must match the length of containers and use_channels, "
        f"got lengths {len(resource_offsets)} (resource_offsets) and "
        f"{len(containers)} (containers/use_channels)."
      )
    # Make sure we have tips on all channels and know their lengths
    tip_presence = await self.request_tip_presence()
    if not all(tip_presence[idx] for idx in use_channels):
      raise RuntimeError("All specified channels must have tips attached.")

    # Move channels to traverse height
    await self._move_to_traverse_height(
      channels=use_channels, traverse_height=min_traverse_height_at_beginning_of_command
    )

    result_by_operation: Dict[int, float] = {}

    async def func(batch: List[int]):
      liquid_heights = await self._probe_liquid_heights_batch(
        containers=[containers[idx] for idx in batch],
        use_channels=[use_channels[idx] for idx in batch],
        lld_mode=lld_mode,
        search_speed=search_speed,
        n_replicates=n_replicates,
      )
      for idx, height in zip(batch, liquid_heights):
        result_by_operation[idx] = height

    await self.execute_batched(
      func=func,
      resources=containers,
      use_channels=use_channels,
      resource_offsets=resource_offsets,
      min_traverse_height_during_command=min_traverse_height_during_command,
    )

    await self._move_to_traverse_height(
      channels=use_channels,
      traverse_height=z_position_at_end_of_command,
    )

    return [result_by_operation[idx] for idx in range(len(containers))]

  async def probe_liquid_volumes(
    self,
    containers: List[Container],
    use_channels: List[int],
    resource_offsets: Optional[List[Coordinate]] = None,
    lld_mode: LLDMode = LLDMode.GAMMA,
    search_speed: float = 10.0,
    n_replicates: int = 3,
    move_to_z_safety_after: bool = True,
  ) -> List[float]:
    """Probe liquid volumes in containers by measuring heights and converting to volumes.

    Performs liquid level detection to measure surface heights, then converts heights to
    volumes using each container's geometric model. This is a convenience wrapper around
    probe_liquid_heights that handles the height-to-volume conversion.

    Args:
      containers: List of Container objects to probe, one per channel. All must support height-to-volume conversion via compute_volume_from_height().
      use_channels: Channel indices to use for probing (0-indexed).
      resource_offsets: Optional XYZ offsets from container centers. Auto-calculated for single containers with odd channel counts. Defaults to container centers.
      lld_mode: Detection mode - LLDMode(1) for capacitive, LLDMode(2) for pressure-based.  Defaults to capacitive.
      search_speed: Z-axis search speed in mm/s. Default 10.0 mm/s.
      n_replicates: Number of measurements per channel. Default 3.
      move_to_z_safety_after: Whether to move channels to safe Z height after probing. Default True.

    Returns:
      Volumes in each container (uL).

    Raises:
      ValueError: If any container doesn't support height-to-volume conversion.

    Notes:
    - Delegates all motion, LLD, validation, and safety logic to probe_liquid_heights
    - All containers must support height-volume functions. Volume calculation uses Container.compute_volume_from_height()
    """

    if any(not resource.supports_compute_height_volume_functions() for resource in containers):
      raise ValueError(
        "probe_liquid_volumes can only be used with containers that support height<->volume functions."
      )

    liquid_heights = await self.probe_liquid_heights(
      containers=containers,
      use_channels=use_channels,
      resource_offsets=resource_offsets,
      lld_mode=lld_mode,
      search_speed=search_speed,
      n_replicates=n_replicates,
      move_to_z_safety_after=move_to_z_safety_after,
    )

    return [
      container.compute_volume_from_height(height)
      for container, height in zip(containers, liquid_heights)
    ]

  # # # Granular channel control methods # # #

  DISPENSING_DRIVE_VOL_LIMIT_BOTTOM = -45  # vol TODO: confirm with others
  DISPENSING_DRIVE_VOL_LIMIT_TOP = 1_250  # vol

  async def channel_dispensing_drive_request_position(self, channel_idx: int) -> float:
    """Deprecated: use ``star.pip.backend.channels[n].request_dispensing_drive_position()``."""
    return await self._pip_channels[channel_idx].request_dispensing_drive_position()

  async def channel_dispensing_drive_move_to_volume_position(
    self,
    channel_idx: int,
    vol: float,
    flow_rate: float = 200.0,  # uL/sec
    acceleration: float = 3000.0,  # uL/sec**2,
    current_limit: int = 5,
  ):
    """Deprecated: use ``star.pip.backend.channels[n].move_dispensing_drive_to_position()``."""
    return await self._pip_channels[channel_idx].move_dispensing_drive_to_position(
      vol=vol,
      flow_rate=flow_rate,
      acceleration=acceleration,
      current_limit=current_limit,
    )

  async def empty_tip(
    self,
    channel_idx: int,
    vol: Optional[float] = None,
    flow_rate: float = 200.0,  # vol/sec
    acceleration: float = 3000.0,  # vol/sec**2,
    current_limit: int = 5,
    reset_dispensing_drive_after: bool = True,
  ):
    """Deprecated: use ``star.pip.backend.channels[n].empty_tip()``."""
    return await self._pip_channels[channel_idx].empty_tip(
      vol=vol,
      flow_rate=flow_rate,
      acceleration=acceleration,
      current_limit=current_limit,
      reset_dispensing_drive_after=reset_dispensing_drive_after,
    )

  async def empty_tips(
    self,
    channels: Optional[List[int]] = None,
    vol: Optional[float] = None,
    flow_rate: float = 200.0,  # vol/sec
    acceleration: float = 3000.0,  # vol/sec**2,
    current_limit: int = 5,
    reset_dispensing_drive_after: bool = True,
  ):
    """Empty multiple tips by moving to `vol` (default bottom limit), optionally returning plunger position to 0.

    Args:
      channels: List of channel indices to empty (0-indexed). If None, all channels with tips mounted are emptied.
      vol: Target volume position to move the dispensing drive piston to (uL). If None, defaults to bottom limit.
      flow_rate: Speed of the movement (uL/sec). Default is 200.0 uL/sec.
      acceleration: Acceleration of the movement (uL/sec**2). Default is 3000.0 uL/sec**2.
      current_limit: Current limit for the drive (1-7). Default is 5.
      reset_dispensing_drive_after: Whether to return the dispensing drive to 0 after emptying. Default is True
    """

    if channels is None:
      channel_occupancy = await self.request_tip_presence()
      channels = [ch for ch, occupied in enumerate(channel_occupancy) if occupied]
    else:
      # Validate that all provided channels are within valid range
      if not all(0 <= ch < self.num_channels for ch in channels):
        raise ValueError(
          f"channel_idx must be between 0 and {self.num_channels - 1}, got {channels}"
        )

    await asyncio.gather(
      *[
        self.empty_tip(
          channel_idx=ch,
          vol=vol,
          flow_rate=flow_rate,
          acceleration=acceleration,
          current_limit=current_limit,
          reset_dispensing_drive_after=reset_dispensing_drive_after,
        )
        for ch in channels
      ]
    )

  # # # Channel Liquid Handling Commands # # #

  async def aspirate(
    self,
    ops: List[SingleChannelAspiration],
    use_channels: List[int],
    jet: Optional[List[bool]] = None,
    blow_out: Optional[List[bool]] = None,
    lld_search_height: Optional[List[float]] = None,
    clot_detection_height: Optional[List[float]] = None,
    pull_out_distance_transport_air: Optional[List[float]] = None,
    second_section_height: Optional[List[float]] = None,
    second_section_ratio: Optional[List[float]] = None,
    minimum_height: Optional[List[float]] = None,
    immersion_depth: Optional[List[float]] = None,
    surface_following_distance: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    pre_wetting_volume: Optional[List[float]] = None,
    lld_mode: Optional[List[LLDMode]] = None,
    gamma_lld_sensitivity: Optional[List[int]] = None,
    dp_lld_sensitivity: Optional[List[int]] = None,
    aspirate_position_above_z_touch_off: Optional[List[float]] = None,
    detection_height_difference_for_dual_lld: Optional[List[float]] = None,
    swap_speed: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    mix_position_from_liquid_surface: Optional[List[float]] = None,
    mix_surface_following_distance: Optional[List[float]] = None,
    limit_curve_index: Optional[List[int]] = None,
    use_2nd_section_aspiration: Optional[List[bool]] = None,
    retract_height_over_2nd_section_to_empty_tip: Optional[List[float]] = None,
    dispensation_speed_during_emptying_tip: Optional[List[float]] = None,
    dosing_drive_speed_during_2nd_section_search: Optional[List[float]] = None,
    z_drive_speed_during_2nd_section_search: Optional[List[float]] = None,
    cup_upper_edge: Optional[List[float]] = None,
    ratio_liquid_rise_to_tip_deep_in: Optional[List[int]] = None,
    immersion_depth_2nd_section: Optional[List[float]] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    min_z_endpos: Optional[float] = None,
    liquid_surface_no_lld: Optional[List[float]] = None,
    # PLR:
    probe_liquid_height: bool = False,
    auto_surface_following_distance: bool = False,
    hamilton_liquid_classes: Optional[List[Optional[HamiltonLiquidClass]]] = None,
    disable_volume_correction: Optional[List[bool]] = None,
    # remove >2026-01
    mix_volume: Optional[List[float]] = None,
    mix_cycles: Optional[List[int]] = None,
    mix_speed: Optional[List[float]] = None,
    immersion_depth_direction: Optional[List[int]] = None,
    liquid_surfaces_no_lld: Optional[List[float]] = None,
  ):
    """Deprecated: use ``star.pip.backend.aspirate()``."""

    from pylabrobot.capabilities.liquid_handling.standard import Aspiration as NewAspiration

    AspirateParams = self._pip.AspirateParams
    from pylabrobot.hamilton.liquid_handlers.star.pip_backend import LLDMode as NewLLDMode

    # # # TODO: delete > 2026-01 # # #
    if mix_volume is not None or mix_cycles is not None or mix_speed is not None:
      raise NotImplementedError(
        "Mixing through backend kwargs is deprecated. Use the `mix` parameter of LiquidHandler.aspirate instead. "
        "https://docs.pylabrobot.org/user_guide/00_liquid-handling/mixing.html"
      )
    if immersion_depth_direction is not None:
      warnings.warn(
        "The immersion_depth_direction parameter is deprecated and will be removed in the future. "
        "Use positive values for immersion_depth to move into the liquid, and negative values to move "
        "out of the liquid.",
        DeprecationWarning,
      )
    if liquid_surfaces_no_lld is not None:
      warnings.warn(
        "The liquid_surfaces_no_lld parameter is deprecated and will be removed in the future. "
        "Use liquid_surface_no_lld instead.",
        DeprecationWarning,
      )
      liquid_surface_no_lld = liquid_surface_no_lld or liquid_surfaces_no_lld
    if ratio_liquid_rise_to_tip_deep_in is not None:
      warnings.warn(
        "ratio_liquid_rise_to_tip_deep_in is deprecated.", DeprecationWarning, stacklevel=2
      )
    if immersion_depth_2nd_section is not None:
      warnings.warn("immersion_depth_2nd_section is deprecated.", DeprecationWarning, stacklevel=2)
    # # # delete # # #

    # Convert lld_mode enums from legacy to new
    new_lld_mode = None
    if lld_mode is not None:
      new_lld_mode = [NewLLDMode(m.value) for m in lld_mode]

    new_ops = [
      NewAspiration(
        resource=op.resource,
        offset=op.offset,
        tip=op.tip,
        volume=op.volume,
        flow_rate=op.flow_rate,
        liquid_height=op.liquid_height,
        blow_out_air_volume=op.blow_out_air_volume,
        mix=op.mix,  # type: ignore[arg-type]
      )
      for op in ops
    ]

    params = AspirateParams(
      hamilton_liquid_classes=hamilton_liquid_classes,
      disable_volume_correction=disable_volume_correction,
      jet=jet,
      blow_out=blow_out,
      lld_search_height=lld_search_height,
      clot_detection_height=clot_detection_height,
      pull_out_distance_transport_air=pull_out_distance_transport_air,
      second_section_height=second_section_height,
      second_section_ratio=second_section_ratio,
      minimum_height=minimum_height,
      immersion_depth=_convert_immersion_depth(immersion_depth, immersion_depth_direction),
      surface_following_distance=surface_following_distance,
      transport_air_volume=transport_air_volume,
      pre_wetting_volume=pre_wetting_volume,
      lld_mode=new_lld_mode,
      gamma_lld_sensitivity=gamma_lld_sensitivity,
      dp_lld_sensitivity=dp_lld_sensitivity,
      aspirate_position_above_z_touch_off=aspirate_position_above_z_touch_off,
      detection_height_difference_for_dual_lld=detection_height_difference_for_dual_lld,
      swap_speed=swap_speed,
      settling_time=settling_time,
      mix_position_from_liquid_surface=mix_position_from_liquid_surface,
      mix_surface_following_distance=mix_surface_following_distance,
      limit_curve_index=limit_curve_index,
      minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command
      or self._pip.traversal_height,
      min_z_endpos=min_z_endpos or self._pip.traversal_height,
      liquid_surface_no_lld=liquid_surface_no_lld,
      use_2nd_section_aspiration=use_2nd_section_aspiration,
      retract_height_over_2nd_section_to_empty_tip=retract_height_over_2nd_section_to_empty_tip,
      dispensation_speed_during_emptying_tip=dispensation_speed_during_emptying_tip,
      dosing_drive_speed_during_2nd_section_search=dosing_drive_speed_during_2nd_section_search,
      z_drive_speed_during_2nd_section_search=z_drive_speed_during_2nd_section_search,
      cup_upper_edge=cup_upper_edge,
      probe_liquid_height=probe_liquid_height,
      auto_surface_following_distance=auto_surface_following_distance,
    )

    return await self._pip.aspirate(new_ops, use_channels, backend_params=params)

  async def dispense(
    self,
    ops: List[SingleChannelDispense],
    use_channels: List[int],
    lld_search_height: Optional[List[float]] = None,
    liquid_surface_no_lld: Optional[List[float]] = None,
    pull_out_distance_transport_air: Optional[List[float]] = None,
    second_section_height: Optional[List[float]] = None,
    second_section_ratio: Optional[List[float]] = None,
    minimum_height: Optional[List[float]] = None,
    immersion_depth: Optional[List[float]] = None,
    surface_following_distance: Optional[List[float]] = None,
    cut_off_speed: Optional[List[float]] = None,
    stop_back_volume: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    lld_mode: Optional[List[LLDMode]] = None,
    dispense_position_above_z_touch_off: Optional[List[float]] = None,
    gamma_lld_sensitivity: Optional[List[int]] = None,
    dp_lld_sensitivity: Optional[List[int]] = None,
    swap_speed: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    mix_position_from_liquid_surface: Optional[List[float]] = None,
    mix_surface_following_distance: Optional[List[float]] = None,
    limit_curve_index: Optional[List[int]] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[int] = None,
    min_z_endpos: Optional[float] = None,
    side_touch_off_distance: float = 0,
    jet: Optional[List[bool]] = None,
    blow_out: Optional[List[bool]] = None,  # "empty" in the VENUS liquid editor
    empty: Optional[List[bool]] = None,  # truly "empty", does not exist in liquid editor, dm4
    # PLR specific
    probe_liquid_height: bool = False,
    auto_surface_following_distance: bool = False,
    hamilton_liquid_classes: Optional[List[Optional[HamiltonLiquidClass]]] = None,
    disable_volume_correction: Optional[List[bool]] = None,
    # remove  in the future
    immersion_depth_direction: Optional[List[int]] = None,
    mix_volume: Optional[List[float]] = None,
    mix_cycles: Optional[List[int]] = None,
    mix_speed: Optional[List[float]] = None,
    dispensing_mode: Optional[List[int]] = None,
  ):
    """Deprecated: use ``star.pip.backend.dispense()``."""

    from pylabrobot.capabilities.liquid_handling.standard import Dispense as NewDispense

    DispenseParams = self._pip.DispenseParams
    from pylabrobot.hamilton.liquid_handlers.star.pip_backend import LLDMode as NewLLDMode

    # # # TODO: delete > 2026-01 # # #
    if mix_volume is not None or mix_cycles is not None or mix_speed is not None:
      raise NotImplementedError(
        "Mixing through backend kwargs is deprecated. Use the `mix` parameter of LiquidHandler.dispense instead. "
        "https://docs.pylabrobot.org/user_guide/00_liquid-handling/mixing.html"
      )
    if immersion_depth_direction is not None:
      warnings.warn(
        "The immersion_depth_direction parameter is deprecated and will be removed in the future. "
        "Use positive values for immersion_depth to move into the liquid, and negative values to move "
        "out of the liquid.",
        DeprecationWarning,
      )
    if dispensing_mode is not None:
      warnings.warn(
        "The dispensing_mode parameter is deprecated and will be removed in the future. "
        "Use the jet, blow_out and empty parameters instead.",
        DeprecationWarning,
      )
    # # # delete # # #

    new_lld_mode = None
    if lld_mode is not None:
      new_lld_mode = [NewLLDMode(m.value) for m in lld_mode]

    new_ops = [
      NewDispense(
        resource=op.resource,
        offset=op.offset,
        tip=op.tip,
        volume=op.volume,
        flow_rate=op.flow_rate,
        liquid_height=op.liquid_height,
        blow_out_air_volume=op.blow_out_air_volume,
        mix=op.mix,  # type: ignore[arg-type]
      )
      for op in ops
    ]

    params = DispenseParams(
      hamilton_liquid_classes=hamilton_liquid_classes,
      disable_volume_correction=disable_volume_correction,
      jet=jet,
      blow_out=blow_out,
      empty=empty,
      lld_search_height=lld_search_height,
      liquid_surface_no_lld=liquid_surface_no_lld,
      pull_out_distance_transport_air=pull_out_distance_transport_air,
      second_section_height=second_section_height,
      second_section_ratio=second_section_ratio,
      minimum_height=minimum_height,
      immersion_depth=_convert_immersion_depth(immersion_depth, immersion_depth_direction),
      surface_following_distance=surface_following_distance,
      cut_off_speed=cut_off_speed,
      stop_back_volume=stop_back_volume,
      transport_air_volume=transport_air_volume,
      lld_mode=new_lld_mode,
      side_touch_off_distance=side_touch_off_distance,
      dispense_position_above_z_touch_off=dispense_position_above_z_touch_off,
      gamma_lld_sensitivity=gamma_lld_sensitivity,
      dp_lld_sensitivity=dp_lld_sensitivity,
      swap_speed=swap_speed,
      settling_time=settling_time,
      mix_position_from_liquid_surface=mix_position_from_liquid_surface,
      mix_surface_following_distance=mix_surface_following_distance,
      limit_curve_index=limit_curve_index,
      minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command
      or self._pip.traversal_height,
      min_z_endpos=min_z_endpos or self._pip.traversal_height,
      probe_liquid_height=probe_liquid_height,
      auto_surface_following_distance=auto_surface_following_distance,
    )

    return await self._pip.dispense(new_ops, use_channels, backend_params=params)

  @_requires_head96
  async def pick_up_tips96(
    self,
    pickup: PickupTipRack,
    tip_pickup_method: Literal["from_rack", "from_waste", "full_blowout"] = "from_rack",
    minimum_height_command_end: Optional[float] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    experimental_alignment_tipspot_identifier: str = "A1",
  ):
    """Pick up tips using the 96 head.

    `tip_pickup_method` can be one of the following:
        - "from_rack": standard tip pickup from a tip rack. this moves the plunger all the way down before mounting tips.
        - "from_waste":
            1. it actually moves the plunger all the way up
            2. mounts tips
            3. moves up like 10mm
            4. moves plunger all the way down
            5. moves to traversal height (tips out of rack)
        - "full_blowout":
            1. it actually moves the plunger all the way up
            2. mounts tips
            3. moves to traversal height (tips out of rack)

    Args:
      pickup: The standard `PickupTipRack` operation.
      tip_pickup_method: The method to use for picking up tips. One of "from_rack", "from_waste", "full_blowout".
      minimum_height_command_end: The minimum height to move to at the end of the command.
      minimum_traverse_height_at_beginning_of_a_command: The minimum height to move to at the beginning of the command.
      experimental_alignment_tipspot_identifier: The tipspot to use for alignment with head's A1 channel. Defaults to "tipspot A1".  allowed range is A1 to H12.
    """

    if isinstance(tip_pickup_method, int):
      warnings.warn(
        "tip_pickup_method as int is deprecated and will be removed in the future. Use string literals instead.",
        DeprecationWarning,
      )
      tip_pickup_method = {0: "from_rack", 1: "from_waste", 2: "full_blowout"}[tip_pickup_method]

    if tip_pickup_method not in {"from_rack", "from_waste", "full_blowout"}:
      raise ValueError(f"Invalid tip_pickup_method: '{tip_pickup_method}'.")

    prototypical_tip = next((tip for tip in pickup.tips if tip is not None), None)
    if prototypical_tip is None:
      raise ValueError("No tips found in the tip rack.")
    if not isinstance(prototypical_tip, HamiltonTip):
      raise TypeError("Tip type must be HamiltonTip.")

    ttti = await self.get_or_assign_tip_type_index(prototypical_tip)

    tip_length = prototypical_tip.total_tip_length
    fitting_depth = prototypical_tip.fitting_depth
    tip_engage_height_from_tipspot = tip_length - fitting_depth

    # Adjust tip engage height based on tip size
    if prototypical_tip.tip_size == TipSize.LOW_VOLUME:
      tip_engage_height_from_tipspot += 2
    elif prototypical_tip.tip_size != TipSize.STANDARD_VOLUME:
      tip_engage_height_from_tipspot -= 2

    # Compute pickup Z
    alignment_tipspot = pickup.resource.get_item(experimental_alignment_tipspot_identifier)
    tip_spot_z = alignment_tipspot.get_location_wrt(self.deck).z + pickup.offset.z
    z_pickup_position = tip_spot_z + tip_engage_height_from_tipspot

    # Compute full position (used for x/y)
    pickup_position = (
      alignment_tipspot.get_location_wrt(self.deck) + alignment_tipspot.center() + pickup.offset
    )
    pickup_position.z = round(z_pickup_position, 2)

    self._check_96_position_legal(pickup_position, skip_z=True)

    if tip_pickup_method == "from_rack":
      # the STAR will not automatically move the dispensing drive down if it is still up
      # so we need to move it down here
      # see https://github.com/PyLabRobot/pylabrobot/pull/835
      lowest_dispensing_drive_height_no_tips = 218.19
      await self.head96_dispensing_drive_move_to_position(lowest_dispensing_drive_height_no_tips)

    try:
      await self.pick_up_tips_core96(
        x_position=abs(round(pickup_position.x * 10)),
        x_direction=0 if pickup_position.x >= 0 else 1,
        y_position=round(pickup_position.y * 10),
        tip_type_idx=ttti,
        tip_pickup_method={
          "from_rack": 0,
          "from_waste": 1,
          "full_blowout": 2,
        }[tip_pickup_method],
        z_deposit_position=round(pickup_position.z * 10),
        minimum_traverse_height_at_beginning_of_a_command=round(
          (minimum_traverse_height_at_beginning_of_a_command or self._pip.traversal_height) * 10
        ),
        minimum_height_command_end=round(
          (minimum_height_command_end or self._pip.traversal_height) * 10
        ),
      )
    except STARFirmwareError as e:
      if plr_e := convert_star_firmware_error_to_plr_error(e):
        raise plr_e from e
      raise e

  @_requires_head96
  async def drop_tips96(
    self,
    drop: DropTipRack,
    minimum_height_command_end: Optional[float] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    experimental_alignment_tipspot_identifier: str = "A1",
  ):
    """Drop tips from the 96 head."""

    if isinstance(drop.resource, TipRack):
      tip_spot_a1 = drop.resource.get_item(experimental_alignment_tipspot_identifier)
      position = tip_spot_a1.get_location_wrt(self.deck) + tip_spot_a1.center() + drop.offset
      tip_rack = tip_spot_a1.parent
      assert tip_rack is not None
      position.z = tip_rack.get_location_wrt(self.deck).z + 1.45
      # This should be the case for all normal hamilton tip carriers + racks
      # In the future, we might want to make this more flexible
      assert abs(position.z - 216.4) < 1e-6, f"z position must be 216.4, got {position.z}"
    else:
      position = self._position_96_head_in_resource(drop.resource) + drop.offset

    self._check_96_position_legal(position, skip_z=True)

    x_direction = 0 if position.x >= 0 else 1

    return await self.discard_tips_core96(
      x_position=abs(round(position.x * 10)),
      x_direction=x_direction,
      y_position=round(position.y * 10),
      z_deposit_position=round(position.z * 10),
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._pip.traversal_height) * 10
      ),
      minimum_height_command_end=round(
        (minimum_height_command_end or self._pip.traversal_height) * 10
      ),
    )

  @_requires_head96
  async def aspirate96(
    self,
    aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer],
    jet: bool = False,
    blow_out: bool = False,
    use_lld: bool = False,
    pull_out_distance_transport_air: float = 10,
    hlc: Optional[HamiltonLiquidClass] = None,
    aspiration_type: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    min_z_endpos: Optional[float] = None,
    lld_search_height: float = 199.9,
    minimum_height: Optional[float] = None,
    second_section_height: float = 3.2,
    second_section_ratio: float = 618.0,
    immersion_depth: float = 0,
    surface_following_distance: float = 0,
    transport_air_volume: float = 5.0,
    pre_wetting_volume: float = 5.0,
    gamma_lld_sensitivity: int = 1,
    swap_speed: float = 2.0,
    settling_time: float = 1.0,
    mix_position_from_liquid_surface: float = 0,
    mix_surface_following_distance: float = 0,
    limit_curve_index: int = 0,
    disable_volume_correction: bool = False,
    # Deprecated parameters, to be removed in future versions
    # rm: >2026-01
    liquid_surface_sink_distance_at_the_end_of_aspiration: float = 0,
    minimal_end_height: Optional[float] = None,
    air_transport_retract_dist: Optional[float] = None,
    maximum_immersion_depth: Optional[float] = None,
    surface_following_distance_during_mix: float = 0,
    tube_2nd_section_height_measured_from_zm: float = 3.2,
    tube_2nd_section_ratio: float = 618.0,
    immersion_depth_direction: Optional[int] = None,
    mix_volume: float = 0,
    mix_cycles: int = 0,
    speed_of_mix: float = 0.0,
  ):
    """Aspirate using the Core96 head.

    Args:
      aspiration: The aspiration to perform.

      jet: Whether to search for a jet liquid class. Only used on dispense.
      blow_out: Whether to use "blow out" dispense mode. Only used on dispense. Note that this is
        labelled as "empty" in the VENUS liquid editor, but "blow out" in the firmware
        documentation.
      hlc: The Hamiltonian liquid class to use. If `None`, the liquid class will be determined
        automatically.

      use_lld: If True, use gamma liquid level detection. If False, use liquid height.
      pull_out_distance_transport_air: The distance to retract after aspirating, in millimeters.

      aspiration_type: The type of aspiration to perform. (0 = simple; 1 = sequence; 2 = cup emptied)
      minimum_traverse_height_at_beginning_of_a_command: The minimum height to move to before
        starting the command.
      min_z_endpos: The minimum height to move to after the command.
      lld_search_height: The height to search for the liquid level.
      minimum_height: Minimum height (maximum immersion depth)
      second_section_height: Height of the second section.
      second_section_ratio: Ratio of [the diameter of the bottom * 10000] / [the diameter of the top]
      immersion_depth: The immersion depth above or below the liquid level.
      surface_following_distance: The distance to follow the liquid surface when aspirating.
      transport_air_volume: The volume of air to aspirate after the liquid.
      pre_wetting_volume: The volume of liquid to use for pre-wetting.
      gamma_lld_sensitivity: The sensitivity of the gamma liquid level detection.
      swap_speed: Swap speed (on leaving liquid) [1mm/s]. Must be between 0.3 and 160. Default 2.
      settling_time: The time to wait after aspirating.
      mix_position_from_liquid_surface: The position of the mix from the liquid surface.
      mix_surface_following_distance: The distance to follow the liquid surface during mix.
      limit_curve_index: The index of the limit curve to use.
      disable_volume_correction: Whether to disable liquid class volume correction.
    """

    # # # TODO: delete > 2026-01 # # #
    if mix_volume != 0 or mix_cycles != 0 or speed_of_mix != 0:
      raise NotImplementedError(
        "Mixing through backend kwargs is deprecated. Use the `mix` parameter of LiquidHandler.aspirate96 instead. "
        "https://docs.pylabrobot.org/user_guide/00_liquid-handling/mixing.html"
      )

    if immersion_depth_direction is not None:
      warnings.warn(
        "The immersion_depth_direction parameter is deprecated and will be removed in the future. "
        "Use positive values for immersion_depth to move into the liquid, and negative values to move "
        "out of the liquid.",
        DeprecationWarning,
      )

    if liquid_surface_sink_distance_at_the_end_of_aspiration != 0:
      surface_following_distance = liquid_surface_sink_distance_at_the_end_of_aspiration
      warnings.warn(
        "The liquid_surface_sink_distance_at_the_end_of_aspiration parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard surface_following_distance parameter instead.\n"
        "liquid_surface_sink_distance_at_the_end_of_aspiration currently superseding surface_following_distance.",
        DeprecationWarning,
      )

    if minimal_end_height is not None:
      min_z_endpos = minimal_end_height
      warnings.warn(
        "The minimal_end_height parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard min_z_endpos parameter instead.\n"
        "min_z_endpos currently superseding minimal_end_height.",
        DeprecationWarning,
      )

    if air_transport_retract_dist is not None:
      pull_out_distance_transport_air = air_transport_retract_dist
      warnings.warn(
        "The air_transport_retract_dist parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard pull_out_distance_transport_air parameter instead.\n"
        "pull_out_distance_transport_air currently superseding air_transport_retract_dist.",
        DeprecationWarning,
      )

    if maximum_immersion_depth is not None:
      minimum_height = maximum_immersion_depth
      warnings.warn(
        "The maximum_immersion_depth parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard minimum_height parameter instead.\n"
        "minimum_height currently superseding maximum_immersion_depth.",
        DeprecationWarning,
      )

    if surface_following_distance_during_mix != 0:
      mix_surface_following_distance = surface_following_distance_during_mix
      warnings.warn(
        "The surface_following_distance_during_mix parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard mix_surface_following_distance parameter instead.\n"
        "mix_surface_following_distance currently superseding surface_following_distance_during_mix.",
        DeprecationWarning,
      )

    if tube_2nd_section_height_measured_from_zm != 3.2:
      second_section_height = tube_2nd_section_height_measured_from_zm
      warnings.warn(
        "The tube_2nd_section_height_measured_from_zm parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_height parameter instead.\n"
        "second_section_height_measured_from_zm currently superseding second_section_height.",
        DeprecationWarning,
      )

    if tube_2nd_section_ratio != 618.0:
      second_section_ratio = tube_2nd_section_ratio
      warnings.warn(
        "The tube_2nd_section_ratio parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_ratio parameter instead.\n"
        "second_section_ratio currently superseding tube_2nd_section_ratio.",
        DeprecationWarning,
      )
    # # # delete # # #

    # get the first well and tip as representatives
    if isinstance(aspiration, MultiHeadAspirationPlate):
      plate = aspiration.wells[0].parent
      assert isinstance(plate, Plate), "MultiHeadAspirationPlate well parent must be a Plate"
      rot = plate.get_absolute_rotation()
      if rot.x % 360 != 0 or rot.y % 360 != 0:
        raise ValueError("Plate rotation around x or y is not supported for 96 head operations")
      if rot.z % 360 == 180:
        ref_well = aspiration.wells[-1]
      elif rot.z % 360 == 0:
        ref_well = aspiration.wells[0]
      else:
        raise ValueError("96 head only supports plate rotations of 0 or 180 degrees around z")

      position = (
        ref_well.get_location_wrt(self.deck)
        + ref_well.center()
        + Coordinate(z=ref_well.material_z_thickness)
        + aspiration.offset
      )
    else:
      x_width = (12 - 1) * 9  # 12 tips in a row, 9 mm between them
      y_width = (8 - 1) * 9  # 8 tips in a column, 9 mm between them
      x_position = (aspiration.container.get_absolute_size_x() - x_width) / 2
      y_position = (aspiration.container.get_absolute_size_y() - y_width) / 2 + y_width
      position = (
        aspiration.container.get_location_wrt(self.deck, z="cavity_bottom")
        + Coordinate(x=x_position, y=y_position)
        + aspiration.offset
      )
    self._check_96_position_legal(position, skip_z=True)

    tip = next(tip for tip in aspiration.tips if tip is not None)

    liquid_height = position.z + (aspiration.liquid_height or 0)

    hlc = hlc or get_star_liquid_class(
      tip_volume=tip.maximal_volume,
      is_core=True,
      is_tip=True,
      has_filter=tip.has_filter,
      # get last liquid in pipette, first to be dispensed
      liquid=Liquid.WATER,  # default to WATER
      jet=jet,
      blow_out=blow_out,  # see comment in method docstring
    )

    if disable_volume_correction or hlc is None:
      volume = aspiration.volume
    else:  # hlc is not None and not disable_volume_correction
      volume = hlc.compute_corrected_volume(aspiration.volume)

    # Get better default values from the HLC if available
    transport_air_volume = transport_air_volume or (
      hlc.aspiration_air_transport_volume if hlc is not None else 0
    )
    blow_out_air_volume = aspiration.blow_out_air_volume or (
      hlc.aspiration_blow_out_volume if hlc is not None else 0
    )
    flow_rate = aspiration.flow_rate or (hlc.aspiration_flow_rate if hlc is not None else 250)
    swap_speed = swap_speed or (hlc.aspiration_swap_speed if hlc is not None else 100)
    settling_time = settling_time or (hlc.aspiration_settling_time if hlc is not None else 0.5)

    x_direction = 0 if position.x >= 0 else 1
    return await self.aspirate_core_96(
      x_position=abs(round(position.x * 10)),
      x_direction=x_direction,
      y_positions=round(position.y * 10),
      aspiration_type=aspiration_type,
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._pip.traversal_height) * 10
      ),
      min_z_endpos=round((min_z_endpos or self._pip.traversal_height) * 10),
      lld_search_height=round(lld_search_height * 10),
      liquid_surface_no_lld=round(liquid_height * 10),
      pull_out_distance_transport_air=round(pull_out_distance_transport_air * 10),
      minimum_height=round((minimum_height or position.z) * 10),
      second_section_height=round(second_section_height * 10),
      second_section_ratio=round(second_section_ratio * 10),
      immersion_depth=round(immersion_depth * 10),
      immersion_depth_direction=immersion_depth_direction or (0 if (immersion_depth >= 0) else 1),
      surface_following_distance=round(surface_following_distance * 10),
      aspiration_volumes=round(volume * 10),
      aspiration_speed=round(flow_rate * 10),
      transport_air_volume=round(transport_air_volume * 10),
      blow_out_air_volume=round(blow_out_air_volume * 10),
      pre_wetting_volume=round(pre_wetting_volume * 10),
      lld_mode=int(use_lld),
      gamma_lld_sensitivity=gamma_lld_sensitivity,
      swap_speed=round(swap_speed * 10),
      settling_time=round(settling_time * 10),
      mix_volume=round(aspiration.mix.volume * 10) if aspiration.mix is not None else 0,
      mix_cycles=aspiration.mix.repetitions if aspiration.mix is not None else 0,
      mix_position_from_liquid_surface=round(mix_position_from_liquid_surface * 10),
      mix_surface_following_distance=round(mix_surface_following_distance * 10),
      speed_of_mix=round(aspiration.mix.flow_rate * 10) if aspiration.mix is not None else 1200,
      channel_pattern=[True] * 12 * 8,
      limit_curve_index=limit_curve_index,
      tadm_algorithm=False,
      recording_mode=0,
    )

  @_requires_head96
  async def dispense96(
    self,
    dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer],
    jet: bool = False,
    empty: bool = False,
    blow_out: bool = False,
    hlc: Optional[HamiltonLiquidClass] = None,
    pull_out_distance_transport_air=10,
    use_lld: bool = False,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    min_z_endpos: Optional[float] = None,
    lld_search_height: float = 199.9,
    minimum_height: Optional[float] = None,
    second_section_height: float = 3.2,
    second_section_ratio: float = 618.0,
    immersion_depth: float = 0,
    surface_following_distance: float = 0,
    transport_air_volume: float = 5.0,
    gamma_lld_sensitivity: int = 1,
    swap_speed: float = 2.0,
    settling_time: float = 0,
    mix_position_from_liquid_surface: float = 0,
    mix_surface_following_distance: float = 0,
    limit_curve_index: int = 0,
    cut_off_speed: float = 5.0,
    stop_back_volume: float = 0,
    disable_volume_correction: bool = False,
    # Deprecated parameters, to be removed in future versions
    # rm: >2026-01
    liquid_surface_sink_distance_at_the_end_of_dispense: float = 0,  # surface_following_distance!
    maximum_immersion_depth: Optional[float] = None,
    minimal_end_height: Optional[float] = None,
    mixing_position_from_liquid_surface: float = 0,
    surface_following_distance_during_mixing: float = 0,
    air_transport_retract_dist=10,
    tube_2nd_section_ratio: float = 618.0,
    tube_2nd_section_height_measured_from_zm: float = 3.2,
    immersion_depth_direction: Optional[int] = None,
    mixing_volume: float = 0,
    mixing_cycles: int = 0,
    speed_of_mixing: float = 0.0,
    dispense_mode: Optional[int] = None,
  ):
    """Dispense using the Core96 head.

    Args:
      dispense: The Dispense command to execute.
      jet: Whether to use jet dispense mode.
      empty: Whether to use empty dispense mode.
      blow_out: Whether to blow out after dispensing.
      pull_out_distance_transport_air: The distance to retract after dispensing, in mm.
      use_lld: Whether to use gamma LLD.

      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command, in mm.
      min_z_endpos: Minimal end height, in mm.
      lld_search_height: LLD search height, in mm.
      minimum_height: Maximum immersion depth, in mm. Equals Minimum height during command.
      second_section_height: Height of the second section, in mm.
      second_section_ratio: Ratio of [the diameter of the bottom * 10000] / [the diameter of the top].
      immersion_depth: Immersion depth, in mm.
      surface_following_distance: Surface following distance, in mm. Default 0.
      transport_air_volume: Transport air volume, to dispense before aspiration.
      gamma_lld_sensitivity: Gamma LLD sensitivity.
      swap_speed: Swap speed (on leaving liquid) [mm/s]. Must be between 0.3 and 160. Default 10.
      settling_time: Settling time, in seconds.
      mix_position_from_liquid_surface: Mixing position from liquid surface, in mm.
      mix_surface_following_distance: Surface following distance during mixing, in mm.
      limit_curve_index: Limit curve index.
      cut_off_speed: Unknown.
      stop_back_volume: Unknown.
      disable_volume_correction: Whether to disable liquid class volume correction.
    """

    # # # TODO: delete > 2026-01 # # #
    if mixing_volume != 0 or mixing_cycles != 0 or speed_of_mixing != 0:
      raise NotImplementedError(
        "Mixing through backend kwargs is deprecated. Use the `mix` parameter of LiquidHandler.dispense instead. "
        "https://docs.pylabrobot.org/user_guide/00_liquid-handling/mixing.html"
      )

    if immersion_depth_direction is not None:
      warnings.warn(
        "The immersion_depth_direction parameter is deprecated and will be removed in the future. "
        "Use positive values for immersion_depth to move into the liquid, and negative values to move "
        "out of the liquid.",
        DeprecationWarning,
      )

    if liquid_surface_sink_distance_at_the_end_of_dispense != 0:
      surface_following_distance = liquid_surface_sink_distance_at_the_end_of_dispense
      warnings.warn(
        "The liquid_surface_sink_distance_at_the_end_of_dispense parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard surface_following_distance parameter instead.\n"
        "liquid_surface_sink_distance_at_the_end_of_dispense currently superseding surface_following_distance.",
        DeprecationWarning,
      )

    if maximum_immersion_depth is not None:
      minimum_height = maximum_immersion_depth
      warnings.warn(
        "The maximum_immersion_depth parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard minimum_height parameter instead.\n"
        "minimum_height currently superseding maximum_immersion_depth.",
        DeprecationWarning,
      )

    if minimal_end_height is not None:
      min_z_endpos = minimal_end_height
      warnings.warn(
        "The minimal_end_height parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard min_z_endpos parameter instead.\n"
        "min_z_endpos currently superseding minimal_end_height.",
        DeprecationWarning,
      )

    if mixing_position_from_liquid_surface != 0:
      mix_position_from_liquid_surface = mixing_position_from_liquid_surface
      warnings.warn(
        "The mixing_position_from_liquid_surface parameter is deprecated and will be removed in the future "
        "Use the Hamilton-standard mix_position_from_liquid_surface parameter instead.\n"
        "mix_position_from_liquid_surface currently superseding mixing_position_from_liquid_surface.",
        DeprecationWarning,
      )

    if surface_following_distance_during_mixing != 0:
      mix_surface_following_distance = surface_following_distance_during_mixing
      warnings.warn(
        "The surface_following_distance_during_mixing parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard mix_surface_following_distance parameter instead.\n"
        "mix_surface_following_distance currently superseding surface_following_distance_during_mixing.",
        DeprecationWarning,
      )

    if air_transport_retract_dist != 10:
      pull_out_distance_transport_air = air_transport_retract_dist
      warnings.warn(
        "The air_transport_retract_dist parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard pull_out_distance_transport_air parameter instead.\n"
        "pull_out_distance_transport_air currently superseding air_transport_retract_dist.",
        DeprecationWarning,
      )

    if tube_2nd_section_ratio != 618.0:
      second_section_ratio = tube_2nd_section_ratio
      warnings.warn(
        "The tube_2nd_section_ratio parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_ratio parameter instead.\n"
        "second_section_ratio currently superseding tube_2nd_section_ratio.",
        DeprecationWarning,
      )

    if tube_2nd_section_height_measured_from_zm != 3.2:
      second_section_height = tube_2nd_section_height_measured_from_zm
      warnings.warn(
        "The tube_2nd_section_height_measured_from_zm parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_height parameter instead.\n"
        "second_section_height currently superseding tube_2nd_section_height_measured_from_zm.",
        DeprecationWarning,
      )

    if dispense_mode is not None:
      warnings.warn(
        "The dispense_mode parameter is deprecated and will be removed in the future. "
        "Use the combination of the `jet`, `empty` and `blow_out` parameters instead. "
        "dispense_mode currently superseding those parameters.",
        DeprecationWarning,
      )
    else:
      dispense_mode = _dispensing_mode_for_op(empty=empty, jet=jet, blow_out=blow_out)
    # # # delete # # #

    # get the first well and tip as representatives
    if isinstance(dispense, MultiHeadDispensePlate):
      plate = dispense.wells[0].parent
      assert isinstance(plate, Plate), "MultiHeadDispensePlate well parent must be a Plate"
      rot = plate.get_absolute_rotation()
      if rot.x % 360 != 0 or rot.y % 360 != 0:
        raise ValueError("Plate rotation around x or y is not supported for 96 head operations")
      if rot.z % 360 == 180:
        ref_well = dispense.wells[-1]
      elif rot.z % 360 == 0:
        ref_well = dispense.wells[0]
      else:
        raise ValueError("96 head only supports plate rotations of 0 or 180 degrees around z")

      position = (
        ref_well.get_location_wrt(self.deck)
        + ref_well.center()
        + Coordinate(z=ref_well.material_z_thickness)
        + dispense.offset
      )
    else:
      # dispense in the center of the container
      # but we have to get the position of the center of tip A1
      x_width = (12 - 1) * 9  # 12 tips in a row, 9 mm between them
      y_width = (8 - 1) * 9  # 8 tips in a column, 9 mm between them
      x_position = (dispense.container.get_absolute_size_x() - x_width) / 2
      y_position = (dispense.container.get_absolute_size_y() - y_width) / 2 + y_width
      position = (
        dispense.container.get_location_wrt(self.deck, z="cavity_bottom")
        + Coordinate(x=x_position, y=y_position)
        + dispense.offset
      )
    self._check_96_position_legal(position, skip_z=True)
    tip = next(tip for tip in dispense.tips if tip is not None)

    liquid_height = position.z + (dispense.liquid_height or 0)

    hlc = hlc or get_star_liquid_class(
      tip_volume=tip.maximal_volume,
      is_core=True,
      is_tip=True,
      has_filter=tip.has_filter,
      # get last liquid in pipette, first to be dispensed
      liquid=Liquid.WATER,  # default to WATER
      jet=jet,
      blow_out=blow_out,  # see comment in method docstring
    )

    if disable_volume_correction or hlc is None:
      volume = dispense.volume
    else:  # hlc is not None and not disable_volume_correction
      volume = hlc.compute_corrected_volume(dispense.volume)

    transport_air_volume = transport_air_volume or (
      hlc.dispense_air_transport_volume if hlc is not None else 0
    )
    blow_out_air_volume = dispense.blow_out_air_volume or (
      hlc.dispense_blow_out_volume if hlc is not None else 0
    )
    flow_rate = dispense.flow_rate or (hlc.dispense_flow_rate if hlc is not None else 120)
    swap_speed = swap_speed or (hlc.dispense_swap_speed if hlc is not None else 100)
    settling_time = settling_time or (hlc.dispense_settling_time if hlc is not None else 5)

    return await self.dispense_core_96(
      dispensing_mode=dispense_mode,
      x_position=abs(round(position.x * 10)),
      x_direction=0 if position.x >= 0 else 1,
      y_position=round(position.y * 10),
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._pip.traversal_height) * 10
      ),
      min_z_endpos=round((min_z_endpos or self._pip.traversal_height) * 10),
      lld_search_height=round(lld_search_height * 10),
      liquid_surface_no_lld=round(liquid_height * 10),
      pull_out_distance_transport_air=round(pull_out_distance_transport_air * 10),
      minimum_height=round((minimum_height or position.z) * 10),
      second_section_height=round(second_section_height * 10),
      second_section_ratio=round(second_section_ratio * 10),
      immersion_depth=round(immersion_depth * 10),
      immersion_depth_direction=immersion_depth_direction or (0 if (immersion_depth >= 0) else 1),
      surface_following_distance=round(surface_following_distance * 10),
      dispense_volume=round(volume * 10),
      dispense_speed=round(flow_rate * 10),
      transport_air_volume=round(transport_air_volume * 10),
      blow_out_air_volume=round(blow_out_air_volume * 10),
      lld_mode=int(use_lld),
      gamma_lld_sensitivity=gamma_lld_sensitivity,
      swap_speed=round(swap_speed * 10),
      settling_time=round(settling_time * 10),
      mixing_volume=round(dispense.mix.volume * 10) if dispense.mix is not None else 0,
      mixing_cycles=dispense.mix.repetitions if dispense.mix is not None else 0,
      mix_position_from_liquid_surface=round(mix_position_from_liquid_surface * 10),
      mix_surface_following_distance=round(mix_surface_following_distance * 10),
      speed_of_mixing=round(dispense.mix.flow_rate * 10) if dispense.mix is not None else 1200,
      channel_pattern=[True] * 12 * 8,
      limit_curve_index=limit_curve_index,
      tadm_algorithm=False,
      recording_mode=0,
      cut_off_speed=round(cut_off_speed * 10),
      stop_back_volume=round(stop_back_volume * 10),
    )

  async def iswap_move_picked_up_resource(
    self,
    center: Coordinate,
    grip_direction: GripDirection,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
  ):
    """After a resource is picked up, move it to a new location but don't release it yet.
    Low level component of :meth:`move_resource`
    """

    assert self.extended_conf.left_x_drive.iswap_installed, "iswap must be installed"

    x_direction = 0 if center.x >= 0 else 1
    y_direction = 0 if center.y >= 0 else 1

    await self.move_plate_to_position(
      x_position=round(abs(center.x) * 10),
      x_direction=x_direction,
      y_position=round(abs(center.y) * 10),
      y_direction=y_direction,
      z_position=round(center.z * 10),
      z_direction=0,
      grip_direction={
        GripDirection.FRONT: 1,
        GripDirection.RIGHT: 2,
        GripDirection.BACK: 3,
        GripDirection.LEFT: 4,
      }[grip_direction],
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._iswap.traversal_height) * 10
      ),
      collision_control_level=collision_control_level,
      acceleration_index_high_acc=acceleration_index_high_acc,
      acceleration_index_low_acc=acceleration_index_low_acc,
    )

  async def core_pick_up_resource(
    self,
    resource: Resource,
    pickup_distance_from_top: float,
    offset: Coordinate = Coordinate.zero(),
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    minimum_z_position_at_the_command_end: Optional[float] = None,
    grip_strength: int = 15,
    z_speed: float = 50.0,
    y_gripping_speed: float = 5.0,
    front_channel: int = 7,
  ):
    """Pick up resource with CoRe gripper tool
    Low level component of :meth:`move_resource`

    Args:
      resource: Resource to pick up.
      offset: Offset from resource position in mm.
      pickup_distance_from_top: Distance from top of resource to pick up.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command [mm] (refers to all channels independent of tip pattern parameter 'tm'). Must be
        between 0 and 360.
      grip_strength: Grip strength (0 = weak, 99 = strong). Must be between 0 and 99. Default 15.
      z_speed: Z speed [mm/s]. Must be between 0.4 and 128.7. Default 50.0.
      y_gripping_speed: Y gripping speed [mm/s]. Must be between 0 and 370.0. Default 5.0.
      front_channel: Channel 1. Must be between 1 and self._num_channels - 1. Default 7.
    """

    # Get center of source plate. Also gripping height and plate width.
    center = resource.get_location_wrt(self.deck, x="c", y="c", z="b") + offset
    grip_height = center.z + resource.get_absolute_size_z() - pickup_distance_from_top
    grip_width = resource.get_absolute_size_y()  # grip width is y size of resource

    if self.core_parked:
      await self.pick_up_core_gripper_tools(front_channel=front_channel)

    await self.core_get_plate(
      x_position=round(center.x * 10),
      x_direction=0,
      y_position=round(center.y * 10),
      y_gripping_speed=round(y_gripping_speed * 10),
      z_position=round(grip_height * 10),
      z_speed=round(z_speed * 10),
      open_gripper_position=round(grip_width * 10) + 30,
      plate_width=round(grip_width * 10) - 30,
      grip_strength=grip_strength,
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._iswap.traversal_height) * 10
      ),
      minimum_z_position_at_the_command_end=round(
        (minimum_z_position_at_the_command_end or self._iswap.traversal_height) * 10
      ),
    )

  async def core_move_picked_up_resource(
    self,
    center: Coordinate,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    acceleration_index: int = 4,
    z_speed: float = 50.0,
  ):
    """After a resource is picked up, move it to a new location but don't release it yet.
    Low level component of :meth:`move_resource`

    Args:
      location: Location to move to.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command [0.1mm] (refers to all channels independent of tip pattern parameter 'tm'). Must be
        between 0 and 3600. Default 3600.
      acceleration_index: Acceleration index (0 = 0.1 mm/s2, 1 = 0.2 mm/s2, 2 = 0.5 mm/s2,
        3 = 1.0 mm/s2, 4 = 2.0 mm/s2, 5 = 5.0 mm/s2, 6 = 10.0 mm/s2, 7 = 20.0 mm/s2). Must be
        between 0 and 7. Default 4.
      z_speed: Z speed [0.1mm/s]. Must be between 3 and 1600. Default 500.
    """

    await self.core_move_plate_to_position(
      x_position=round(center.x * 10),
      x_direction=0,
      x_acceleration_index=acceleration_index,
      y_position=round(center.y * 10),
      z_position=round(center.z * 10),
      z_speed=round(z_speed * 10),
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._iswap.traversal_height) * 10
      ),
    )

  async def core_release_picked_up_resource(
    self,
    location: Coordinate,
    resource: Resource,
    pickup_distance_from_top: float,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    z_position_at_the_command_end: Optional[float] = None,
    return_tool: bool = True,
  ):
    """Place resource with CoRe gripper tool
    Low level component of :meth:`move_resource`

    Args:
      resource: Location to place.
      pickup_distance_from_top: Distance from top of resource to place.
      offset: Offset from resource position in mm.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command [mm] (refers to all channels independent of tip pattern parameter 'tm'). Must be
        between 0 and 360.0.
      z_position_at_the_command_end: Minimum z-Position at end of a command [mm] (refers to all
        channels independent of tip pattern parameter 'tm'). Must be between 0 and 360.0
      return_tool: Return tool to wasteblock mount after placing. Default True.
    """

    # Get center of destination location. Also gripping height and plate width.
    grip_height = location.z + resource.get_absolute_size_z() - pickup_distance_from_top
    grip_width = resource.get_absolute_size_y()

    await self.core_put_plate(
      x_position=round(location.x * 10),
      x_direction=0,
      y_position=round(location.y * 10),
      z_position=round(grip_height * 10),
      z_press_on_distance=0,
      z_speed=500,
      open_gripper_position=round(grip_width * 10) + 30,
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._iswap.traversal_height) * 10
      ),
      z_position_at_the_command_end=round(
        (z_position_at_the_command_end or self._iswap.traversal_height) * 10
      ),
      return_tool=return_tool,
    )

  async def pick_up_resource(
    self,
    pickup: ResourcePickup,
    use_arm: Literal["iswap", "core"] = "iswap",
    core_front_channel: int = 7,
    iswap_grip_strength: int = 4,
    core_grip_strength: int = 15,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    z_position_at_the_command_end: Optional[float] = None,
    plate_width_tolerance: float = 2.0,
    open_gripper_position: Optional[float] = None,
    hotel_depth=160.0,
    hotel_clearance_height=7.5,
    high_speed=False,
    plate_width: Optional[float] = None,
    use_unsafe_hotel: bool = False,
    iswap_collision_control_level: int = 0,
    iswap_fold_up_sequence_at_the_end_of_process: bool = False,
    # deprecated
    channel_1: Optional[int] = None,
    channel_2: Optional[int] = None,
  ):
    if use_arm == "iswap":
      assert (
        pickup.resource.get_absolute_rotation().x == 0
        and pickup.resource.get_absolute_rotation().y == 0
      )
      assert pickup.resource.get_absolute_rotation().z % 90 == 0
      if plate_width is None:
        if pickup.direction in (GripDirection.FRONT, GripDirection.BACK):
          plate_width = pickup.resource.get_absolute_size_x()
        else:
          plate_width = pickup.resource.get_absolute_size_y()

      center_in_absolute_space = pickup.resource.center().rotated(
        pickup.resource.get_absolute_rotation()
      )
      x, y, z = (
        pickup.resource.get_location_wrt(self.deck, "l", "f", "t")
        + center_in_absolute_space
        + pickup.offset
      )
      z -= pickup.pickup_distance_from_top

      traverse_height_at_beginning = (
        minimum_traverse_height_at_beginning_of_a_command or self._iswap.traversal_height
      )
      z_position_at_the_command_end = z_position_at_the_command_end or self._iswap.traversal_height

      if open_gripper_position is None:
        if use_unsafe_hotel:
          open_gripper_position = plate_width + 5
        else:
          open_gripper_position = plate_width + 3

      if use_unsafe_hotel:
        await self.unsafe.get_from_hotel(
          hotel_center_x_coord=round(abs(x) * 10),
          hotel_center_y_coord=round(abs(y) * 10),
          # hotel_center_z_coord=int((z * 10)+0.5), # use sensible rounding (.5 goes up)
          hotel_center_z_coord=round(abs(z) * 10),
          hotel_center_x_direction=0 if x >= 0 else 1,
          hotel_center_y_direction=0 if y >= 0 else 1,
          hotel_center_z_direction=0 if z >= 0 else 1,
          clearance_height=round(hotel_clearance_height * 10),
          hotel_depth=round(hotel_depth * 10),
          grip_direction=pickup.direction,
          open_gripper_position=round(open_gripper_position * 10),
          traverse_height_at_beginning=round(traverse_height_at_beginning * 10),
          z_position_at_end=round(z_position_at_the_command_end * 10),
          high_acceleration_index=4 if high_speed else 1,
          low_acceleration_index=1,
          plate_width=round(plate_width * 10),
          plate_width_tolerance=round(plate_width_tolerance * 10),
        )
      else:
        await self.iswap_get_plate(
          x_position=round(abs(x) * 10),
          y_position=round(abs(y) * 10),
          z_position=round(abs(z) * 10),
          x_direction=0 if x >= 0 else 1,
          y_direction=0 if y >= 0 else 1,
          z_direction=0 if z >= 0 else 1,
          grip_direction={
            GripDirection.FRONT: 1,
            GripDirection.RIGHT: 2,
            GripDirection.BACK: 3,
            GripDirection.LEFT: 4,
          }[pickup.direction],
          minimum_traverse_height_at_beginning_of_a_command=round(
            traverse_height_at_beginning * 10
          ),
          z_position_at_the_command_end=round(z_position_at_the_command_end * 10),
          grip_strength=iswap_grip_strength,
          open_gripper_position=round(open_gripper_position * 10),
          plate_width=round(plate_width * 10) - 33,
          plate_width_tolerance=round(plate_width_tolerance * 10),
          collision_control_level=iswap_collision_control_level,
          acceleration_index_high_acc=4 if high_speed else 1,
          acceleration_index_low_acc=1,
          iswap_fold_up_sequence_at_the_end_of_process=iswap_fold_up_sequence_at_the_end_of_process,
        )
    elif use_arm == "core":
      if use_unsafe_hotel:
        raise ValueError("Cannot use iswap hotel mode with core grippers")

      if pickup.direction != GripDirection.FRONT:
        raise NotImplementedError("Core grippers only support FRONT (default)")

      if channel_1 is not None or channel_2 is not None:
        warnings.warn(
          "The channel_1 and channel_2 parameters are deprecated and will be removed in future versions. "
          "Please use the core_front_channel parameter instead.",
          DeprecationWarning,
        )
        assert channel_1 is not None and channel_2 is not None, (
          "Both channel_1 and channel_2 must be provided"
        )
        assert channel_1 + 1 == channel_2, "channel_2 must be channel_1 + 1"
        core_front_channel = (
          channel_2 - 1
        )  # core_front_channel is the first channel of the gripper tool

      await self.core_pick_up_resource(
        resource=pickup.resource,
        pickup_distance_from_top=pickup.pickup_distance_from_top,
        offset=pickup.offset,
        minimum_traverse_height_at_beginning_of_a_command=self._iswap.traversal_height,
        minimum_z_position_at_the_command_end=self._iswap.traversal_height,
        front_channel=core_front_channel,
        grip_strength=core_grip_strength,
      )
    else:
      raise ValueError(f"use_arm must be either 'iswap' or 'core', not {use_arm}")

  async def move_picked_up_resource(
    self, move: ResourceMove, use_arm: Literal["iswap", "core"] = "iswap"
  ):
    center = (
      move.location
      + move.resource.get_anchor("c", "c", "t")
      - Coordinate(z=move.pickup_distance_from_top)
      + move.offset
    )

    if use_arm == "iswap":
      await self.iswap_move_picked_up_resource(
        center=center,
        grip_direction=move.gripped_direction,
        minimum_traverse_height_at_beginning_of_a_command=self._iswap.traversal_height,
        collision_control_level=1,
        acceleration_index_high_acc=4,
        acceleration_index_low_acc=1,
      )
    else:
      await self.core_move_picked_up_resource(
        center=center,
        minimum_traverse_height_at_beginning_of_a_command=self._iswap.traversal_height,
        acceleration_index=4,
      )

  async def drop_resource(
    self,
    drop: ResourceDrop,
    use_arm: Literal["iswap", "core"] = "iswap",
    return_core_gripper: bool = True,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    z_position_at_the_command_end: Optional[float] = None,
    open_gripper_position: Optional[float] = None,
    hotel_depth=160.0,
    hotel_clearance_height=7.5,
    hotel_high_speed=False,
    use_unsafe_hotel: bool = False,
    iswap_collision_control_level: int = 0,
    iswap_fold_up_sequence_at_the_end_of_process: bool = False,
  ):
    # Get center of source plate in absolute space.
    # The computation of the center has to be rotated so that the offset is in absolute space.
    # center_in_absolute_space will be the vector pointing from the destination origin to the
    # center of the moved the resource after drop.
    # This means that the center vector has to be rotated from the child local space by the
    # new child absolute rotation. The moved resource's rotation will be the original child
    # rotation plus the rotation applied by the movement.
    # The resource is moved by drop.rotation
    # The new resource absolute location is
    # drop.resource.get_absolute_rotation().z + drop.rotation
    center_in_absolute_space = drop.resource.center().rotated(
      Rotation(z=drop.resource.get_absolute_rotation().z + drop.rotation)
    )
    x, y, z = drop.destination + center_in_absolute_space + drop.offset

    if use_arm == "iswap":
      traversal_height_start = (
        minimum_traverse_height_at_beginning_of_a_command or self._iswap.traversal_height
      )
      z_position_at_the_command_end = z_position_at_the_command_end or self._iswap.traversal_height
      assert (
        drop.resource.get_absolute_rotation().x == 0
        and drop.resource.get_absolute_rotation().y == 0
      )
      assert drop.resource.get_absolute_rotation().z % 90 == 0

      # Use the pickup direction to determine how wide the plate is gripped.
      # Note that the plate is still in the original orientation at this point,
      # so get_absolute_size_{x,y}() will return the size of the plate in the original orientation.
      if (
        drop.pickup_direction == GripDirection.FRONT or drop.pickup_direction == GripDirection.BACK
      ):
        plate_width = drop.resource.get_absolute_size_x()
      elif (
        drop.pickup_direction == GripDirection.RIGHT or drop.pickup_direction == GripDirection.LEFT
      ):
        plate_width = drop.resource.get_absolute_size_y()
      else:
        raise ValueError("Invalid grip direction")

      z = z + drop.resource.get_absolute_size_z() - drop.pickup_distance_from_top

      if open_gripper_position is None:
        if use_unsafe_hotel:
          open_gripper_position = plate_width + 5
        else:
          open_gripper_position = plate_width + 3

      if use_unsafe_hotel:
        # hotel: down forward down.
        # down to level of the destination + the clearance height (so clearance height can be subtracted)
        # hotel_depth is forward.
        # clearance height is second down.

        await self.unsafe.put_in_hotel(
          hotel_center_x_coord=round(abs(x) * 10),
          hotel_center_y_coord=round(abs(y) * 10),
          hotel_center_z_coord=round(abs(z) * 10),
          hotel_center_x_direction=0 if x >= 0 else 1,
          hotel_center_y_direction=0 if y >= 0 else 1,
          hotel_center_z_direction=0 if z >= 0 else 1,
          clearance_height=round(hotel_clearance_height * 10),
          hotel_depth=round(hotel_depth * 10),
          grip_direction=drop.direction,
          open_gripper_position=round(open_gripper_position * 10),
          traverse_height_at_beginning=round(traversal_height_start * 10),
          z_position_at_end=round(z_position_at_the_command_end * 10),
          high_acceleration_index=4 if hotel_high_speed else 1,
          low_acceleration_index=1,
        )
      else:
        await self.iswap_put_plate(
          x_position=round(abs(x) * 10),
          y_position=round(abs(y) * 10),
          z_position=round(abs(z) * 10),
          x_direction=0 if x >= 0 else 1,
          y_direction=0 if y >= 0 else 1,
          z_direction=0 if z >= 0 else 1,
          grip_direction={
            GripDirection.FRONT: 1,
            GripDirection.RIGHT: 2,
            GripDirection.BACK: 3,
            GripDirection.LEFT: 4,
          }[drop.direction],
          minimum_traverse_height_at_beginning_of_a_command=round(traversal_height_start * 10),
          z_position_at_the_command_end=round(z_position_at_the_command_end * 10),
          open_gripper_position=round(open_gripper_position * 10),
          collision_control_level=iswap_collision_control_level,
          iswap_fold_up_sequence_at_the_end_of_process=iswap_fold_up_sequence_at_the_end_of_process,
        )
    elif use_arm == "core":
      if use_unsafe_hotel:
        raise ValueError("Cannot use iswap hotel mode with core grippers")

      if drop.direction != GripDirection.FRONT:
        raise NotImplementedError("Core grippers only support FRONT direction (default)")

      await self.core_release_picked_up_resource(
        location=Coordinate(x, y, z),
        resource=drop.resource,
        pickup_distance_from_top=drop.pickup_distance_from_top,
        minimum_traverse_height_at_beginning_of_a_command=self._iswap.traversal_height,
        z_position_at_the_command_end=self._iswap.traversal_height,
        # int(previous_location.z + move.resource.get_size_z() / 2) * 10,
        return_tool=return_core_gripper,
      )
    else:
      raise ValueError(f"use_arm must be either 'iswap' or 'core', not {use_arm}")

  async def prepare_for_manual_channel_operation(self, channel: int):
    """Deprecated: use ``star.pip.backend.prepare_for_manual_channel_operation()``."""
    await self.driver.pip.prepare_for_manual_channel_operation(channel)

  async def move_channel_x(self, channel: int, x: float):
    """Deprecated: use ``star.driver.left_x_arm.move_to()``."""
    await self._left_x_arm.move_to(x)

  @need_iswap_parked
  async def move_channel_y(self, channel: int, y: float):
    """Deprecated: use ``star.driver.pip.channels[n].move_y()``."""
    await self.driver.pip.channels[channel].move_y(y)

  async def move_channel_z(self, channel: int, z: float):
    """Deprecated: use ``channels[n].move_stop_disk_z()`` or ``channels[n].move_tool_z()``."""
    await self._pip.channels[channel].move_stop_disk_z(z)

  async def move_channel_stop_disk_z(
    self,
    channel_idx: int,
    z: float,
    speed: float = 125.0,
    acceleration: float = 800.0,
    current_limit: int = 3,
  ):
    """Deprecated: use ``star.pip.backend.channels[n].move_stop_disk_z()``."""
    return await self._pip.channels[channel_idx].move_stop_disk_z(
      z, speed, acceleration, current_limit
    )

  async def move_channel_tool_z(self, channel_idx: int, z: float):
    """Deprecated: use ``star.pip.backend.channels[n].move_tool_z()``."""
    return await self._pip.channels[channel_idx].move_tool_z(z)

  async def move_channel_x_relative(self, channel: int, distance: float):
    """Move a channel in the x direction by a relative amount."""
    current_x = await self.request_x_pos_channel_n(channel)
    await self.move_channel_x(channel, current_x + distance)

  async def move_channel_y_relative(self, channel: int, distance: float):
    """Move a channel in the y direction by a relative amount."""
    current_y = await self.request_y_pos_channel_n(channel)
    await self.move_channel_y(channel, current_y + distance)

  async def move_channel_z_relative(self, channel: int, distance: float):
    """Move a channel in the z direction by a relative amount."""
    # TODO: determine whether this refers to stop disk or tip bottom
    current_z = await self.request_z_pos_channel_n(channel)
    await self.move_channel_z(channel, current_z + distance)

  def get_channel_spacings(self, use_channels: List[int]) -> List[float]:
    return [self._channels_minimum_y_spacing[ch] for ch in sorted(use_channels)]

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    if not isinstance(tip, HamiltonTip):
      return False
    if tip.tip_size in {TipSize.XL}:
      return False
    return True

  async def core_check_resource_exists_at_location_center(
    self,
    location: Coordinate,
    resource: Resource,
    gripper_y_margin: float = 0.5,
    offset: Coordinate = Coordinate.zero(),
    minimum_traverse_height_at_beginning_of_a_command: float = 275.0,
    z_position_at_the_command_end: float = 275.0,
    enable_recovery: bool = True,
    audio_feedback: bool = True,
  ) -> bool:
    """Check existence of resource with CoRe gripper tool
    a "Get plate using CO-RE gripper" + error handling
    Which channels are used for resource check is dependent on which channels have been used for
    `STARBackend.get_core(p1: int, p2: int)` (channel indices are 0-based) which is a prerequisite
    for this check function.

    Args:
      location: Location to check for resource
      resource: Resource to check for.
      gripper_y_margin = Distance between the front / back wall of the resource
        and the grippers during "bumping" / checking
      offset: Offset from resource position in mm.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
        a command [mm] (refers to all channels independent of tip pattern parameter 'tm').
        Must be between 0 and 360.0.
      z_position_at_the_command_end: Minimum z-Position at end of a command [mm] (refers to
        all channels independent of tip pattern parameter 'tm'). Must be between 0 and 360.0.
      enable_recovery: if True will ask for user input if resource was not found
      audio_feedback: enable controlling computer to emit different sounds when
        finding/not finding the resource

    Returns:
      True if resource was found, False if resource was not found
    """

    center = location + resource.centers()[0] + offset
    y_width_to_gripper_bump = resource.get_absolute_size_y() - gripper_y_margin * 2
    max_spacing = max(self._channels_minimum_y_spacing)
    assert max_spacing <= y_width_to_gripper_bump <= round(resource.get_absolute_size_y()), (
      f"width between channels must be between {max_spacing} and "
      f"{resource.get_absolute_size_y()} mm"
      " (i.e. the maximal distance between channels and the max y size of the resource"
    )

    # Check if CoRe gripper currently in use
    cores_used = not self._core_parked
    if not cores_used:
      raise ValueError("CoRe grippers not yet picked up.")

    # Enable recovery of failed checks
    resource_found = False
    try_counter = 0
    while not resource_found:
      try:
        await self.core_get_plate(
          x_position=round(center.x * 10),
          y_position=round(center.y * 10),
          z_position=round(center.z * 10),
          open_gripper_position=round(y_width_to_gripper_bump * 10),
          plate_width=round(y_width_to_gripper_bump * 10),
          # Set default values based on VENUS check_plate commands
          y_gripping_speed=50,
          x_direction=0,
          z_speed=600,
          grip_strength=20,
          # Enable mods of channel z position for check acceleration
          minimum_traverse_height_at_beginning_of_a_command=round(
            minimum_traverse_height_at_beginning_of_a_command * 10
          ),
          minimum_z_position_at_the_command_end=round(z_position_at_the_command_end * 10),
        )
      except STARFirmwareError as exc:
        for module_error in exc.errors.values():
          if module_error.trace_information == 62:
            resource_found = True
          else:
            raise ValueError(f"Unexpected error encountered: {exc}") from exc
      else:
        if audio_feedback:
          audio.play_not_found()
        if enable_recovery:
          print(
            f"\nWARNING: Resource '{resource.name}' not found at center"
            f" location {(center.x, center.y, center.z)} during check no {try_counter}."
          )
          user_prompt = input(
            "Have you checked resource is present?"
            "\n [ yes ] -> machine will check location again"
            "\n [ abort ] -> machine will abort run\n Answer:"
          )
          if user_prompt == "yes":
            try_counter += 1
          elif user_prompt == "abort":
            raise ValueError(
              f"Resource '{resource.name}' not found at center"
              f" location {(center.x, center.y, center.z)}"
              " & error not resolved -> aborted resource movement."
            )
        else:
          # Resource was not found
          return False

    # Resource was found
    if audio_feedback:
      audio.play_got_item()
    return True

  def _position_96_head_in_resource(self, resource: Resource) -> Coordinate:
    """The firmware command expects location of tip A1 of the head. We center the head in the given
    resource."""
    head_size_x = 9 * 11  # 12 channels, 9mm spacing in between
    head_size_y = 9 * 7  #   8 channels, 9mm spacing in between
    channel_size = 9
    loc = resource.get_location_wrt(self.deck)
    loc.x += (resource.get_size_x() - head_size_x) / 2 + channel_size / 2
    loc.y += (resource.get_size_y() - head_size_y) / 2 + channel_size / 2
    return loc

  def _check_96_position_legal(self, c: Coordinate, skip_z=False) -> None:
    """Validate that a coordinate is within the allowed range for the 96 head.

    Args:
      c: The coordinate of the A1 position of the head.
      skip_z: If True, the z coordinate is not checked. This is useful for commands that handle
        the z coordinate separately, such as the big four.

    Raises:
      ValueError: If one or more components are out of range. The error message contains all offending components.
    """

    # TODO: these are values for a STARBackend. Find them for a STARlet.

    x_min = self.HEAD96_X_MIN_WITH_LEFT_SIDE_PANEL if self.left_side_panel_installed else -271.0

    errors = []
    if not (x_min <= c.x <= 974.0):
      errors.append(f"x={c.x}")
    if not (108.0 <= c.y <= 560.0):
      errors.append(f"y={c.y}")
    if not (180.5 <= c.z <= 342.5) and not skip_z:
      errors.append(f"z={c.z}")

    if len(errors) > 0:
      raise ValueError(
        "Illegal 96 head position: "
        + ", ".join(errors)
        + f" (allowed ranges: x [{x_min}, 974], y [108, 560], z [180.5, 342.5])"
      )

  # ============== Firmware Commands ==============

  # -------------- 3.2 System general commands --------------

  async def pre_initialize_instrument(self):
    """Deprecated: use ``star.driver.pre_initialize_instrument()``."""
    return await self.send_command(module="C0", command="VI", read_timeout=300)

  async def define_tip_needle(
    self,
    tip_type_table_index: int,
    has_filter: bool,
    tip_length: int,
    maximum_tip_volume: int,
    tip_size: TipSize,
    pickup_method: TipPickupMethod,
  ):
    """Tip/needle definition.

    Args:
      tip_type_table_index: tip_table_index
      has_filter: with(out) filter
      tip_length: Tip length [0.1mm]
      maximum_tip_volume: Maximum volume of tip [0.1ul]
                          Note! it's automatically limited to max. channel capacity
      tip_type: Type of tip collar (Tip type identification)
      pickup_method: pick up method.
                      Attention! The values set here are temporary and apply only until
                      power OFF or RESET. After power ON the default values apply. (see Table 3)
    """

    assert 0 <= tip_type_table_index <= 99, "tip_type_table_index must be between 0 and 99"
    assert 0 <= tip_type_table_index <= 99, "tip_type_table_index must be between 0 and 99"
    assert 1 <= tip_length <= 1999, "tip_length must be between 1 and 1999"
    assert 1 <= maximum_tip_volume <= 56000, "maximum_tip_volume must be between 1 and 56000"

    return await self.send_command(
      module="C0",
      command="TT",
      tt=f"{tip_type_table_index:02}",
      tf=has_filter,
      tl=f"{tip_length:04}",
      tv=f"{maximum_tip_volume:05}",
      tg=tip_size.value,
      tu=pickup_method.value,
    )

  # -------------- 3.2.1 System query --------------

  async def request_error_code(self):
    """Deprecated: use ``star.driver.request_error_code()``."""

    return await self.send_command(module="C0", command="RE")

  async def request_firmware_version(self):
    """Deprecated: use ``star.driver.request_firmware_version()``."""

    return await self.send_command(module="C0", command="RF")

  async def request_parameter_value(self):
    """Deprecated: use ``star.driver.request_parameter_value()``."""

    return await self.send_command(module="C0", command="RA")

  class BoardType(enum.Enum):
    C167CR_SINGLE_PROCESSOR_BOARD = 0
    C167CR_DUAL_PROCESSOR_BOARD = 1
    LPC2468_XE167_DUAL_PROCESSOR_BOARD = 2
    LPC2468_SINGLE_PROCESSOR_BOARD = 5
    UNKNOWN = -1

  async def request_electronic_board_type(self):
    """Deprecated: use ``star.driver.request_electronic_board_type()``."""

    resp = await self.send_command(module="C0", command="QB")
    try:
      return STARBackend.BoardType(resp["qb"])
    except ValueError:
      return STARBackend.BoardType.UNKNOWN

  async def request_supply_voltage(self):
    """Deprecated: use ``star.driver.request_supply_voltage()``."""

    return await self.send_command(module="C0", command="MU")

  async def request_instrument_initialization_status(self) -> bool:
    """Deprecated: use ``star.driver.request_instrument_initialization_status()``."""

    resp = await self.send_command(module="C0", command="QW", fmt="qw#")
    return resp is not None and resp["qw"] == 1

  async def request_autoload_initialization_status(self) -> bool:
    """Deprecated: use ``star.autoload.request_initialization_status()``."""
    return await self._autoload.request_initialization_status()

  async def request_name_of_last_faulty_parameter(self):
    """Deprecated: use ``star.driver.request_name_of_last_faulty_parameter()``."""

    return await self.send_command(module="C0", command="VP", fmt="vp&&")

  async def request_master_status(self):
    """Deprecated: use ``star.driver.request_master_status()``."""

    return await self.send_command(module="C0", command="RQ")

  async def request_number_of_presence_sensors_installed(self):
    """Deprecated: use ``star.driver.request_number_of_presence_sensors_installed()``."""

    resp = await self.send_command(module="C0", command="SR")
    return resp["sr"]

  async def request_eeprom_data_correctness(self):
    """Deprecated: use ``star.driver.request_eeprom_data_correctness()``."""

    return await self.send_command(module="C0", command="QV")

  # -------------- 3.3 Settings --------------

  # -------------- 3.3.1 Volatile Settings --------------

  async def set_single_step_mode(self, single_step_mode: bool = False):
    """Deprecated: use ``star.driver.set_single_step_mode()``."""

    return await self.send_command(
      module="C0",
      command="AM",
      am=single_step_mode,
    )

  async def trigger_next_step(self):
    """Deprecated: use ``star.driver.trigger_next_step()``."""

    # TODO: this command has no reply!!!!
    return await self.send_command(module="C0", command="NS")

  async def halt(self):
    """Deprecated: use ``star.driver.halt()``."""

    return await self.send_command(module="C0", command="HD")

  async def save_all_cycle_counters(self):
    """Deprecated: use ``star.driver.save_all_cycle_counters()``."""

    return await self.send_command(module="C0", command="AZ")

  async def set_not_stop(self, non_stop):
    """Deprecated: use ``star.driver.set_not_stop()``."""

    if non_stop:
      # TODO: this command has no reply!!!!
      return await self.send_command(module="C0", command="AB")
    else:
      return await self.send_command(module="C0", command="AW")

  # -------------- 3.3.2 Non volatile settings (stored in EEPROM) --------------

  async def store_installation_data(
    self,
    date: datetime.datetime = datetime.datetime.now(),
    serial_number: str = "0000",
  ):
    """Deprecated: use ``star.driver.store_installation_data()``."""

    assert len(serial_number) == 4, "serial number must be 4 chars long"

    return await self.send_command(module="C0", command="SI", si=date, sn=serial_number)

  async def store_verification_data(
    self,
    verification_subject: int = 0,
    date: datetime.datetime = datetime.datetime.now(),
    verification_status: bool = False,
  ):
    """Deprecated: use ``star.driver.store_verification_data()``."""

    assert 0 <= verification_subject <= 24, "verification_subject must be between 0 and 24"

    return await self.send_command(
      module="C0",
      command="AV",
      vo=verification_subject,
      vd=date,
      vs=verification_status,
    )

  async def additional_time_stamp(self):
    """Deprecated: use ``star.driver.additional_time_stamp()``."""

    return await self.send_command(module="C0", command="AT")

  async def set_x_offset_x_axis_iswap(self, x_offset: int):
    """Deprecated: use ``star.driver.set_x_offset_x_axis_iswap()``."""

    return await self.send_command(module="C0", command="AG", x_offset=x_offset)

  async def set_x_offset_x_axis_core_96_head(self, x_offset: int):
    """Deprecated: use ``star.driver.set_x_offset_x_axis_core_96_head()``."""

    return await self.send_command(module="C0", command="AF", x_offset=x_offset)

  async def set_x_offset_x_axis_core_nano_pipettor_head(self, x_offset: int):
    """Deprecated: use ``star.driver.set_x_offset_x_axis_core_nano_pipettor_head()``."""

    return await self.send_command(module="C0", command="AF", x_offset=x_offset)

  async def save_download_date(self, date: datetime.datetime = datetime.datetime.now()):
    """Deprecated: use ``star.driver.save_download_date()``."""

    return await self.send_command(
      module="C0",
      command="AO",
      ao=date,
    )

  async def save_technical_status_of_assemblies(self, processor_board: str, power_supply: str):
    """Deprecated: use ``star.driver.save_technical_status_of_assemblies()``."""

    return await self.send_command(
      module="C0",
      command="BT",
      qt=processor_board + " " + power_supply,
    )

  async def set_instrument_configuration(
    self,
    configuration_data_1: Optional[str] = None,  # TODO: configuration byte
    configuration_data_2: Optional[str] = None,  # TODO: configuration byte
    configuration_data_3: Optional[str] = None,  # TODO: configuration byte
    instrument_size_in_slots_x_range: int = 54,
    auto_load_size_in_slots: int = 54,
    tip_waste_x_position: int = 13400,
    right_x_drive_configuration_byte_1: int = 0,
    right_x_drive_configuration_byte_2: int = 0,
    minimal_iswap_collision_free_position: int = 3500,
    maximal_iswap_collision_free_position: int = 11400,
    left_x_arm_width: int = 3700,
    right_x_arm_width: int = 3700,
    num_pip_channels: int = 0,
    num_xl_channels: int = 0,
    num_robotic_channels: int = 0,
    minimal_raster_pitch_of_pip_channels: int = 90,
    minimal_raster_pitch_of_xl_channels: int = 360,
    minimal_raster_pitch_of_robotic_channels: int = 360,
    pip_maximal_y_position: int = 6065,
    left_arm_minimal_y_position: int = 60,
    right_arm_minimal_y_position: int = 60,
  ):
    """Deprecated: use ``star.driver.set_instrument_configuration()``."""

    assert 1 <= instrument_size_in_slots_x_range <= 9, (
      "instrument_size_in_slots_x_range must be between 1 and 99"
    )
    assert 1 <= auto_load_size_in_slots <= 54, "auto_load_size_in_slots must be between 1 and 54"
    assert 1000 <= tip_waste_x_position <= 25000, "tip_waste_x_position must be between 1 and 25000"
    assert 0 <= right_x_drive_configuration_byte_1 <= 1, (
      "right_x_drive_configuration_byte_1 must be between 0 and 1"
    )
    assert 0 <= right_x_drive_configuration_byte_2 <= 1, (
      "right_x_drive_configuration_byte_2 must be between 0 and  must1"
    )
    assert 0 <= minimal_iswap_collision_free_position <= 30000, (
      "minimal_iswap_collision_free_position must be between 0 and 30000"
    )
    assert 0 <= maximal_iswap_collision_free_position <= 30000, (
      "maximal_iswap_collision_free_position must be between 0 and 30000"
    )
    assert 0 <= left_x_arm_width <= 9999, "left_x_arm_width must be between 0 and 9999"
    assert 0 <= right_x_arm_width <= 9999, "right_x_arm_width must be between 0 and 9999"
    assert 0 <= num_pip_channels <= 16, "num_pip_channels must be between 0 and 16"
    assert 0 <= num_xl_channels <= 8, "num_xl_channels must be between 0 and 8"
    assert 0 <= num_robotic_channels <= 8, "num_robotic_channels must be between 0 and 8"
    assert 0 <= minimal_raster_pitch_of_pip_channels <= 999, (
      "minimal_raster_pitch_of_pip_channels must be between 0 and 999"
    )
    assert 0 <= minimal_raster_pitch_of_xl_channels <= 999, (
      "minimal_raster_pitch_of_xl_channels must be between 0 and 999"
    )
    assert 0 <= minimal_raster_pitch_of_robotic_channels <= 999, (
      "minimal_raster_pitch_of_robotic_channels must be between 0 and 999"
    )
    assert 0 <= pip_maximal_y_position <= 9999, "pip_maximal_y_position must be between 0 and 9999"
    assert 0 <= left_arm_minimal_y_position <= 9999, (
      "left_arm_minimal_y_position must be between 0 and 9999"
    )
    assert 0 <= right_arm_minimal_y_position <= 9999, (
      "right_arm_minimal_y_position must be between 0 and 9999"
    )

    return await self.send_command(
      module="C0",
      command="AK",
      kb=configuration_data_1,
      ka=configuration_data_2,
      ke=configuration_data_3,
      xt=instrument_size_in_slots_x_range,
      xa=auto_load_size_in_slots,
      xw=tip_waste_x_position,
      xr=right_x_drive_configuration_byte_1,
      xo=right_x_drive_configuration_byte_2,
      xm=minimal_iswap_collision_free_position,
      xx=maximal_iswap_collision_free_position,
      xu=left_x_arm_width,
      xv=right_x_arm_width,
      kp=num_pip_channels,
      kc=num_xl_channels,
      kr=num_robotic_channels,
      ys=minimal_raster_pitch_of_pip_channels,
      kl=minimal_raster_pitch_of_xl_channels,
      km=minimal_raster_pitch_of_robotic_channels,
      ym=pip_maximal_y_position,
      yu=left_arm_minimal_y_position,
      yx=right_arm_minimal_y_position,
    )

  async def save_pip_channel_validation_status(self, validation_status: bool = False):
    """Deprecated: use ``star.driver.save_pip_channel_validation_status()``."""

    return await self.send_command(
      module="C0",
      command="AJ",
      tq=validation_status,
    )

  async def save_xl_channel_validation_status(self, validation_status: bool = False):
    """Deprecated: use ``star.driver.save_xl_channel_validation_status()``."""

    return await self.send_command(
      module="C0",
      command="AE",
      tx=validation_status,
    )

  # TODO: response
  async def configure_node_names(self):
    """Deprecated: use ``star.driver.configure_node_names()``."""

    return await self.send_command(module="C0", command="AJ")

  async def set_deck_data(self, data_index: int = 0, data_stream: str = "0"):
    """Deprecated: use ``star.driver.set_deck_data()``."""

    assert 0 <= data_index <= 9, "data_index must be between 0 and 9"
    assert len(data_stream) == 12, "data_stream must be 12 chars"

    return await self.send_command(
      module="C0",
      command="DD",
      vi=data_index,
      vj=data_stream,
    )

  # -------------- 3.3.3 Settings query (stored in EEPROM) --------------

  async def request_technical_status_of_assemblies(self):
    """Deprecated: use ``star.driver.request_technical_status_of_assemblies()``."""

    # TODO: parse res
    return await self.send_command(module="C0", command="QT")

  async def request_installation_data(self):
    """Deprecated: use ``star.driver.request_installation_data()``."""

    # TODO: parse res
    return await self.send_command(module="C0", command="RI")

  async def request_device_serial_number(self) -> str:
    """Deprecated: use ``star.driver.request_device_serial_number()``."""
    return (await self.send_command("C0", "RI", fmt="si####sn&&&&sn&&&&"))["sn"]  # type: ignore

  async def request_download_date(self):
    """Deprecated: use ``star.driver.request_download_date()``."""

    # TODO: parse res
    return await self.send_command(module="C0", command="RO")

  async def request_verification_data(self, verification_subject: int = 0):
    """Deprecated: use ``star.driver.request_verification_data()``."""

    assert 0 <= verification_subject <= 24, "verification_subject must be between 0 and 24"

    # TODO: parse results.
    return await self.send_command(module="C0", command="RO", vo=verification_subject)

  async def request_additional_timestamp_data(self):
    """Deprecated: use ``star.driver.request_additional_timestamp_data()``."""

    # TODO: parse res
    return await self.send_command(module="C0", command="RS")

  async def request_pip_channel_validation_status(self):
    """Deprecated: use ``star.driver.request_pip_channel_validation_status()``."""

    # TODO: parse res
    return await self.send_command(module="C0", command="RJ")

  async def request_xl_channel_validation_status(self):
    """Deprecated: use ``star.driver.request_xl_channel_validation_status()``."""

    # TODO: parse res
    return await self.send_command(module="C0", command="UJ")

  async def request_machine_configuration(self) -> MachineConfiguration:
    """Request machine configuration (RM command) [SFCO.0035].

    Returns the basic machine configuration including configuration data 1 (kb)
    and number of PIP channels (kp).
    """

    resp = await self.send_command(module="C0", command="RM", fmt="kb**kp##")
    kb = resp["kb"]
    return MachineConfiguration(
      pip_type_1000ul=bool(kb & (1 << 0)),
      kb_iswap_installed=bool(kb & (1 << 1)),
      main_front_cover_monitoring_installed=bool(kb & (1 << 2)),
      auto_load_installed=bool(kb & (1 << 3)),
      wash_station_1_installed=bool(kb & (1 << 4)),
      wash_station_2_installed=bool(kb & (1 << 5)),
      temp_controlled_carrier_1_installed=bool(kb & (1 << 6)),
      temp_controlled_carrier_2_installed=bool(kb & (1 << 7)),
      num_pip_channels=resp["kp"],
    )

  async def request_extended_configuration(self) -> ExtendedConfiguration:
    """Request extended configuration (QM command).

    Returns the full instrument configuration matching the AK
    (Set Instrument Configuration) [SFCO.0026] parameter set.
    """

    resp = await self.send_command(
      module="C0",
      command="QM",
      fmt="ka******ke********xt##xa##xw#####xl**xn**xr**xo**xm#####xx#####xu####xv####kc#kr#"
      + "ys###kl###km###ym####yu####yx####",
    )

    def _parse_drive(byte1: int, byte2: int) -> DriveConfiguration:
      return DriveConfiguration(
        pip_installed=bool(byte1 & (1 << 0)),
        iswap_installed=bool(byte1 & (1 << 1)),
        core_96_head_installed=bool(byte1 & (1 << 2)),
        nano_pipettor_installed=bool(byte1 & (1 << 3)),
        dispensing_head_384_installed=bool(byte1 & (1 << 4)),
        xl_channels_installed=bool(byte1 & (1 << 5)),
        tube_gripper_installed=bool(byte1 & (1 << 6)),
        imaging_channel_installed=bool(byte1 & (1 << 7)),
        robotic_channel_installed=bool(byte2 & (1 << 0)),
      )

    ka = resp["ka"]
    return ExtendedConfiguration(
      left_x_drive_large=bool(ka & (1 << 0)),
      ka_core_96_head_installed=bool(ka & (1 << 1)),
      right_x_drive_large=bool(ka & (1 << 2)),
      pump_station_1_installed=bool(ka & (1 << 3)),
      pump_station_2_installed=bool(ka & (1 << 4)),
      wash_station_1_type_cr=bool(ka & (1 << 5)),
      wash_station_2_type_cr=bool(ka & (1 << 6)),
      left_cover_installed=bool(ka & (1 << 7)),
      right_cover_installed=bool(ka & (1 << 8)),
      additional_front_cover_monitoring_installed=bool(ka & (1 << 9)),
      pump_station_3_installed=bool(ka & (1 << 10)),
      multi_channel_nano_pipettor_installed=bool(ka & (1 << 11)),
      dispensing_head_384_installed=bool(ka & (1 << 12)),
      xl_channels_installed=bool(ka & (1 << 13)),
      tube_gripper_installed=bool(ka & (1 << 14)),
      waste_direction_left=bool(ka & (1 << 15)),
      iswap_gripper_wide=bool(ka & (1 << 16)),
      additional_channel_nano_pipettor_installed=bool(ka & (1 << 17)),
      imaging_channel_installed=bool(ka & (1 << 18)),
      robotic_channel_installed=bool(ka & (1 << 19)),
      channel_order_ox_first=bool(ka & (1 << 20)),
      x0_interface_ham_can=bool(ka & (1 << 21)),
      park_heads_with_iswap_off=bool(ka & (1 << 22)),
      configuration_data_3=resp["ke"],
      instrument_size_slots=resp["xt"],
      auto_load_size_slots=resp["xa"],
      tip_waste_x_position=resp["xw"] / 10,
      left_x_drive=_parse_drive(resp["xl"], resp["xn"]),
      right_x_drive=_parse_drive(resp["xr"], resp["xo"]),
      min_iswap_collision_free_position=resp["xm"] / 10,
      max_iswap_collision_free_position=resp["xx"] / 10,
      left_x_arm_width=resp["xu"] / 10,
      right_x_arm_width=resp["xv"] / 10,
      num_xl_channels=resp["kc"],
      num_robotic_channels=resp["kr"],
      min_raster_pitch_pip_channels=resp["ys"] / 10,
      min_raster_pitch_xl_channels=resp["kl"] / 10,
      min_raster_pitch_robotic_channels=resp["km"] / 10,
      pip_maximal_y_position=resp["ym"] / 10,
      left_arm_min_y_position=resp["yu"] / 10,
      right_arm_min_y_position=resp["yx"] / 10,
    )

  async def request_node_names(self):
    """Deprecated: use ``star.driver.request_node_names()``."""

    # TODO: parse res
    return await self.send_command(module="C0", command="RK")

  async def request_deck_data(self):
    """Deprecated: use ``star.driver.request_deck_data()``."""

    # TODO: parse res
    return await self.send_command(module="C0", command="VD")

  # -------------- 3.4 X-Axis control --------------

  # -------------- 3.4.1 Movements --------------

  async def position_left_x_arm_(self, x_position: int = 0):
    """Deprecated: use ``star.left_x_arm.move_to()``."""
    return await self._left_x_arm.move_to(x_position=x_position / 10)

  async def position_right_x_arm_(self, x_position: int = 0):
    """Deprecated: use ``star.right_x_arm.move_to()``."""
    assert self.driver.right_x_arm is not None, "Right X arm is not installed"
    return await self.driver.right_x_arm.move_to(x_position=x_position / 10)

  async def move_left_x_arm_to_position_with_all_attached_components_in_z_safety_position(
    self, x_position: int = 0
  ):
    """Deprecated: use ``star.left_x_arm.move_to_safe()``."""
    return await self._left_x_arm.move_to_safe(x_position=x_position / 10)

  async def move_right_x_arm_to_position_with_all_attached_components_in_z_safety_position(
    self, x_position: int = 0
  ):
    """Deprecated: use ``star.right_x_arm.move_to_safe()``."""
    assert self.driver.right_x_arm is not None, "Right X arm is not installed"
    return await self.driver.right_x_arm.move_to_safe(x_position=x_position / 10)

  # -------------- 3.4.2 X-Area reservation for external access --------------

  async def occupy_and_provide_area_for_external_access(
    self,
    taken_area_identification_number: int = 0,
    taken_area_left_margin: int = 0,
    taken_area_left_margin_direction: int = 0,
    taken_area_size: int = 0,
    arm_preposition_mode_related_to_taken_areas: int = 0,
  ):
    """Deprecated: use ``star.driver.occupy_and_provide_area_for_external_access()``."""

    assert 0 <= taken_area_identification_number <= 9999, (
      "taken_area_identification_number must be between 0 and 9999"
    )
    assert 0 <= taken_area_left_margin <= 99, "taken_area_left_margin must be between 0 and 99"
    assert 0 <= taken_area_left_margin_direction <= 1, (
      "taken_area_left_margin_direction must be between 0 and 1"
    )
    assert 0 <= taken_area_size <= 50000, "taken_area_size must be between 0 and 50000"
    assert 0 <= arm_preposition_mode_related_to_taken_areas <= 2, (
      "arm_preposition_mode_related_to_taken_areas must be between 0 and 2"
    )

    return await self.send_command(
      module="C0",
      command="BA",
      aq=taken_area_identification_number,
      al=taken_area_left_margin,
      ad=taken_area_left_margin_direction,
      ar=taken_area_size,
      ap=arm_preposition_mode_related_to_taken_areas,
    )

  async def release_occupied_area(self, taken_area_identification_number: int = 0):
    """Deprecated: use ``star.driver.release_occupied_area()``."""

    assert 0 <= taken_area_identification_number <= 999, (
      "taken_area_identification_number must be between 0 and 9999"
    )

    return await self.send_command(
      module="C0",
      command="BB",
      aq=taken_area_identification_number,
    )

  async def release_all_occupied_areas(self):
    """Deprecated: use ``star.driver.release_all_occupied_areas()``."""

    return await self.send_command(module="C0", command="BC")

  # -------------- 3.4.3 X-query --------------

  async def request_left_x_arm_position(self) -> float:
    """Deprecated: use ``star.left_x_arm.request_position()``."""
    return await self._left_x_arm.request_position()

  async def request_right_x_arm_position(self) -> float:
    """Deprecated: use ``star.right_x_arm.request_position()``."""
    assert self.driver.right_x_arm is not None, "Right X arm is not installed"
    return await self.driver.right_x_arm.request_position()

  async def request_maximal_ranges_of_x_drives(self):
    """Deprecated: use ``star.driver.request_maximal_ranges_of_x_drives()``."""

    return await self.send_command(module="C0", command="RU")

  async def request_present_wrap_size_of_installed_arms(self):
    """Deprecated: use ``star.driver.request_present_wrap_size_of_installed_arms()``."""

    return await self.send_command(module="C0", command="UA")

  async def request_left_x_arm_last_collision_type(self):
    """Deprecated: use ``star.left_x_arm.last_collision_type()``."""
    return await self._left_x_arm.last_collision_type()

  async def request_right_x_arm_last_collision_type(self) -> bool:
    """Deprecated: use ``star.right_x_arm.last_collision_type()``."""
    assert self.driver.right_x_arm is not None, "Right X arm is not installed"
    return await self.driver.right_x_arm.last_collision_type()

  # -------------- 3.5 Pipetting channel commands --------------

  # -------------- 3.5.1 Initialization --------------

  async def initialize_pip(self):
    """Deprecated: use ``star.pip.backend.initialize_pip()``."""
    dy = (4050 - 2175) // (self.num_channels - 1)
    y_positions = [4050 - i * dy for i in range(self.num_channels)]

    await self.initialize_pipetting_channels(
      x_positions=[
        int(self.extended_conf.tip_waste_x_position * 10)
      ],  # Tip eject waste X position.
      y_positions=y_positions,
      begin_of_tip_deposit_process=int(self._pip.traversal_height * 10),
      end_of_tip_deposit_process=1220,
      z_position_at_end_of_a_command=3600,
      tip_pattern=[True] * self.num_channels,
      tip_type=4,  # TODO: get from tip types
      discarding_method=0,
    )

  async def initialize_pipetting_channels(
    self,
    x_positions: List[int] = [0],
    y_positions: List[int] = [0],
    begin_of_tip_deposit_process: int = 0,
    end_of_tip_deposit_process: int = 0,
    z_position_at_end_of_a_command: int = 3600,
    tip_pattern: List[bool] = [True],
    tip_type: int = 16,
    discarding_method: int = 1,
  ):
    """Deprecated: use ``star.pip.backend.initialize_pipetting_channels()``."""

    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert 0 <= begin_of_tip_deposit_process <= 3600, (
      "begin_of_tip_deposit_process must be between 0 and 3600"
    )
    assert 0 <= end_of_tip_deposit_process <= 3600, (
      "end_of_tip_deposit_process must be between 0 and 3600"
    )
    assert 0 <= z_position_at_end_of_a_command <= 3600, (
      "z_position_at_end_of_a_command must be between 0 and 3600"
    )
    assert 0 <= tip_type <= 99, "tip must be between 0 and 99"
    assert 0 <= discarding_method <= 1, "discarding_method must be between 0 and 1"

    return await self.send_command(
      module="C0",
      command="DI",
      read_timeout=120,
      xp=[f"{xp:05}" for xp in x_positions],
      yp=[f"{yp:04}" for yp in y_positions],
      tp=f"{begin_of_tip_deposit_process:04}",
      tz=f"{end_of_tip_deposit_process:04}",
      te=f"{z_position_at_end_of_a_command:04}",
      tm=[f"{tm:01}" for tm in tip_pattern],
      tt=f"{tip_type:02}",
      ti=discarding_method,
    )

  # -------------- 3.5.2 Tip handling commands using PIP --------------

  @need_iswap_parked
  async def pick_up_tip(
    self,
    x_positions: List[int],
    y_positions: List[int],
    tip_pattern: List[bool],
    tip_type_idx: int,
    begin_tip_pick_up_process: int = 0,
    end_tip_pick_up_process: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    pickup_method: TipPickupMethod = TipPickupMethod.OUT_OF_RACK,
  ):
    """Tip Pick-up

    Args:
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      tip_pattern: Tip pattern (channels involved).
      tip_type_idx: Tip type.
      begin_tip_pick_up_process: Begin of tip picking up process (Z- range) [0.1mm]. Must be
          between 0 and 3600. Default 0.
      end_tip_pick_up_process: End of tip picking up process (Z- range) [0.1mm]. Must be
          between 0 and 3600. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
          of a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3600. Default 3600.
      pickup_method: Pick up method.
    """

    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert 0 <= begin_tip_pick_up_process <= 3600, (
      "begin_tip_pick_up_process must be between 0 and 3600"
    )
    assert 0 <= end_tip_pick_up_process <= 3600, (
      "end_tip_pick_up_process must be between 0 and 3600"
    )
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    )

    return await self.send_command(
      module="C0",
      command="TP",
      tip_pattern=tip_pattern,
      read_timeout=max(120, self.read_timeout),
      xp=[f"{x:05}" for x in x_positions],
      yp=[f"{y:04}" for y in y_positions],
      tm=tip_pattern,
      tt=f"{tip_type_idx:02}",
      tp=f"{begin_tip_pick_up_process:04}",
      tz=f"{end_tip_pick_up_process:04}",
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      td=pickup_method.value,
    )

  @need_iswap_parked
  async def discard_tip(
    self,
    x_positions: List[int],
    y_positions: List[int],
    tip_pattern: List[bool],
    begin_tip_deposit_process: int = 0,
    end_tip_deposit_process: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    z_position_at_end_of_a_command: int = 3600,
    discarding_method: TipDropMethod = TipDropMethod.DROP,
  ):
    """discard tip

    Args:
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      tip_pattern: Tip pattern (channels involved). Must be between 0 and 1. Default 1.
      begin_tip_deposit_process: Begin of tip deposit process (Z- range) [0.1mm]. Must be between
          0 and 3600. Default 0.
      end_tip_deposit_process: End of tip deposit process (Z- range) [0.1mm]. Must be between 0
          and 3600.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
          command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm'). Must
          be between 0 and 3600.
      z-position_at_end_of_a_command: Z-Position at end of a command [0.1mm].
          Must be between 0 and 3600.
      discarding_method: Pick up method Pick up method. 0 = auto selection (see command TT
          parameter tu) 1 = pick up out of rack. 2 = pick up out of wash liquid (slowly). Must be
          between 0 and 2.

    If discarding is PLACE_SHIFT (0), tp/ tz = tip cone end height.
    Otherwise, tp/ tz = stop disk height.
    """

    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert 0 <= begin_tip_deposit_process <= 3600, (
      "begin_tip_deposit_process must be between 0 and 3600"
    )
    assert 0 <= end_tip_deposit_process <= 3600, (
      "end_tip_deposit_process must be between 0 and 3600"
    )
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    )
    assert 0 <= z_position_at_end_of_a_command <= 3600, (
      "z_position_at_end_of_a_command must be between 0 and 3600"
    )

    return await self.send_command(
      module="C0",
      command="TR",
      tip_pattern=tip_pattern,
      read_timeout=max(120, self.read_timeout),
      xp=[f"{x:05}" for x in x_positions],
      yp=[f"{y:04}" for y in y_positions],
      tm=tip_pattern,
      tp=begin_tip_deposit_process,
      tz=end_tip_deposit_process,
      th=minimum_traverse_height_at_beginning_of_a_command,
      te=z_position_at_end_of_a_command,
      ti=discarding_method.value,
    )

  # TODO:(command:TW) Tip Pick-up for DC wash procedure

  # -------------- 3.5.3 Liquid handling commands using PIP --------------

  # TODO:(command:DC) Set multiple dispense values using PIP

  @need_iswap_parked
  async def aspirate_pip(
    self,
    aspiration_type: List[int] = [0],
    tip_pattern: List[bool] = [True],
    x_positions: List[int] = [0],
    y_positions: List[int] = [0],
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    min_z_endpos: int = 3600,
    lld_search_height: List[int] = [0],
    clot_detection_height: List[int] = [60],
    liquid_surface_no_lld: List[int] = [3600],
    pull_out_distance_transport_air: List[int] = [50],
    second_section_height: List[int] = [0],
    second_section_ratio: List[int] = [0],
    minimum_height: List[int] = [3600],
    immersion_depth: List[int] = [0],
    immersion_depth_direction: List[int] = [0],
    surface_following_distance: List[int] = [0],
    aspiration_volumes: List[int] = [0],
    aspiration_speed: List[int] = [500],
    transport_air_volume: List[int] = [0],
    blow_out_air_volume: List[int] = [200],
    pre_wetting_volume: List[int] = [0],
    lld_mode: List[int] = [1],
    gamma_lld_sensitivity: List[int] = [1],
    dp_lld_sensitivity: List[int] = [1],
    aspirate_position_above_z_touch_off: List[int] = [5],
    detection_height_difference_for_dual_lld: List[int] = [0],
    swap_speed: List[int] = [100],
    settling_time: List[int] = [5],
    mix_volume: List[int] = [0],
    mix_cycles: List[int] = [0],
    mix_position_from_liquid_surface: List[int] = [250],
    mix_speed: List[int] = [500],
    mix_surface_following_distance: List[int] = [0],
    limit_curve_index: List[int] = [0],
    tadm_algorithm: bool = False,
    recording_mode: int = 0,
    # For second section aspiration only
    use_2nd_section_aspiration: List[bool] = [False],
    retract_height_over_2nd_section_to_empty_tip: List[int] = [60],
    dispensation_speed_during_emptying_tip: List[int] = [468],
    dosing_drive_speed_during_2nd_section_search: List[int] = [468],
    z_drive_speed_during_2nd_section_search: List[int] = [215],
    cup_upper_edge: List[int] = [3600],
    # deprecated, remove >2026-06
    ratio_liquid_rise_to_tip_deep_in: Optional[List[int]] = None,
    immersion_depth_2nd_section: Optional[List[int]] = None,
  ):
    """aspirate pip

    Aspiration of liquid using PIP.

    It's not really clear what second section aspiration is, but it does not seem to be used
    very often. Probably safe to ignore it.

    LLD restrictions!
      - "dP and Dual LLD" are used in aspiration only. During dispensation LLD is set to OFF.
      - "side touch off" turns LLD & "Z touch off" to OFF , is not available for simultaneous
        Asp/Disp. command

    Args:
      aspiration_type: Type of aspiration (0 = simple;1 = sequence; 2 = cup emptied).
                        Must be between 0 and 2. Default 0.
      tip_pattern: Tip pattern (channels involved). Default True.
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
          a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3600. Default 3600.
      min_z_endpos: Minimum z-Position at end of a command [0.1 mm] (refers to all channels
          independent of tip pattern parameter 'tm'). Must be between 0 and 3600. Default 3600.
      lld_search_height: LLD search height [0.1 mm]. Must be between 0 and 3600. Default 0.
      clot_detection_height: Check height of clot detection above current surface (as computed)
          of the liquid [0.1mm]. Must be between 0 and 500. Default 60.
      liquid_surface_no_lld: Liquid surface at function without LLD [0.1mm]. Must be between 0
          and 3600. Default 3600.
      pull_out_distance_transport_air: pull out distance to take transport air in function
          without LLD [0.1mm]. Must be between 0 and 3600. Default 50.
      second_section_height: Tube 2nd section height measured from "zx" [0.1mm]. Must be
          between 0 and 3600. Default 0.
      second_section_ratio: Tube 2nd section ratio (see Fig. 2 in fw guide). Must be between
          0 and 10000. Default 0.
      minimum_height: Minimum height (maximum immersion depth) [0.1 mm]. Must be between 0 and
          3600. Default 3600.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out
          of liquid). Must be between 0 and 1. Default 0.
      surface_following_distance: Surface following distance during aspiration [0.1mm]. Must
          be between 0 and 3600. Default 0.
      aspiration_volumes: Aspiration volume [0.1ul]. Must be between 0 and 12500. Default 0.
      aspiration_speed: Aspiration speed [0.1ul/s]. Must be between 4 and 5000. Default 500.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 9999. Default 200.
      pre_wetting_volume: Pre-wetting volume. Must be between 0 and 999. Default 0.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be
            between 0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and
            4. Default 1.
      dp_lld_sensitivity: delta p LLD sensitivity (1= high, 4=low). Must be between 1 and
            4. Default 1.
      aspirate_position_above_z_touch_off: aspirate position above Z touch off [0.1mm]. Must
            be between 0 and 100. Default 5.
      detection_height_difference_for_dual_lld: Difference in detection height for dual
            LLD [0.1 mm]. Must be between 0 and 99. Default 0.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1600.
            Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mix_volume: mix volume [0.1ul]. Must be between 0 and 12500. Default 0
      mix_cycles: Number of mix cycles. Must be between 0 and 99. Default 0.
      mix_position_from_liquid_surface: mix position in Z- direction from
          liquid surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 900. Default 250.
      mix_speed: Speed of mix [0.1ul/s]. Must be between 4 and 5000.
          Default 500.
      mix_surface_following_distance: Surface following distance during
          mix [0.1mm]. Must be between 0 and 3600. Default 0.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
          be between 0 and 2. Default 0.
      use_2nd_section_aspiration: 2nd section aspiration. Default False.
      retract_height_over_2nd_section_to_empty_tip: Retract height over 2nd section to empty
          tip [0.1mm]. Must be between 0 and 3600. Default 60.
      dispensation_speed_during_emptying_tip: Dispensation speed during emptying tip [0.1ul/s]
            Must be between 4 and 5000. Default 468.
      dosing_drive_speed_during_2nd_section_search: Dosing drive speed during 2nd section
          search [0.1ul/s]. Must be between 4 and 5000. Default 468.
      z_drive_speed_during_2nd_section_search: Z drive speed during 2nd section search [0.1mm/s].
          Must be between 3 and 1600. Default 215.
      cup_upper_edge: Cup upper edge [0.1mm]. Must be between 0 and 3600. Default 3600.
    """

    if ratio_liquid_rise_to_tip_deep_in is not None:
      warnings.warn(
        "ratio_liquid_rise_to_tip_deep_in is deprecated and will be removed in a future version.",
        DeprecationWarning,
        stacklevel=2,
      )
    if immersion_depth_2nd_section is not None:
      warnings.warn(
        "immersion_depth_2nd_section is deprecated and will be removed in a future version.",
        DeprecationWarning,
        stacklevel=2,
      )

    assert all(0 <= x <= 2 for x in aspiration_type), "aspiration_type must be between 0 and 2"
    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    )
    assert 0 <= min_z_endpos <= 3600, "min_z_endpos must be between 0 and 3600"
    assert all(0 <= x <= 3600 for x in lld_search_height), (
      "lld_search_height must be between 0 and 3600"
    )
    assert all(0 <= x <= 500 for x in clot_detection_height), (
      "clot_detection_height must be between 0 and 500"
    )
    assert all(0 <= x <= 3600 for x in liquid_surface_no_lld), (
      "liquid_surface_no_lld must be between 0 and 3600"
    )
    assert all(0 <= x <= 3600 for x in pull_out_distance_transport_air), (
      "pull_out_distance_transport_air must be between 0 and 3600"
    )
    assert all(0 <= x <= 3600 for x in second_section_height), (
      "second_section_height must be between 0 and 3600"
    )
    assert all(0 <= x <= 10000 for x in second_section_ratio), (
      "second_section_ratio must be between 0 and 10000"
    )
    assert all(0 <= x <= 3600 for x in minimum_height), "minimum_height must be between 0 and 3600"
    assert all(0 <= x <= 3600 for x in immersion_depth), (
      "immersion_depth must be between 0 and 3600"
    )
    assert all(0 <= x <= 1 for x in immersion_depth_direction), (
      "immersion_depth_direction must be between 0 and 1"
    )
    assert all(0 <= x <= 3600 for x in surface_following_distance), (
      "surface_following_distance must be between 0 and 3600"
    )
    assert all(0 <= x <= 12500 for x in aspiration_volumes), (
      "aspiration_volumes must be between 0 and 12500"
    )
    assert all(4 <= x <= 5000 for x in aspiration_speed), (
      "aspiration_speed must be between 4 and 5000"
    )
    assert all(0 <= x <= 500 for x in transport_air_volume), (
      "transport_air_volume must be between 0 and 500"
    )
    assert all(0 <= x <= 9999 for x in blow_out_air_volume), (
      "blow_out_air_volume must be between 0 and 9999"
    )
    assert all(0 <= x <= 999 for x in pre_wetting_volume), (
      "pre_wetting_volume must be between 0 and 999"
    )
    assert all(0 <= x <= 4 for x in lld_mode), "lld_mode must be between 0 and 4"
    assert all(1 <= x <= 4 for x in gamma_lld_sensitivity), (
      "gamma_lld_sensitivity must be between 1 and 4"
    )
    assert all(1 <= x <= 4 for x in dp_lld_sensitivity), (
      "dp_lld_sensitivity must be between 1 and 4"
    )
    assert all(0 <= x <= 100 for x in aspirate_position_above_z_touch_off), (
      "aspirate_position_above_z_touch_off must be between 0 and 100"
    )
    assert all(0 <= x <= 99 for x in detection_height_difference_for_dual_lld), (
      "detection_height_difference_for_dual_lld must be between 0 and 99"
    )
    assert all(3 <= x <= 1600 for x in swap_speed), "swap_speed must be between 3 and 1600"
    assert all(0 <= x <= 99 for x in settling_time), "settling_time must be between 0 and 99"
    assert all(0 <= x <= 12500 for x in mix_volume), "mix_volume must be between 0 and 12500"
    assert all(0 <= x <= 99 for x in mix_cycles), "mix_cycles must be between 0 and 99"
    assert all(0 <= x <= 900 for x in mix_position_from_liquid_surface), (
      "mix_position_from_liquid_surface must be between 0 and 900"
    )
    assert all(4 <= x <= 5000 for x in mix_speed), "mix_speed must be between 4 and 5000"
    assert all(0 <= x <= 3600 for x in mix_surface_following_distance), (
      "mix_surface_following_distance must be between 0 and 3600"
    )
    assert all(0 <= x <= 999 for x in limit_curve_index), (
      "limit_curve_index must be between 0 and 999"
    )
    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"
    assert all(0 <= x <= 3600 for x in retract_height_over_2nd_section_to_empty_tip), (
      "retract_height_over_2nd_section_to_empty_tip must be between 0 and 3600"
    )
    assert all(4 <= x <= 5000 for x in dispensation_speed_during_emptying_tip), (
      "dispensation_speed_during_emptying_tip must be between 4 and 5000"
    )
    assert all(4 <= x <= 5000 for x in dosing_drive_speed_during_2nd_section_search), (
      "dosing_drive_speed_during_2nd_section_search must be between 4 and 5000"
    )
    assert all(3 <= x <= 1600 for x in z_drive_speed_during_2nd_section_search), (
      "z_drive_speed_during_2nd_section_search must be between 3 and 1600"
    )
    assert all(0 <= x <= 3600 for x in cup_upper_edge), "cup_upper_edge must be between 0 and 3600"

    return await self.send_command(
      module="C0",
      command="AS",
      tip_pattern=tip_pattern,
      read_timeout=max(300, self.read_timeout),
      at=[f"{at:01}" for at in aspiration_type],
      tm=tip_pattern,
      xp=[f"{xp:05}" for xp in x_positions],
      yp=[f"{yp:04}" for yp in y_positions],
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{min_z_endpos:04}",
      lp=[f"{lp:04}" for lp in lld_search_height],
      ch=[f"{ch:03}" for ch in clot_detection_height],
      zl=[f"{zl:04}" for zl in liquid_surface_no_lld],
      po=[f"{po:04}" for po in pull_out_distance_transport_air],
      zu=[f"{zu:04}" for zu in second_section_height],
      zr=[f"{zr:05}" for zr in second_section_ratio],
      zx=[f"{zx:04}" for zx in minimum_height],
      ip=[f"{ip:04}" for ip in immersion_depth],
      it=[f"{it}" for it in immersion_depth_direction],
      fp=[f"{fp:04}" for fp in surface_following_distance],
      av=[f"{av:05}" for av in aspiration_volumes],
      as_=[f"{as_:04}" for as_ in aspiration_speed],
      ta=[f"{ta:03}" for ta in transport_air_volume],
      ba=[f"{ba:04}" for ba in blow_out_air_volume],
      oa=[f"{oa:03}" for oa in pre_wetting_volume],
      lm=[f"{lm}" for lm in lld_mode],
      ll=[f"{ll}" for ll in gamma_lld_sensitivity],
      lv=[f"{lv}" for lv in dp_lld_sensitivity],
      zo=[f"{zo:03}" for zo in aspirate_position_above_z_touch_off],
      ld=[f"{ld:02}" for ld in detection_height_difference_for_dual_lld],
      de=[f"{de:04}" for de in swap_speed],
      wt=[f"{wt:02}" for wt in settling_time],
      mv=[f"{mv:05}" for mv in mix_volume],
      mc=[f"{mc:02}" for mc in mix_cycles],
      mp=[f"{mp:03}" for mp in mix_position_from_liquid_surface],
      ms=[f"{ms:04}" for ms in mix_speed],
      mh=[f"{mh:04}" for mh in mix_surface_following_distance],
      gi=[f"{gi:03}" for gi in limit_curve_index],
      gj=tadm_algorithm,
      gk=recording_mode,
      lk=[1 if lk else 0 for lk in use_2nd_section_aspiration],
      ik=[f"{ik:04}" for ik in retract_height_over_2nd_section_to_empty_tip],
      sd=[f"{sd:04}" for sd in dispensation_speed_during_emptying_tip],
      se=[f"{se:04}" for se in dosing_drive_speed_during_2nd_section_search],
      sz=[f"{sz:04}" for sz in z_drive_speed_during_2nd_section_search],
      io=[f"{io:04}" for io in cup_upper_edge],
    )

  @need_iswap_parked
  async def dispense_pip(
    self,
    tip_pattern: List[bool],
    dispensing_mode: List[int] = [0],
    x_positions: List[int] = [0],
    y_positions: List[int] = [0],
    minimum_height: List[int] = [3600],
    lld_search_height: List[int] = [0],
    liquid_surface_no_lld: List[int] = [3600],
    pull_out_distance_transport_air: List[int] = [50],
    immersion_depth: List[int] = [0],
    immersion_depth_direction: List[int] = [0],
    surface_following_distance: List[int] = [0],
    second_section_height: List[int] = [0],
    second_section_ratio: List[int] = [0],
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    min_z_endpos: int = 3600,  #
    dispense_volumes: List[int] = [0],
    dispense_speed: List[int] = [500],
    cut_off_speed: List[int] = [250],
    stop_back_volume: List[int] = [0],
    transport_air_volume: List[int] = [0],
    blow_out_air_volume: List[int] = [200],
    lld_mode: List[int] = [1],
    side_touch_off_distance: int = 1,
    dispense_position_above_z_touch_off: List[int] = [5],
    gamma_lld_sensitivity: List[int] = [1],
    dp_lld_sensitivity: List[int] = [1],
    swap_speed: List[int] = [100],
    settling_time: List[int] = [5],
    mix_volume: List[int] = [0],
    mix_cycles: List[int] = [0],
    mix_position_from_liquid_surface: List[int] = [250],
    mix_speed: List[int] = [500],
    mix_surface_following_distance: List[int] = [0],
    limit_curve_index: List[int] = [0],
    tadm_algorithm: bool = False,
    recording_mode: int = 0,
  ):
    """dispense pip

    Dispensing of liquid using PIP.

    LLD restrictions!
      - "dP and Dual LLD" are used in aspiration only. During dispensation all pressure-based
        LLD is set to OFF.
      - "side touch off" turns LLD & "Z touch off" to OFF , is not available for simultaneous
        Asp/Disp. command

    Args:
      dispensing_mode: Type of dispensing mode 0 = Partial volume in jet mode
        1 = Blow out in jet mode 2 = Partial volume at surface
        3 = Blow out at surface 4 = Empty tip at fix position.
      tip_pattern: Tip pattern (channels involved). Default True.
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      minimum_height: Minimum height (maximum immersion depth) [0.1 mm]. Must be between 0 and
        3600. Default 3600.
      lld_search_height: LLD search height [0.1 mm]. Must be between 0 and 3600. Default 0.
      liquid_surface_no_lld: Liquid surface at function without LLD [0.1mm]. Must be between 0 and
        3600. Default 3600.
      pull_out_distance_transport_air: pull out distance to take transport air in function without
        LLD [0.1mm]. Must be between 0 and 3600. Default 50.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
        liquid). Must be between 0 and 1. Default 0.
      surface_following_distance: Surface following distance during aspiration [0.1mm]. Must be
        between 0 and 3600. Default 0.
      second_section_height: Tube 2nd section height measured from "zx" [0.1mm]. Must be between
        0 and 3600. Default 0.
      second_section_ratio: Tube 2nd section ratio (see Fig. 2 in fw guide). Must be between 0 and
        10000. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm'). Must be
        between 0 and 3600. Default 3600.
      min_z_endpos: Minimum z-Position at end of a command [0.1 mm] (refers to all channels
        independent of tip pattern parameter 'tm'). Must be between 0 and 3600.  Default 3600.
      dispense_volumes: Dispense volume [0.1ul]. Must be between 0 and 12500. Default 0.
      dispense_speed: Dispense speed [0.1ul/s]. Must be between 4 and 5000. Default 500.
      cut_off_speed: Cut-off speed [0.1ul/s]. Must be between 4 and 5000. Default 250.
      stop_back_volume: Stop back volume [0.1ul]. Must be between 0 and 180. Default 0.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 9999. Default 200.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be between 0
        and 4. Default 1.
      side_touch_off_distance: side touch off distance [0.1 mm] (0 = OFF). Must be between 0 and 45.
        Default 1.
      dispense_position_above_z_touch_off: dispense position above Z touch off [0.1 s] (0 = OFF)
        Turns LLD & Z touch off to OFF if ON!. Must be between 0 and 100. Default 5.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
        Default 1.
      dp_lld_sensitivity: delta p LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
        Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1600.
        Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mix_volume: Mix volume [0.1ul]. Must be between 0 and 12500. Default 0.
      mix_cycles: Number of mix cycles. Must be between 0 and 99. Default 0.
      mix_position_from_liquid_surface: Mix position in Z- direction from liquid surface (LLD or
        absolute terms) [0.1mm]. Must be between 0 and 900.  Default 250.
      mix_speed: Speed of mixing [0.1ul/s]. Must be between 4 and 5000. Default 500.
      mix_surface_following_distance: Surface following distance during mixing [0.1mm]. Must be
        between 0 and 3600. Default 0.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
        be between 0 and 2. Default 0.
    """

    assert all(0 <= x <= 4 for x in dispensing_mode), "dispensing_mode must be between 0 and 4"
    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert any(0 <= x <= 3600 for x in minimum_height), "minimum_height must be between 0 and 3600"
    assert any(0 <= x <= 3600 for x in lld_search_height), (
      "lld_search_height must be between 0 and 3600"
    )
    assert any(0 <= x <= 3600 for x in liquid_surface_no_lld), (
      "liquid_surface_no_lld must be between 0 and 3600"
    )
    assert any(0 <= x <= 3600 for x in pull_out_distance_transport_air), (
      "pull_out_distance_transport_air must be between 0 and 3600"
    )
    assert any(0 <= x <= 3600 for x in immersion_depth), (
      "immersion_depth must be between 0 and 3600"
    )
    assert any(0 <= x <= 1 for x in immersion_depth_direction), (
      "immersion_depth_direction must be between 0 and 1"
    )
    assert any(0 <= x <= 3600 for x in surface_following_distance), (
      "surface_following_distance must be between 0 and 3600"
    )
    assert any(0 <= x <= 3600 for x in second_section_height), (
      "second_section_height must be between 0 and 3600"
    )
    assert any(0 <= x <= 10000 for x in second_section_ratio), (
      "second_section_ratio must be between 0 and 10000"
    )
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    )
    assert 0 <= min_z_endpos <= 3600, "min_z_endpos must be between 0 and 3600"
    assert any(0 <= x <= 12500 for x in dispense_volumes), (
      "dispense_volume must be between 0 and 12500"
    )
    assert any(4 <= x <= 5000 for x in dispense_speed), "dispense_speed must be between 4 and 5000"
    assert any(4 <= x <= 5000 for x in cut_off_speed), "cut_off_speed must be between 4 and 5000"
    assert any(0 <= x <= 180 for x in stop_back_volume), (
      "stop_back_volume must be between 0 and 180"
    )
    assert any(0 <= x <= 500 for x in transport_air_volume), (
      "transport_air_volume must be between 0 and 500"
    )
    assert any(0 <= x <= 9999 for x in blow_out_air_volume), (
      "blow_out_air_volume must be between 0 and 9999"
    )
    assert any(0 <= x <= 4 for x in lld_mode), "lld_mode must be between 0 and 4"
    assert 0 <= side_touch_off_distance <= 45, "side_touch_off_distance must be between 0 and 45"
    assert any(0 <= x <= 100 for x in dispense_position_above_z_touch_off), (
      "dispense_position_above_z_touch_off must be between 0 and 100"
    )
    assert any(1 <= x <= 4 for x in gamma_lld_sensitivity), (
      "gamma_lld_sensitivity must be between 1 and 4"
    )
    assert any(1 <= x <= 4 for x in dp_lld_sensitivity), (
      "dp_lld_sensitivity must be between 1 and 4"
    )
    assert any(3 <= x <= 1600 for x in swap_speed), "swap_speed must be between 3 and 1600"
    assert any(0 <= x <= 99 for x in settling_time), "settling_time must be between 0 and 99"
    assert any(0 <= x <= 12500 for x in mix_volume), "mix_volume must be between 0 and 12500"
    assert any(0 <= x <= 99 for x in mix_cycles), "mix_cycles must be between 0 and 99"
    assert any(0 <= x <= 900 for x in mix_position_from_liquid_surface), (
      "mix_position_from_liquid_surface must be between 0 and 900"
    )
    assert any(4 <= x <= 5000 for x in mix_speed), "mix_speed must be between 4 and 5000"
    assert any(0 <= x <= 3600 for x in mix_surface_following_distance), (
      "mix_surface_following_distance must be between 0 and 3600"
    )
    assert any(0 <= x <= 999 for x in limit_curve_index), (
      "limit_curve_index must be between 0 and 999"
    )
    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"

    return await self.send_command(
      module="C0",
      command="DS",
      tip_pattern=tip_pattern,
      read_timeout=max(300, self.read_timeout),
      dm=[f"{dm:01}" for dm in dispensing_mode],
      tm=[f"{tm:01}" for tm in tip_pattern],
      xp=[f"{xp:05}" for xp in x_positions],
      yp=[f"{yp:04}" for yp in y_positions],
      zx=[f"{zx:04}" for zx in minimum_height],
      lp=[f"{lp:04}" for lp in lld_search_height],
      zl=[f"{zl:04}" for zl in liquid_surface_no_lld],
      po=[f"{po:04}" for po in pull_out_distance_transport_air],
      ip=[f"{ip:04}" for ip in immersion_depth],
      it=[f"{it:01}" for it in immersion_depth_direction],
      fp=[f"{fp:04}" for fp in surface_following_distance],
      zu=[f"{zu:04}" for zu in second_section_height],
      zr=[f"{zr:05}" for zr in second_section_ratio],
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{min_z_endpos:04}",
      dv=[f"{dv:05}" for dv in dispense_volumes],
      ds=[f"{ds:04}" for ds in dispense_speed],
      ss=[f"{ss:04}" for ss in cut_off_speed],
      rv=[f"{rv:03}" for rv in stop_back_volume],
      ta=[f"{ta:03}" for ta in transport_air_volume],
      ba=[f"{ba:04}" for ba in blow_out_air_volume],
      lm=[f"{lm:01}" for lm in lld_mode],
      dj=f"{side_touch_off_distance:02}",  #
      zo=[f"{zo:03}" for zo in dispense_position_above_z_touch_off],
      ll=[f"{ll:01}" for ll in gamma_lld_sensitivity],
      lv=[f"{lv:01}" for lv in dp_lld_sensitivity],
      de=[f"{de:04}" for de in swap_speed],
      wt=[f"{wt:02}" for wt in settling_time],
      mv=[f"{mv:05}" for mv in mix_volume],
      mc=[f"{mc:02}" for mc in mix_cycles],
      mp=[f"{mp:03}" for mp in mix_position_from_liquid_surface],
      ms=[f"{ms:04}" for ms in mix_speed],
      mh=[f"{mh:04}" for mh in mix_surface_following_distance],
      gi=[f"{gi:03}" for gi in limit_curve_index],
      gj=tadm_algorithm,  #
      gk=recording_mode,  #
    )

  # TODO:(command:DA) Simultaneous aspiration & dispensation of liquid

  # TODO:(command:DF) Dispense on fly using PIP (Partial volume in jet mode)

  # TODO:(command:LW) DC Wash procedure using PIP

  # -------------- 3.5.5 CoRe gripper commands --------------

  def _get_core_front_back(self):
    core_grippers = self.deck.get_resource("core_grippers")
    assert isinstance(core_grippers, HamiltonCoreGrippers), "core_grippers must be CoReGrippers"
    back_channel_y_center = int(
      (
        core_grippers.get_location_wrt(self.deck).y
        + core_grippers.back_channel_y_center
        + self.core_adjustment.y
      )
    )
    front_channel_y_center = int(
      (
        core_grippers.get_location_wrt(self.deck).y
        + core_grippers.front_channel_y_center
        + self.core_adjustment.y
      )
    )
    assert back_channel_y_center > front_channel_y_center, (
      "back_channel_y_center must be greater than front_channel_y_center"
    )
    assert front_channel_y_center > self.extended_conf.left_arm_min_y_position, (
      f"front_channel_y_center must be greater than {self.extended_conf.left_arm_min_y_position}mm"
    )
    return back_channel_y_center, front_channel_y_center

  def _get_core_x(self) -> float:
    """Get the X coordinate for the CoRe grippers based on deck size and adjustment."""
    core_grippers = self.deck.get_resource("core_grippers")
    assert isinstance(core_grippers, HamiltonCoreGrippers), "core_grippers must be CoReGrippers"
    return core_grippers.get_location_wrt(self.deck).x + self.core_adjustment.x

  async def get_core(self, p1: int, p2: int):
    warnings.warn("Deprecated. Use pick_up_core_gripper_tools instead.", DeprecationWarning)
    assert p1 + 1 == p2, "p2 must be p1 + 1"
    return await self.pick_up_core_gripper_tools(front_channel=p2 - 1)  # p1 here is 1-indexed

  @need_iswap_parked
  async def pick_up_core_gripper_tools(
    self,
    front_channel: int,
    front_offset: Optional[Coordinate] = None,
    back_offset: Optional[Coordinate] = None,
  ):
    """Get CoRe gripper tool from wasteblock mount."""

    if not 0 < front_channel < self.num_channels:
      raise ValueError(f"front_channel must be between 1 and {self.num_channels - 1} (inclusive)")
    back_channel = front_channel - 1

    # Only enforce x equality if both offsets are explicitly provided.
    if front_offset is not None and back_offset is not None and front_offset.x != back_offset.x:
      raise ValueError("front_offset.x and back_offset.x must be the same")

    xs = self._get_core_x() + (front_offset.x if front_offset is not None else 0)

    back_channel_y_center, front_channel_y_center = self._get_core_front_back()
    if back_offset is not None:
      back_channel_y_center += back_offset.y
    if front_offset is not None:
      front_channel_y_center += front_offset.y

    if front_offset is not None and back_offset is not None and front_offset.z != back_offset.z:
      raise ValueError("front_offset.z and back_offset.z must be the same")
    z_offset = 0 if front_offset is None else front_offset.z

    command_output = await self.driver.pick_up_core_gripper_tools(
      x_position=xs,
      back_channel_y=back_channel_y_center,
      front_channel_y=front_channel_y_center,
      back_channel=back_channel,
      front_channel=front_channel,
      begin_z=235.0 + self.core_adjustment.z + z_offset,
      end_z=225.0 + self.core_adjustment.z + z_offset,
      traversal_height=self._iswap.traversal_height,
    )
    self._core_parked = False
    return command_output

  async def put_core(self):
    warnings.warn("Deprecated. Use return_core_gripper_tools instead.", DeprecationWarning)
    return await self.return_core_gripper_tools()

  @need_iswap_parked
  async def return_core_gripper_tools(
    self,
    front_offset: Optional[Coordinate] = None,
    back_offset: Optional[Coordinate] = None,
  ):
    """Put CoRe gripper tool at wasteblock mount."""

    # Only enforce x equality if both offsets are explicitly provided.
    if front_offset is not None and back_offset is not None and back_offset.x != front_offset.x:
      raise ValueError("back_offset.x and front_offset.x must be the same")

    xs = self._get_core_x() + (front_offset.x if front_offset is not None else 0)

    back_channel_y_center, front_channel_y_center = self._get_core_front_back()
    if back_offset is not None:
      back_channel_y_center += back_offset.y
    if front_offset is not None:
      front_channel_y_center += front_offset.y

    if front_offset is not None and back_offset is not None and back_offset.z != front_offset.z:
      raise ValueError("back_offset.z and front_offset.z must be the same")
    z_offset = 0 if front_offset is None else front_offset.z

    command_output = await self.driver.return_core_gripper_tools(
      x_position=xs,
      back_channel_y=back_channel_y_center,
      front_channel_y=front_channel_y_center,
      begin_z=215.0 + self.core_adjustment.z + z_offset,
      end_z=205.0 + self.core_adjustment.z + z_offset,
      traversal_height=self._iswap.traversal_height,
    )
    self._core_parked = True
    return command_output

  async def core_open_gripper(self):
    """Open CoRe gripper tool."""
    return await self.send_command(module="C0", command="ZO")

  @need_iswap_parked
  async def core_get_plate(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_gripping_speed: int = 50,
    z_position: int = 0,
    z_speed: int = 500,
    open_gripper_position: int = 0,
    plate_width: int = 0,
    grip_strength: int = 15,
    minimum_traverse_height_at_beginning_of_a_command: int = 2750,
    minimum_z_position_at_the_command_end: int = 2750,
  ):
    """Get plate with CoRe gripper tool from wasteblock mount."""

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_gripping_speed <= 3700, "y_gripping_speed must be between 0 and 3700"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_speed <= 1287, "z_speed must be between 0 and 1287"
    assert 0 <= open_gripper_position <= 9999, "open_gripper_position must be between 0 and 9999"
    assert 0 <= plate_width <= 9999, "plate_width must be between 0 and 9999"
    assert 0 <= grip_strength <= 99, "grip_strength must be between 0 and 99"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    )
    assert 0 <= minimum_z_position_at_the_command_end <= 3600, (
      "minimum_z_position_at_the_command_end must be between 0 and 3600"
    )

    command_output = await self.send_command(
      module="C0",
      command="ZP",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yv=f"{y_gripping_speed:04}",
      zj=f"{z_position:04}",
      zy=f"{z_speed:04}",
      yo=f"{open_gripper_position:04}",
      yg=f"{plate_width:04}",
      yw=f"{grip_strength:02}",
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{minimum_z_position_at_the_command_end:04}",
    )

    return command_output

  @need_iswap_parked
  async def core_put_plate(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    z_position: int = 0,
    z_press_on_distance: int = 0,
    z_speed: int = 500,
    open_gripper_position: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 2750,
    z_position_at_the_command_end: int = 2750,
    return_tool: bool = True,
  ):
    """Put plate with CoRe gripper tool and return to wasteblock mount."""

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_press_on_distance <= 50, "z_press_on_distance must be between 0 and 999"
    assert 0 <= z_speed <= 1600, "z_speed must be between 0 and 1600"
    assert 0 <= open_gripper_position <= 9999, "open_gripper_position must be between 0 and 9999"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    )
    assert 0 <= z_position_at_the_command_end <= 3600, (
      "z_position_at_the_command_end must be between 0 and 3600"
    )

    command_output = await self.send_command(
      module="C0",
      command="ZR",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      zj=f"{z_position:04}",
      zi=f"{z_press_on_distance:03}",
      zy=f"{z_speed:04}",
      yo=f"{open_gripper_position:04}",
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{z_position_at_the_command_end:04}",
    )

    if return_tool:
      await self.return_core_gripper_tools()

    return command_output

  @need_iswap_parked
  async def core_move_plate_to_position(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    x_acceleration_index: int = 4,
    y_position: int = 0,
    z_position: int = 0,
    z_speed: int = 500,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
  ):
    """Move a plate with CoRe gripper tool."""

    command_output = await self.send_command(
      module="C0",
      command="ZM",
      xs=f"{x_position:05}",
      xd=x_direction,
      xg=x_acceleration_index,
      yj=f"{y_position:04}",
      zj=f"{z_position:04}",
      zy=f"{z_speed:04}",
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
    )

    return command_output

  async def core_read_barcode_of_picked_up_resource(
    self,
    rails: int,
    reading_direction: Literal["vertical", "horizontal", "free"] = "horizontal",
    minimal_z_position: float = 220.0,
    traverse_height_at_beginning_of_a_command: float = 275.0,
    z_speed: float = 128.7,
    allow_manual_input: bool = False,
    labware_description: Optional[str] = None,
  ):
    """Read a 1D barcode using the CoRe gripper scanner.

    Args:
      rails: Rail/slot number where the barcode to be read is located (1-54).
      reading_direction: Direction of barcode reading: 'vertical', 'horizontal', or 'free'. Default is 'horizontal'.
      minimal_z_position: Minimal Z position [mm] during barcode reading (220.0-360.0). Default is 220.0.
      traverse_height_at_beginning_of_a_command: Traverse height at beginning of command [mm] (0.0-360.0). Default is 275.0.
      z_speed: Z speed [mm/s] during barcode reading (0.0-128.7). Default is 128.7.
      allow_manual_input: If True, allows the user to manually input a barcode if scanning fails. Default is False.
      labware_description: Optional description of the labware being scanned, used in the manual input
        prompt to provide context to the user.

    Returns:
      A Barcode if one is successfully read, either by the scanner or via manual user input.

    Raises:
      STARFirmwareError: if the firmware reports an error in the response.
      ValueError: if the response format is unexpected or if no barcode is present and
        ``allow_manual_input`` is False, or if manual input is enabled but the user does not
        provide a barcode.
    """

    assert 1 <= rails <= 54, "rails must be between 1 and 54"
    assert 0 <= minimal_z_position <= 3600, "minimal_z_position must be between 0 and 3600"
    assert 0 <= traverse_height_at_beginning_of_a_command <= 3600, (
      "traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    )
    assert 0 <= z_speed <= 1287, "z_speed must be between 0 and 1287"

    try:
      reading_direction_int = {
        "vertical": 0,
        "horizontal": 1,
        "free": 2,
      }[reading_direction]
    except KeyError as e:
      raise ValueError(
        "reading_direction must be one of 'vertical', 'horizontal', or 'free'"
      ) from e

    command_output = cast(
      str,
      await self.send_command(
        module="C0",
        command="ZB",
        cp=f"{rails:02}",
        zb=f"{round(minimal_z_position * 10):04}",
        th=f"{round(traverse_height_at_beginning_of_a_command * 10):04}",
        zy=f"{round(z_speed * 10):04}",
        bd=reading_direction_int,
        ma="0250 2100 0860 0200",
        mr=0,
        mo="000 000 000 000 000 000 000",
      ),
    )

    if command_output is None:
      raise RuntimeError("No response received from CoRe barcode read command.")

    resp = command_output.strip()
    er_index = resp.find("er")
    if er_index == -1:
      # Unexpected format: no error section present.
      raise ValueError(f"Unexpected CoRe barcode response (no error section): {resp}")

    self.check_fw_string_error(resp)

    # Parse barcode section: firmware returns `bb/LL<barcode>` where LL is length (00..99).
    bb_index = resp.find("bb/", er_index + 7)
    if bb_index == -1:
      # Unexpected layout of barcode section.
      raise ValueError(f"Unexpected CoRe barcode response format: {resp}")

    if len(resp) < bb_index + 5:
      # Need at least 'bb/LL'.
      raise ValueError(f"Unexpected CoRe barcode response format: {resp}")

    bb_len_str = resp[bb_index + 3 : bb_index + 5]
    try:
      bb_len = int(bb_len_str)
    except ValueError as e:
      raise ValueError(f"Invalid CoRe barcode length field 'bb': {bb_len_str}") from e

    barcode_str = resp[bb_index + 5 :].strip()

    # No barcode present.
    if bb_len == 0:
      if allow_manual_input:
        # Provide context and allow the user to recover by entering a barcode manually.
        # Use ANSI color codes to make the prompt stand out in typical terminals.
        YELLOW = "\033[93m"
        BOLD = "\033[1m"
        RESET = "\033[0m"

        lines = [
          f"{YELLOW}{BOLD}=== CoRe barcode scan failed ==={RESET}",
          f"{YELLOW}No barcode read by CoRe scanner.{RESET}",
        ]
        if labware_description is not None:
          lines.append(f"{YELLOW}Labware: {labware_description}{RESET}")
        lines.append(f"{YELLOW}Enter barcode manually (leave blank to abort): {RESET}")
        prompt = "\n".join(lines)

        # Blocking input is acceptable here because this helper is only intended for CLI usage.
        user_barcode = input(prompt).strip()
        if not user_barcode:
          raise ValueError("No barcode read by CoRe scanner and no manual barcode provided.")

        return Barcode(
          data=user_barcode,
          symbology="code128",
          position_on_resource="front",
        )

      raise ValueError("No barcode read by CoRe scanner.")

    if not barcode_str:
      # Length > 0 but no data present.
      raise ValueError(f"Unexpected CoRe barcode response format: {resp}")

    # If the firmware returns more characters than declared, truncate to the declared length.
    if len(barcode_str) > bb_len:
      barcode_str = barcode_str[:bb_len]

    return Barcode(
      data=barcode_str,
      symbology="code128",
      position_on_resource="front",
    )

  # -------------- 3.5.6 Adjustment & movement commands --------------

  async def position_single_pipetting_channel_in_y_direction(
    self, pipetting_channel_index: int, y_position: int
  ):
    """Deprecated: use ``star.pip.backend.position_channels_in_y_direction()``."""
    return await self.driver.pip.position_channels_in_y_direction(
      ys={pipetting_channel_index - 1: y_position / 10}
    )

  async def position_single_pipetting_channel_in_z_direction(
    self, pipetting_channel_index: int, z_position: int
  ):
    """Position single pipetting channel in Z-direction.

    Note that this refers to the point of the tip if a tip is mounted!

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16.
      z_position: y position [0.1mm]. Must be between 0 and 3347. The docs say 3600,but empirically 3347 is the max.
    """

    assert 1 <= pipetting_channel_index <= self.num_channels, (
      "pipetting_channel_index must be between 1 and self.num_channels"
    )
    # docs say 3600, but empirically 3347 is the max
    assert 0 <= z_position <= 3347, "z_position must be between 0 and 3347"

    return await self.send_command(
      module="C0",
      command="KZ",
      pn=f"{pipetting_channel_index:02}",
      zj=f"{z_position:04}",
    )

  async def search_for_teach_in_signal_using_pipetting_channel_n_in_x_direction(
    self, pipetting_channel_index: int, x_position: int
  ):
    """Deprecated: use ``star.driver.left_x_arm.clld_probe_x_position()``."""
    if self.driver.left_x_arm is None:
      raise RuntimeError("left_x_arm not configured")
    return await self.driver.left_x_arm.clld_probe_x_position(
      channel_idx=pipetting_channel_index - 1,
      probing_direction="right",
      end_pos_search=x_position / 10,
    )

  async def spread_pip_channels(self):
    """Deprecated: use ``star.pip.backend.spread_pip_channels()``."""

    return await self.send_command(module="C0", command="JE")

  @need_iswap_parked
  async def move_all_pipetting_channels_to_defined_position(
    self,
    tip_pattern: bool = True,
    x_positions: int = 0,
    y_positions: int = 0,
    minimum_traverse_height_at_beginning_of_command: int = 3600,
    z_endpos: int = 0,
  ):
    """Deprecated: use ``star.pip.backend.move_all_pipetting_channels_to_defined_position()``."""

    if self.left_side_panel_installed:
      min_x = round(self.PIP_X_MIN_WITH_LEFT_SIDE_PANEL * 10)
      if x_positions < min_x:
        raise ValueError(
          f"PIP channel x={x_positions / 10}mm is below the minimum "
          f"{self.PIP_X_MIN_WITH_LEFT_SIDE_PANEL}mm (left side panel is installed)"
        )
    assert 0 <= x_positions <= 25000, "x_positions must be between 0 and 25000"
    assert 0 <= y_positions <= 6500, "y_positions must be between 0 and 6500"
    assert 0 <= minimum_traverse_height_at_beginning_of_command <= 3600, (
      "minimum_traverse_height_at_beginning_of_command must be between 0 and 3600"
    )
    assert 0 <= z_endpos <= 3600, "z_endpos must be between 0 and 3600"

    return await self.send_command(
      module="C0",
      command="JM",
      tm=tip_pattern,
      xp=x_positions,
      yp=y_positions,
      th=minimum_traverse_height_at_beginning_of_command,
      zp=z_endpos,
    )

  # TODO:(command:JR): teach rack using pipetting channel n

  @need_iswap_parked
  async def position_max_free_y_for_n(self, pipetting_channel_index: int):
    """Deprecated: use ``star.pip.backend.position_max_free_y_for_n()``."""

    assert 0 <= pipetting_channel_index < self.num_channels, (
      "pipetting_channel_index must be between 1 and self.num_channels"
    )
    # convert Python's 0-based indexing to Hamilton firmware's 1-based indexing
    pipetting_channel_index = pipetting_channel_index + 1

    return await self.send_command(
      module="C0",
      command="JP",
      pn=f"{pipetting_channel_index:02}",
    )

  async def move_all_channels_in_z_safety(self):
    """Deprecated: use ``star.pip.backend.move_all_channels_in_z_safety()``."""

    return await self.send_command(module="C0", command="ZA")

  # -------------- 3.5.7 PIP query --------------

  # TODO:(command:RY): Request Y-Positions of all pipetting channels

  async def request_x_pos_channel_n(self, pipetting_channel_index: int = 0) -> float:
    """Deprecated: use ``star.pip.channels[n].request_x_pos()``."""
    return await self.driver.pip.channels[pipetting_channel_index].request_x_pos()

  async def request_y_pos_channel_n(self, pipetting_channel_index: int) -> float:
    """Deprecated: use ``star.pip.channels[n].request_y_pos()``."""
    return await self.driver.pip.channels[pipetting_channel_index].request_y_pos()

  # TODO:(command:RZ): Request Z-Positions of all pipetting channels

  async def request_z_pos_channel_n(self, pipetting_channel_index: int) -> float:
    warnings.warn(
      "Deprecated. Use either request_tip_bottom_z_position or request_probe_z_position. "
      "Returning request_tip_bottom_z_position for now."
    )
    return await self.request_tip_bottom_z_position(channel_idx=pipetting_channel_index)

  async def request_tip_bottom_z_position(self, channel_idx: int) -> float:
    """Deprecated: use ``star.pip.channels[n].request_tip_bottom_z_position()``."""
    return await self.driver.pip.channels[channel_idx].request_tip_bottom_z_position()

  async def request_tip_presence(self) -> List[Optional[bool]]:
    """Deprecated: use ``star.pip.backend.request_tip_presence()``."""
    return await self.driver.pip.request_tip_presence()

  async def channels_sense_tip_presence(self) -> List[int]:
    """Deprecated - use `request_tip_presence` instead."""
    warnings.warn(
      "`channels_sense_tip_presence` is deprecated and will be "
      "removed in a future version. Use `request_tip_presence` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return [int(v) for v in await self.request_tip_presence() if v is not None]

  async def request_pip_height_last_lld(self) -> List[float]:
    """Deprecated: use ``star.pip.backend.request_pip_height_last_lld()``."""
    return await self.driver.pip.request_pip_height_last_lld()

  async def request_tadm_status(self):
    """Deprecated: use ``star.pip.channels[n].request_tadm_enabled()``."""
    return {i: await ch.request_tadm_enabled() for i, ch in enumerate(self.driver.pip.channels)}

  # TODO:(command:FS) Request PIP channel dispense on fly status
  # TODO:(command:VE) Request PIP channel 2nd section aspiration data

  # -------------- 3.6 XL channel commands --------------

  # TODO: all XL channel commands

  # -------------- 3.6.1 Initialization XL --------------

  # TODO:(command:LI)

  # -------------- 3.6.2 Tip handling commands using XL --------------

  # TODO:(command:LP)
  # TODO:(command:LR)

  # -------------- 3.6.3 Liquid handling commands using XL --------------

  # TODO:(command:LA)
  # TODO:(command:LD)
  # TODO:(command:LB)
  # TODO:(command:LC)

  # -------------- 3.6.4 Wash commands using XL channel --------------

  # TODO:(command:LE)
  # TODO:(command:LF)

  # -------------- 3.6.5 XL CoRe gripper commands --------------

  # TODO:(command:LT)
  # TODO:(command:LS)
  # TODO:(command:LU)
  # TODO:(command:LV)
  # TODO:(command:LM)
  # TODO:(command:LO)
  # TODO:(command:LG)

  # -------------- 3.6.6 Adjustment & movement commands CP --------------

  # TODO:(command:LY)
  # TODO:(command:LZ)
  # TODO:(command:LH)
  # TODO:(command:LJ)
  # TODO:(command:XM)
  # TODO:(command:LL)
  # TODO:(command:LQ)
  # TODO:(command:LK)
  # TODO:(command:UE)

  # -------------- 3.6.7 XL channel query --------------

  # TODO:(command:UY)
  # TODO:(command:UB)
  # TODO:(command:UZ)
  # TODO:(command:UD)
  # TODO:(command:UT)
  # TODO:(command:UL)
  # TODO:(command:US)
  # TODO:(command:UF)

  # -------------- 3.7 Tube gripper commands --------------

  # TODO: all tube gripper commands

  # -------------- 3.7.1 Movements --------------

  # TODO:(command:FC)
  # TODO:(command:FD)
  # TODO:(command:FO)
  # TODO:(command:FT)
  # TODO:(command:FU)
  # TODO:(command:FJ)
  # TODO:(command:FM)
  # TODO:(command:FW)

  # -------------- 3.7.2 Tube gripper query --------------

  # TODO:(command:FQ)
  # TODO:(command:FN)

  # -------------- 3.8 Imaging channel commands --------------

  # TODO: all imaging commands

  # -------------- 3.8.1 Movements --------------

  # TODO:(command:IC)
  # TODO:(command:ID)
  # TODO:(command:IM)
  # TODO:(command:IJ)

  # -------------- 3.8.2 Imaging channel query --------------

  # TODO:(command:IN)

  # -------------- 3.9 Robotic channel commands --------------

  # -------------- 3.9.1 Initialization --------------

  # TODO:(command:OI)

  # -------------- 3.9.2 Cap handling commands --------------

  # TODO:(command:OP)
  # TODO:(command:OQ)

  # -------------- 3.9.3 Adjustment & movement commands --------------

  # TODO:(command:OY)
  # TODO:(command:OZ)
  # TODO:(command:OH)
  # TODO:(command:OJ)
  # TODO:(command:OX)
  # TODO:(command:OM)
  # TODO:(command:OF)
  # TODO:(command:OG)

  # -------------- 3.9.4 Robotic channel query --------------

  # TODO:(command:OA)
  # TODO:(command:OB)
  # TODO:(command:OC)
  # TODO:(command:OD)
  # TODO:(command:OT)

  # -------------- 3.10 96-Head commands --------------

  async def head96_request_firmware_version(self) -> datetime.date:
    """Request 96 Head firmware version (MEM-READ command)."""
    return await self._star_head96.request_firmware_version()

  async def _head96_request_configuration(self) -> List[str]:
    """Request the 96-head configuration (raw) using the QU command.

    The instrument returns a sequence of positional tokens. This method returns
    those tokens without decoding them, but the following indices are currently
    understood:

        - index 0: clot_monitoring_with_clld
        - index 1: stop_disc_type (codes: 0=core_i, 1=core_ii)
        - index 2: instrument_type (codes: 0=legacy, 1=FM-STAR)
        - indices 3..9: reservable positions (positions 4..10)

    Returns:
      Raw positional tokens extracted from the QU response (the portion after the last ``"au"`` marker).
    """
    resp: str = await self.send_command(module="H0", command="QU")
    return resp.split("au")[-1].split()

  async def head96_request_type(self) -> Head96Information.HeadType:
    """Send QG and return the 96-head type as a human-readable string."""
    type_map: Dict[int, Head96Information.HeadType] = {
      0: "Low volume head",
      1: "High volume head",
      2: "96 head II",
      3: "96 head TADM",
    }
    resp = await self.send_command(module="H0", command="QG", fmt="qg#")
    return type_map.get(resp["qg"], "unknown")

  # -------------- 3.10.1 Initialization --------------

  async def initialize_core_96_head(
    self, trash96: Trash, z_position_at_the_command_end: float = 245.0
  ):
    """Initialize CoRe 96 Head

    Args:
      trash96: Trash object where tips should be disposed. The 96 head will be positioned in the
        center of the trash.
      z_position_at_the_command_end: Z position at the end of the command [mm].
    """
    # The firmware command expects location of tip A1 of the head.
    loc = self._position_96_head_in_resource(trash96)
    self._check_96_position_legal(loc, skip_z=True)

    return await self._star_head96.initialize(
      x=loc.x,
      y=loc.y,
      z=loc.z,
      minimum_height_command_end=z_position_at_the_command_end,
    )

  async def request_core_96_head_initialization_status(self) -> bool:
    return await self._star_head96.request_initialization_status()

  async def head96_dispensing_drive_and_squeezer_driver_initialize(
    self,
    squeezer_speed: float = 15.0,  # mm/sec
    squeezer_acceleration: float = 62.0,  # mm/sec**2,
    squeezer_current_limit: int = 15,
    dispensing_drive_current_limit: int = 7,
  ):
    """Initialize 96-head's dispensing drive AND squeezer drive

    This command...
      - drops any tips that might be on the channel (in place, without moving to trash!)
      - moves the dispense drive to volume position 215.92 uL
        (after tip pickup it will be at 218.19 uL)

    Args:
      squeezer_speed: Speed of the movement (mm/sec). Default is 15.0 mm/sec.
      squeezer_acceleration: Acceleration of the movement (mm/sec**2). Default is 62.0 mm/sec**2.
      squeezer_current_limit: Current limit for the squeezer drive (1-15). Default is 15.
      dispensing_drive_current_limit: Current limit for the dispensing drive (1-15). Default is 7.
    """
    return await self._star_head96.initialize_dispensing_drive_and_squeezer(
      squeezer_speed=squeezer_speed,
      squeezer_acceleration=squeezer_acceleration,
      squeezer_current_limit=squeezer_current_limit,
      dispensing_drive_current_limit=dispensing_drive_current_limit,
    )

  # -------------- 3.10.2 96-Head Movements --------------

  # Conversion factors for 96-Head (mm per increment)
  _head96_z_drive_mm_per_increment = 0.005
  _head96_y_drive_mm_per_increment = 0.015625
  _head96_dispensing_drive_mm_per_increment = 0.001025641026
  _head96_dispensing_drive_uL_per_increment = 0.019340933
  _head96_squeezer_drive_mm_per_increment = 0.0002086672009

  # Z-axis conversions

  def _head96_z_drive_mm_to_increment(self, value_mm: float) -> int:
    """Convert mm to Z-axis hardware increments for 96-head."""
    return round(value_mm / self._head96_z_drive_mm_per_increment)

  def _head96_z_drive_increment_to_mm(self, value_increments: int) -> float:
    """Convert Z-axis hardware increments to mm for 96-head."""
    return round(value_increments * self._head96_z_drive_mm_per_increment, 2)

  # Y-axis conversions

  def _head96_y_drive_mm_to_increment(self, value_mm: float) -> int:
    """Convert mm to Y-axis hardware increments for 96-head."""
    return round(value_mm / self._head96_y_drive_mm_per_increment)

  def _head96_y_drive_increment_to_mm(self, value_increments: int) -> float:
    """Convert Y-axis hardware increments to mm for 96-head."""
    return round(value_increments * self._head96_y_drive_mm_per_increment, 2)

  # Dispensing drive conversions (mm and uL)

  def _head96_dispensing_drive_mm_to_increment(self, value_mm: float) -> int:
    """Convert mm to dispensing drive hardware increments for 96-head."""
    return round(value_mm / self._head96_dispensing_drive_mm_per_increment)

  def _head96_dispensing_drive_increment_to_mm(self, value_increments: int) -> float:
    """Convert dispensing drive hardware increments to mm for 96-head."""
    return round(value_increments * self._head96_dispensing_drive_mm_per_increment, 2)

  def _head96_dispensing_drive_uL_to_increment(self, value_uL: float) -> int:
    """Convert uL to dispensing drive hardware increments for 96-head."""
    return round(value_uL / self._head96_dispensing_drive_uL_per_increment)

  def _head96_dispensing_drive_increment_to_uL(self, value_increments: int) -> float:
    """Convert dispensing drive hardware increments to uL for 96-head."""
    return round(value_increments * self._head96_dispensing_drive_uL_per_increment, 2)

  def _head96_dispensing_drive_mm_to_uL(self, value_mm: float) -> float:
    """Convert dispensing drive mm to uL for 96-head."""
    # Convert mm -> increment -> uL
    increment = self._head96_dispensing_drive_mm_to_increment(value_mm)
    return self._head96_dispensing_drive_increment_to_uL(increment)

  def _head96_dispensing_drive_uL_to_mm(self, value_uL: float) -> float:
    """Convert dispensing drive uL to mm for 96-head."""
    # Convert uL -> increment -> mm
    increment = self._head96_dispensing_drive_uL_to_increment(value_uL)
    return self._head96_dispensing_drive_increment_to_mm(increment)

  # Squeezer drive conversions

  def _head96_squeezer_drive_mm_to_increment(self, value_mm: float) -> int:
    """Convert mm to squeezer drive hardware increments for 96-head."""
    return round(value_mm / self._head96_squeezer_drive_mm_per_increment)

  def _head96_squeezer_drive_increment_to_mm(self, value_increments: int) -> float:
    """Convert squeezer drive hardware increments to mm for 96-head."""
    return round(value_increments * self._head96_squeezer_drive_mm_per_increment, 2)

  # Movement commands

  async def move_core_96_to_safe_position(self):
    """Move CoRe 96 Head to Z safe position."""
    warnings.warn(
      "move_core_96_to_safe_position is deprecated. Use head96_move_to_z_safety instead. "
      "This method will be removed in 2026-04",  # TODO: remove 2026-04
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.head96_move_to_z_safety()

  @_requires_head96
  async def head96_move_to_z_safety(self):
    """Move 96-Head to Z safety coordinate, i.e. z=342.5 mm."""
    return await self._star_head96.move_to_z_safety()

  @_requires_head96
  async def head96_park(
    self,
  ):
    """Park the 96-head.

    Uses firmware default speeds and accelerations.
    """
    return await self._star_head96.park()

  @_requires_head96
  async def head96_move_x(self, x: float):
    """Move the 96-head to a specified X-axis coordinate.

    Note: Unlike head96_move_y and head96_move_z, the X-axis movement does not have
    dedicated speed/acceleration parameters - it uses the EM command which moves
    all axes together.

    Args:
      x: Target X coordinate in mm. Valid range: [-271.0, 974.0]

    Returns:
      Response from the hardware command.

    Raises:
      RuntimeError: If 96-head is not installed.
    """
    current_pos = await self.head96_request_position()
    return await self.head96_move_to_coordinate(
      Coordinate(x, current_pos.y, current_pos.z),
      minimum_height_at_beginning_of_a_command=current_pos.z - 10,
    )

  @_requires_head96
  async def head96_move_y(
    self,
    y: float,
    speed: float = 300.0,
    acceleration: float = 300.0,
    current_protection_limiter: int = 15,
  ):
    """Move the 96-head to a specified Y-axis coordinate.

    Args:
      y: Target Y coordinate in mm. Valid range: [93.75, 562.5]
      speed: Movement speed in mm/sec. Valid range: [0.78125, 390.625 or 625.0]. Default: 300.0
      acceleration: Movement acceleration in mm/sec**2. Valid range: [78.125, 781.25]. Default: 300.0
      current_protection_limiter: Motor current limit (0-15, hardware units). Default: 15

    Returns:
      Response from the hardware command.

    Raises:
      RuntimeError: If 96-head is not installed.
      AssertionError: If firmware info missing or parameters out of range.

    Note:
      Maximum speed varies by firmware version:
      - Pre-2021: 390.625 mm/sec (25,000 increments)
      - 2021+: 625.0 mm/sec (40,000 increments)
      The exact firmware version introducing this change is undocumented.
    """
    assert self._head96_information is not None, (
      "requires 96-head firmware version information for safe operation"
    )

    fw_version = self._head96_information.fw_version

    # Determine speed limit based on firmware version
    # Pre-2021 firmware appears to have lower speed capability or safety limits
    # TODO: Verify exact firmware version and investigate the reason for this change
    y_speed_upper_limit = 390.625 if fw_version.year <= 2021 else 625.0  # mm/sec

    # Validate parameters before hardware communication
    assert 93.75 <= y <= 562.5, "y must be between 93.75 and 562.5 mm"
    assert 0.78125 <= speed <= y_speed_upper_limit, (
      f"speed must be between 0.78125 and {y_speed_upper_limit} mm/sec for firmware version {fw_version}. "
      f"Your firmware version: {self._head96_information.fw_version}. "
      "If this limit seems incorrect, please test cautiously with an empty deck and report "
      "accurate limits + firmware to PyLabRobot: https://github.com/PyLabRobot/pylabrobot/issues"
    )
    assert 78.125 <= acceleration <= 781.25, (
      "acceleration must be between 78.125 and 781.25 mm/sec**2"
    )
    assert isinstance(current_protection_limiter, int) and (
      0 <= current_protection_limiter <= 15
    ), "current_protection_limiter must be an integer between 0 and 15"

    # Convert mm-based parameters to hardware increments using conversion methods
    y_increment = self._head96_y_drive_mm_to_increment(y)
    speed_increment = self._head96_y_drive_mm_to_increment(speed)
    acceleration_increment = self._head96_y_drive_mm_to_increment(acceleration)

    resp = await self.send_command(
      module="H0",
      command="YA",
      ya=f"{y_increment:05}",
      yv=f"{speed_increment:05}",
      yr=f"{acceleration_increment:05}",
      yw=f"{current_protection_limiter:02}",
    )

    return resp

  @_requires_head96
  async def head96_move_z(
    self,
    z: float,
    speed: float = 80.0,
    acceleration: float = 300.0,
    current_protection_limiter: int = 15,
  ):
    """Move the 96-head to a specified Z-axis coordinate.

    Args:
      z: Target Z coordinate in mm. Valid range: [180.5, 342.5]
      speed: Movement speed in mm/sec. Valid range: [0.25, 100.0]. Default: 80.0
      acceleration: Movement acceleration in mm/sec^2. Valid range: [25.0, 500.0]. Default: 300.0
      current_protection_limiter: Motor current limit (0-15, hardware units). Default: 15

    Returns:
      Response from the hardware command.

    Raises:
      RuntimeError: If 96-head is not installed.
      AssertionError: If firmware info missing or parameters out of range.

    Note:
      Firmware versions from 2021+ use 1:1 acceleration scaling, while pre-2021 versions
      use 100x scaling. Both maintain a 100,000 increment upper limit.
    """
    assert self._head96_information is not None, (
      "requires 96-head firmware version information for safe operation"
    )

    fw_version = self._head96_information.fw_version

    # Validate parameters before hardware communication
    assert 180.5 <= z <= 342.5, "z must be between 180.5 and 342.5 mm"
    assert 0.25 <= speed <= 100.0, "speed must be between 0.25 and 100.0 mm/sec"
    assert 25.0 <= acceleration <= 500.0, "acceleration must be between 25.0 and 500.0 mm/sec**2"
    assert isinstance(current_protection_limiter, int) and (
      0 <= current_protection_limiter <= 15
    ), "current_protection_limiter must be an integer between 0 and 15"

    # Determine acceleration scaling based on firmware version
    # Pre-2010 firmware: acceleration parameter is multiplied by 1000
    # 2010+ firmware: acceleration parameter is 1:1 with increment/sec**2
    # TODO: identify exact firmware version that introduced this change
    acceleration_multiplier = 1 if fw_version.year >= 2010 else 0.001

    # Convert mm-based parameters to hardware increments
    z_increment = self._head96_z_drive_mm_to_increment(z)
    speed_increment = self._head96_z_drive_mm_to_increment(speed)
    acceleration_increment = round(
      self._head96_z_drive_mm_to_increment(acceleration) * acceleration_multiplier
    )

    resp = await self.send_command(
      module="H0",
      command="ZA",
      za=f"{z_increment:05}",
      zv=f"{speed_increment:05}",
      zr=f"{acceleration_increment:06}",
      zw=f"{current_protection_limiter:02}",
    )

    return resp

  # -------------- 3.10.2 Tip handling using CoRe 96 Head --------------

  @need_iswap_parked
  @_requires_head96
  async def pick_up_tips_core96(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    tip_type_idx: int,
    tip_pickup_method: int = 2,
    z_deposit_position: int = 3425,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimum_height_command_end: int = 3425,
  ):
    """Pick up tips with CoRe 96 head

    Args:
      x_position: x position [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: y position [0.1mm]. Must be between 1080 and 5600. Default 5600.
      tip_size: Tip type.
      tip_pickup_method: Tip pick up method. 0 = pick up from rack. 1 = pick up from C0Re 96 tip
        wash station. 2 = pick up with " full volume blow out"
      z_deposit_position: Z- deposit position [0.1mm] (collar bearing position) Must bet between
        0 and 3425. Default 3425.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
        of a command [0.1mm]. Must be between 0 and 3425.
      minimum_height_command_end: Minimal height at command end [0.1 mm] Must be between 0 and 3425.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= z_deposit_position <= 3425, "z_deposit_position must be between 0 and 3425"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    )
    assert 0 <= minimum_height_command_end <= 3425, (
      "minimum_height_command_end must be between 0 and 3425"
    )

    return await self.send_command(
      module="C0",
      command="EP",
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      tt=f"{tip_type_idx:02}",
      wu=tip_pickup_method,
      za=f"{z_deposit_position:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimum_height_command_end:04}",
    )

  @need_iswap_parked
  @_requires_head96
  async def discard_tips_core96(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    z_deposit_position: int = 3425,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimum_height_command_end: int = 3425,
  ):
    """Drop tips with CoRe 96 head

    Args:
      x_position: x position [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: y position [0.1mm]. Must be between 1080 and 5600. Default 5600.
      tip_type: Tip type.
      tip_pickup_method: Tip pick up method. 0 = pick up from rack. 1 = pick up from C0Re 96
        tip wash station. 2 = pick up with " full volume blow out"
      z_deposit_position: Z- deposit position [0.1mm] (collar bearing position) Must bet between
        0 and 3425. Default 3425.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
        of a command [0.1mm]. Must be between 0 and 3425.
      minimum_height_command_end: Minimal height at command end [0.1 mm] Must be between 0 and 3425
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= z_deposit_position <= 3425, "z_deposit_position must be between 0 and 3425"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    )
    assert 0 <= minimum_height_command_end <= 3425, (
      "minimum_height_command_end must be between 0 and 3425"
    )

    return await self.send_command(
      module="C0",
      command="ER",
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      za=f"{z_deposit_position:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimum_height_command_end:04}",
    )

  # -------------- 3.10.3 Liquid handling using CoRe 96 Head --------------

  # # # Granular commands # # #

  async def head96_dispensing_drive_move_to_home_volume(
    self,
  ):
    """Move the 96-head dispensing drive into its home position (vol=0.0 uL).

    .. warning::
      This firmware command is known to be broken: the 96-head dispensing drive cannot reach
      vol=0.0 uL, which typically raises
      ``STARFirmwareError: {'CoRe 96 Head': UnknownHamiltonError('Position out of permitted
      area')}``.
    """
    return await self._star_head96.dispensing_drive_move_to_home_volume()

  # # # "Atomic" liquid handling commands # # #

  @need_iswap_parked
  @_requires_head96
  async def aspirate_core_96(
    self,
    aspiration_type: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_positions: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    min_z_endpos: int = 3425,
    lld_search_height: int = 3425,
    liquid_surface_no_lld: int = 3425,
    pull_out_distance_transport_air: int = 3425,
    minimum_height: int = 3425,
    second_section_height: int = 0,
    second_section_ratio: int = 3425,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    surface_following_distance: float = 0,
    aspiration_volumes: int = 0,
    aspiration_speed: int = 1000,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 200,
    pre_wetting_volume: int = 0,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    swap_speed: int = 100,
    settling_time: int = 5,
    mix_volume: int = 0,
    mix_cycles: int = 0,
    mix_position_from_liquid_surface: int = 250,
    mix_surface_following_distance: int = 0,
    speed_of_mix: int = 1000,
    channel_pattern: List[bool] = [True] * 96,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0,
    # Deprecated parameters, to be removed in future versions
    # rm: >2026-01:
    liquid_surface_sink_distance_at_the_end_of_aspiration: float = 0,
    minimal_end_height: int = 3425,
    liquid_surface_at_function_without_lld: int = 3425,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int = 50,
    maximum_immersion_depth: int = 3425,
    surface_following_distance_during_mix: int = 0,
    tube_2nd_section_ratio: int = 3425,
    tube_2nd_section_height_measured_from_zm: int = 0,
  ):
    """aspirate CoRe 96

    Aspiration of liquid using CoRe 96

    Args:
      aspiration_type: Type of aspiration (0 = simple; 1 = sequence; 2 = cup emptied). Must be
          between 0 and 2. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_positions: Y-Position [0.1mm] of well A1. Must be between 1080 and 5600. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
          a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3425. Default 3425.
      min_z_endpos: Minimal height at command end [0.1mm]. Must be between 0 and 3425. Default 3425.
      lld_search_height: LLD search height [0.1mm]. Must be between 0 and 3425. Default 3425.
      liquid_surface_no_lld: Liquid surface at function without LLD [0.1mm]. Must be between 0 and 3425. Default 3425.
      pull_out_distance_transport_air: pull out distance to take transport air in function without LLD [0.1mm]. Must be between 0 and 3425. Default 50.
      minimum_height: Minimum height (maximum immersion depth) [0.1mm]. Must be between 0 and 3425. Default 3425.
      second_section_height: second ratio height. Must be between 0 and 3425. Default 0.
      second_section_ratio: Tube 2nd section ratio (See Fig 2.). Must be between 0 and 10000. Default 3425.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
          liquid). Must be between 0 and 1. Default 0.
      surface_following_distance_at_the_end_of_aspiration: Surface following distance during
          aspiration [0.1mm]. Must be between 0 and 990. Default 0. (renamed for clarity from
          'liquid_surface_sink_distance_at_the_end_of_aspiration' in firmware docs)
      aspiration_volumes: Aspiration volume [0.1ul]. Must be between 0 and 11500. Default 0.
      aspiration_speed: Aspiration speed [0.1ul/s]. Must be between 3 and 5000. Default 1000.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 11500. Default 200.
      pre_wetting_volume: Pre-wetting volume. Must be between 0 and 11500. Default 0.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be between
          0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
          Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1000. Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mix_volume: mix volume [0.1ul]. Must be between 0 and 11500. Default 0.
      mix_cycles: Number of mix cycles. Must be between 0 and 99. Default 0.
      mix_position_from_liquid_surface: mix position in Z- direction from
          liquid surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 990. Default 250.
      mix_surface_following_distance: surface following distance during
          mix [0.1mm]. Must be between 0 and 990. Default 0.
      speed_of_mix: Speed of mix [0.1ul/s]. Must be between 3 and 5000.
          Default 1000.
      todo: TODO: 24 hex chars. Must be between 4 and 5000.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement.
          Must be between 0 and 2. Default 0.
    """

    # # # TODO: delete > 2026-01 # # #
    # deprecated liquid_surface_sink_distance_at_the_end_of_aspiration:
    if liquid_surface_sink_distance_at_the_end_of_aspiration != 0.0:
      surface_following_distance = liquid_surface_sink_distance_at_the_end_of_aspiration
      warnings.warn(
        "The liquid_surface_sink_distance_at_the_end_of_aspiration parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard surface_following_distance parameter instead.\n"
        "liquid_surface_sink_distance_at_the_end_of_aspiration currently superseding "
        "surface_following_distance.",
        DeprecationWarning,
      )

    if minimal_end_height != 3425:
      min_z_endpos = minimal_end_height
      warnings.warn(
        "The minimal_end_height parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard min_z_endpos parameter instead.\n"
        "minimal_end_height currently superseding min_z_endpos.",
        DeprecationWarning,
      )

    if liquid_surface_at_function_without_lld != 3425:
      liquid_surface_no_lld = liquid_surface_at_function_without_lld
      warnings.warn(
        "The liquid_surface_at_function_without_lld parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard liquid_surface_no_lld parameter instead.\n"
        "liquid_surface_at_function_without_lld currently superseding liquid_surface_no_lld.",
        DeprecationWarning,
      )

    if pull_out_distance_to_take_transport_air_in_function_without_lld != 50:
      pull_out_distance_transport_air = (
        pull_out_distance_to_take_transport_air_in_function_without_lld
      )
      warnings.warn(
        "The pull_out_distance_to_take_transport_air_in_function_without_lld parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard pull_out_distance_transport_air parameter instead.\n"
        "pull_out_distance_to_take_transport_air_in_function_without_lld currently superseding pull_out_distance_transport_air.",
        DeprecationWarning,
      )

    if maximum_immersion_depth != 3425:
      minimum_height = maximum_immersion_depth
      warnings.warn(
        "The maximum_immersion_depth parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard minimum_height parameter instead.\n"
        "minimum_height currently superseding maximum_immersion_depth.",
        DeprecationWarning,
      )

    if surface_following_distance_during_mix != 0:
      mix_surface_following_distance = surface_following_distance_during_mix
      warnings.warn(
        "The surface_following_distance_during_mix parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard mix_surface_following_distance parameter instead.\n"
        "surface_following_distance_during_mix currently superseding mix_surface_following_distance.",
        DeprecationWarning,
      )

    if tube_2nd_section_ratio != 3425:
      second_section_ratio = tube_2nd_section_ratio
      warnings.warn(
        "The tube_2nd_section_ratio parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_ratio parameter instead.\n"
        "tube_2nd_section_ratio currently superseding second_section_ratio.",
        DeprecationWarning,
      )

    if tube_2nd_section_height_measured_from_zm != 0:
      second_section_height = tube_2nd_section_height_measured_from_zm
      warnings.warn(
        "The tube_2nd_section_height_measured_from_zm parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard tube_2nd_section_height_measured_from_zm parameter instead.\n"
        "tube_2nd_section_height_measured_from_zm currently superseding tube_2nd_section_height_measured_from_zm.",
        DeprecationWarning,
      )
    # # # delete # # #

    assert 0 <= aspiration_type <= 2, "aspiration_type must be between 0 and 2"
    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_positions <= 5600, "y_positions must be between 1080 and 5600"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    )
    assert 0 <= min_z_endpos <= 3425, "min_z_endpos must be between 0 and 3425"
    assert 0 <= lld_search_height <= 3425, "lld_search_height must be between 0 and 3425"
    assert 0 <= liquid_surface_no_lld <= 3425, "liquid_surface_no_lld must be between 0 and 3425"
    assert 0 <= pull_out_distance_transport_air <= 3425, (
      "pull_out_distance_transport_air must be between 0 and 3425"
    )
    assert 0 <= minimum_height <= 3425, "minimum_height must be between 0 and 3425"
    assert 0 <= second_section_height <= 3425, "second_section_height must be between 0 and 3425"
    assert 0 <= second_section_ratio <= 10000, "second_section_ratio must be between 0 and 10000"
    assert 0 <= immersion_depth <= 3600, "immersion_depth must be between 0 and 3600"
    assert 0 <= immersion_depth_direction <= 1, "immersion_depth_direction must be between 0 and 1"
    assert 0 <= surface_following_distance <= 990, (
      "surface_following_distance must be between 0 and 990"
    )
    assert 0 <= aspiration_volumes <= 11500, "aspiration_volumes must be between 0 and 11500"
    assert 3 <= aspiration_speed <= 5000, "aspiration_speed must be between 3 and 5000"
    assert 0 <= transport_air_volume <= 500, "transport_air_volume must be between 0 and 500"
    assert 0 <= blow_out_air_volume <= 11500, "blow_out_air_volume must be between 0 and 11500"
    assert 0 <= pre_wetting_volume <= 11500, "pre_wetting_volume must be between 0 and 11500"
    assert 0 <= lld_mode <= 4, "lld_mode must be between 0 and 4"
    assert 1 <= gamma_lld_sensitivity <= 4, "gamma_lld_sensitivity must be between 1 and 4"
    assert 3 <= swap_speed <= 1000, "swap_speed must be between 3 and 1000"
    assert 0 <= settling_time <= 99, "settling_time must be between 0 and 99"
    assert 0 <= mix_volume <= 11500, "mix_volume must be between 0 and 11500"
    assert 0 <= mix_cycles <= 99, "mix_cycles must be between 0 and 99"
    assert 0 <= mix_position_from_liquid_surface <= 990, (
      "mix_position_from_liquid_surface must be between 0 and 990"
    )
    assert 0 <= mix_surface_following_distance <= 990, (
      "mix_surface_following_distance must be between 0 and 990"
    )
    assert 3 <= speed_of_mix <= 5000, "speed_of_mix must be between 3 and 5000"
    assert 0 <= limit_curve_index <= 999, "limit_curve_index must be between 0 and 999"

    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"

    # Convert bool list to hex string
    assert len(channel_pattern) == 96, "channel_pattern must be a list of 96 boolean values"
    channel_pattern_bin_str = reversed(["1" if x else "0" for x in channel_pattern])
    channel_pattern_hex = hex(int("".join(channel_pattern_bin_str), 2)).upper()[2:]

    return await self.send_command(
      module="C0",
      command="EA",
      aa=aspiration_type,
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_positions:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{min_z_endpos:04}",
      lz=f"{lld_search_height:04}",
      zt=f"{liquid_surface_no_lld:04}",
      pp=f"{pull_out_distance_transport_air:04}",
      zm=f"{minimum_height:04}",
      zv=f"{second_section_height:04}",
      zq=f"{second_section_ratio:05}",
      iw=f"{immersion_depth:03}",
      ix=immersion_depth_direction,
      fh=f"{surface_following_distance:03}",
      af=f"{aspiration_volumes:05}",
      ag=f"{aspiration_speed:04}",
      vt=f"{transport_air_volume:03}",
      bv=f"{blow_out_air_volume:05}",
      wv=f"{pre_wetting_volume:05}",
      cm=lld_mode,
      cs=gamma_lld_sensitivity,
      bs=f"{swap_speed:04}",
      wh=f"{settling_time:02}",
      hv=f"{mix_volume:05}",
      hc=f"{mix_cycles:02}",
      hp=f"{mix_position_from_liquid_surface:03}",
      mj=f"{mix_surface_following_distance:03}",
      hs=f"{speed_of_mix:04}",
      cw=channel_pattern_hex,
      cr=f"{limit_curve_index:03}",
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  @need_iswap_parked
  @_requires_head96
  async def dispense_core_96(
    self,
    dispensing_mode: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    second_section_height: int = 0,
    second_section_ratio: int = 3425,
    lld_search_height: int = 3425,
    liquid_surface_no_lld: int = 3425,
    pull_out_distance_transport_air: int = 50,
    minimum_height: int = 3425,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    surface_following_distance: float = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    min_z_endpos: int = 3425,
    dispense_volume: int = 0,
    dispense_speed: int = 5000,
    cut_off_speed: int = 250,
    stop_back_volume: int = 0,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 200,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    side_touch_off_distance: int = 0,
    swap_speed: int = 100,
    settling_time: int = 5,
    mixing_volume: int = 0,
    mixing_cycles: int = 0,
    mix_position_from_liquid_surface: int = 250,
    mix_surface_following_distance: int = 0,
    speed_of_mixing: int = 1000,
    channel_pattern: List[bool] = [True] * 12 * 8,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0,
    # Deprecated parameters, to be removed in future versions
    # rm: >2026-01:
    liquid_surface_sink_distance_at_the_end_of_dispense: float = 0,  # surface_following_distance!
    tube_2nd_section_ratio: int = 3425,
    liquid_surface_at_function_without_lld: int = 3425,
    maximum_immersion_depth: int = 3425,
    minimal_end_height: int = 3425,
    mixing_position_from_liquid_surface: int = 250,
    surface_following_distance_during_mixing: int = 0,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int = 50,
    tube_2nd_section_height_measured_from_zm: int = 0,
  ):
    """Dispensing of liquid using CoRe 96

    Args:
      dispensing_mode: Type of dispensing mode 0 = Partial volume in jet mode 1 = Blow out
          in jet mode 2 = Partial volume at surface 3 = Blow out at surface 4 = Empty tip at fix
          position. Must be between 0 and 4. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Y-Position [0.1mm] of well A1. Must be between 1080 and 5600. Default 0.
      minimum_height: Minimum height (maximum immersion depth) [0.1mm]. Must be between 0 and 3425. Default 3425.
      second_section_height: Second ratio height. [0.1mm]. Must be between 0 and 3425. Default 0.
      second_section_ratio: Tube 2nd section ratio (See Fig 2.). Must be between 0 and 10000. Default 3425.
      lld_search_height: LLD search height [0.1mm]. Must be between 0 and 3425. Default 3425.
      liquid_surface_no_lld: Liquid surface at function without LLD [0.1mm]. Must be between 0 and 3425. Default 3425.
      pull_out_distance_transport_air: pull out distance to take transport air in function without LLD [0.1mm]. Must be between 0 and 3425. Default 50.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
          liquid). Must be between 0 and 1. Default 0.
      surface_following_distance: Liquid surface following distance during dispense [0.1mm].
          Must be between 0 and 990. Default 0. (renamed for clarity from
          'liquid_surface_sink_distance_at_the_end_of_dispense' in firmware docs)
      minimum_traverse_height_at_beginning_of_a_command: Minimal traverse height at begin of
          command [0.1mm]. Must be between 0 and 3425. Default 3425.
      min_z_endpos: Minimal height at command end [0.1mm]. Must be between 0 and 3425. Default 3425.
      dispense_volume: Dispense volume [0.1ul]. Must be between 0 and 11500. Default 0.
      dispense_speed: Dispense speed [0.1ul/s]. Must be between 3 and 5000. Default 5000.
      cut_off_speed: Cut-off speed [0.1ul/s]. Must be between 3 and 5000. Default 250.
      stop_back_volume: Stop back volume [0.1ul/s]. Must be between 0 and 999. Default 0.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 11500. Default 200.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be
          between 0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
          Default 1.
      side_touch_off_distance: side touch off distance [0.1 mm] 0 = OFF ( > 0 = ON & turns LLD off)
        Must be between 0 and 45. Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1000. Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mixing_volume: mix volume [0.1ul]. Must be between 0 and 11500. Default 0.
      mixing_cycles: Number of mixing cycles. Must be between 0 and 99. Default 0.
      mix_position_from_liquid_surface: mix position in Z- direction from liquid surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 990. Default 250.
      mix_surface_following_distance: surface following distance during mixing [0.1mm].  Must be between 0 and 990. Default 0.
      speed_of_mixing: Speed of mixing [0.1ul/s]. Must be between 3 and 5000. Default 1000.
      channel_pattern: list of 96 boolean values
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
          be between 0 and 2. Default 0.
    """

    # # # TODO: delete > 2026-01 # # #
    # deprecated liquid_surface_sink_distance_at_the_end_of_aspiration:
    if liquid_surface_sink_distance_at_the_end_of_dispense != 0.0:
      surface_following_distance = liquid_surface_sink_distance_at_the_end_of_dispense
      warnings.warn(
        "The liquid_surface_sink_distance_at_the_end_of_dispense parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard surface_following_distance parameter instead.\n"
        "liquid_surface_sink_distance_at_the_end_of_dispense currently superseding surface_following_distance.",
        DeprecationWarning,
      )

    if tube_2nd_section_ratio != 3425:
      second_section_ratio = tube_2nd_section_ratio
      warnings.warn(
        "The tube_2nd_section_ratio parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_ratio parameter instead.\n"
        "second_section_ratio currently superseding tube_2nd_section_ratio.",
        DeprecationWarning,
      )

    if maximum_immersion_depth != 3425:
      minimum_height = maximum_immersion_depth
      warnings.warn(
        "The maximum_immersion_depth parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard minimum_height parameter instead.\n"
        "minimum_height currently superseding maximum_immersion_depth.",
        DeprecationWarning,
      )

    if liquid_surface_at_function_without_lld != 3425:
      liquid_surface_no_lld = liquid_surface_at_function_without_lld
      warnings.warn(
        "The liquid_surface_at_function_without_lld parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard liquid_surface_no_lld parameter instead.\n"
        "liquid_surface_at_function_without_lld currently superseding liquid_surface_no_lld.",
        DeprecationWarning,
      )

    if minimal_end_height != 3425:
      min_z_endpos = minimal_end_height
      warnings.warn(
        "The minimal_end_height parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard min_z_endpos parameter instead.\n"
        "minimal_end_height currently superseding min_z_endpos.",
        DeprecationWarning,
      )

    if mixing_position_from_liquid_surface != 250:
      mix_position_from_liquid_surface = mixing_position_from_liquid_surface
      warnings.warn(
        "The mixing_position_from_liquid_surface parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard mix_position_from_liquid_surface parameter instead.\n"
        "mixing_position_from_liquid_surface currently superseding mix_position_from_liquid_surface.",
        DeprecationWarning,
      )

    if surface_following_distance_during_mixing != 0:
      mix_surface_following_distance = surface_following_distance_during_mixing
      warnings.warn(
        "The surface_following_distance_during_mixing parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard mix_surface_following_distance parameter instead.\n"
        "mix_surface_following_distance currently superseding surface_following_distance_during_mixing.",
        DeprecationWarning,
      )

    if pull_out_distance_to_take_transport_air_in_function_without_lld != 50:
      pull_out_distance_transport_air = (
        pull_out_distance_to_take_transport_air_in_function_without_lld
      )
      warnings.warn(
        "The pull_out_distance_to_take_transport_air_in_function_without_lld parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard pull_out_distance_transport_air parameter instead.\n"
        "pull_out_distance_to_take_transport_air_in_function_without_lld currently superseding pull_out_distance_transport_air.",
        DeprecationWarning,
      )

    if tube_2nd_section_height_measured_from_zm != 0:
      second_section_height = tube_2nd_section_height_measured_from_zm
      warnings.warn(
        "The tube_2nd_section_height_measured_from_zm parameter is deprecated and will be removed in the future. "
        "Use the Hamilton-standard second_section_height parameter instead.\n"
        "tube_2nd_section_height_measured_from_zm currently superseding second_section_height.",
        DeprecationWarning,
      )
    # # # delete # # #

    assert 0 <= dispensing_mode <= 4, "dispensing_mode must be between 0 and 4"
    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= minimum_height <= 3425, "minimum_height must be between 0 and 3425"
    assert 0 <= second_section_height <= 3425, "second_section_height must be between 0 and 3425"
    assert 0 <= second_section_ratio <= 10000, "second_section_ratio must be between 0 and 10000"
    assert 0 <= lld_search_height <= 3425, "lld_search_height must be between 0 and 3425"
    assert 0 <= liquid_surface_no_lld <= 3425, "liquid_surface_no_lld must be between 0 and 3425"
    assert 0 <= pull_out_distance_transport_air <= 3425, (
      "pull_out_distance_transport_air must be between 0 and 3425"
    )
    assert 0 <= immersion_depth <= 3600, "immersion_depth must be between 0 and 3600"
    assert 0 <= immersion_depth_direction <= 1, "immersion_depth_direction must be between 0 and 1"
    assert 0 <= surface_following_distance <= 990, (
      "surface_following_distance must be between 0 and 990"
    )
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    )
    assert 0 <= min_z_endpos <= 3425, "min_z_endpos must be between 0 and 3425"
    assert 0 <= dispense_volume <= 11500, "dispense_volume must be between 0 and 11500"
    assert 3 <= dispense_speed <= 5000, "dispense_speed must be between 3 and 5000"
    assert 3 <= cut_off_speed <= 5000, "cut_off_speed must be between 3 and 5000"
    assert 0 <= stop_back_volume <= 999, "stop_back_volume must be between 0 and 999"
    assert 0 <= transport_air_volume <= 500, "transport_air_volume must be between 0 and 500"
    assert 0 <= blow_out_air_volume <= 11500, "blow_out_air_volume must be between 0 and 11500"
    assert 0 <= lld_mode <= 4, "lld_mode must be between 0 and 4"
    assert 1 <= gamma_lld_sensitivity <= 4, "gamma_lld_sensitivity must be between 1 and 4"
    assert 0 <= side_touch_off_distance <= 45, "side_touch_off_distance must be between 0 and 45"
    assert 3 <= swap_speed <= 1000, "swap_speed must be between 3 and 1000"
    assert 0 <= settling_time <= 99, "settling_time must be between 0 and 99"
    assert 0 <= mixing_volume <= 11500, "mixing_volume must be between 0 and 11500"
    assert 0 <= mixing_cycles <= 99, "mixing_cycles must be between 0 and 99"
    assert 0 <= mix_position_from_liquid_surface <= 990, (
      "mix_position_from_liquid_surface must be between 0 and 990"
    )
    assert 0 <= mix_surface_following_distance <= 990, (
      "mix_surface_following_distance must be between 0 and 990"
    )
    assert 3 <= speed_of_mixing <= 5000, "speed_of_mixing must be between 3 and 5000"
    assert 0 <= limit_curve_index <= 999, "limit_curve_index must be between 0 and 999"
    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"

    # Convert bool list to hex string
    assert len(channel_pattern) == 96, "channel_pattern must be a list of 96 boolean values"
    channel_pattern_bin_str = reversed(["1" if x else "0" for x in channel_pattern])
    channel_pattern_hex = hex(int("".join(channel_pattern_bin_str), 2)).upper()[2:]

    return await self.send_command(
      module="C0",
      command="ED",
      da=dispensing_mode,
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      zm=f"{minimum_height:04}",
      zv=f"{second_section_height:04}",
      zq=f"{second_section_ratio:05}",
      lz=f"{lld_search_height:04}",
      zt=f"{liquid_surface_no_lld:04}",
      pp=f"{pull_out_distance_transport_air:04}",
      iw=f"{immersion_depth:03}",
      ix=immersion_depth_direction,
      fh=f"{surface_following_distance:03}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{min_z_endpos:04}",
      df=f"{dispense_volume:05}",
      dg=f"{dispense_speed:04}",
      es=f"{cut_off_speed:04}",
      ev=f"{stop_back_volume:03}",
      vt=f"{transport_air_volume:03}",
      bv=f"{blow_out_air_volume:05}",
      cm=lld_mode,
      cs=gamma_lld_sensitivity,
      ej=f"{side_touch_off_distance:02}",
      bs=f"{swap_speed:04}",
      wh=f"{settling_time:02}",
      hv=f"{mixing_volume:05}",
      hc=f"{mixing_cycles:02}",
      hp=f"{mix_position_from_liquid_surface:03}",
      mj=f"{mix_surface_following_distance:03}",
      hs=f"{speed_of_mixing:04}",
      cw=channel_pattern_hex,
      cr=f"{limit_curve_index:03}",
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  # -------------- 3.10.4 Adjustment & movement commands --------------

  @_requires_head96
  async def move_core_96_head_to_defined_position(
    self,
    x: float,
    y: float,
    z: float = 342.5,
    minimum_height_at_beginning_of_a_command: float = 342.5,
  ):
    """Move CoRe 96 Head to defined position

    Args:
      x: X-Position [1mm] of well A1. Must be between -300.0 and 300.0. Default 0.
      y: Y-Position [1mm]. Must be between 108.0 and 560.0. Default 0.
      z: Z-Position [1mm]. Must be between 0 and 560.0. Default 0.
      minimum_height_at_beginning_of_a_command: Minimum height at beginning of a command [1mm]
        (refers to all channels independent of tip pattern parameter 'tm'). Must be between 0 and
        342.5. Default 342.5.
    """

    warnings.warn(  # TODO: remove 2025-02
      "`move_core_96_head_to_defined_position` is deprecated and will be "
      "removed in 2025-02. Use `head96_move_to_coordinate` instead.",
      DeprecationWarning,
      stacklevel=2,
    )

    # TODO: these are values for a STARBackend. Find them for a STARlet.
    self._check_96_position_legal(Coordinate(x, y, z))
    assert 0 <= minimum_height_at_beginning_of_a_command <= 342.5, (
      "minimum_height_at_beginning_of_a_command must be between 0 and 342.5"
    )

    return await self.send_command(
      module="C0",
      command="EM",
      xs=f"{abs(round(x * 10)):05}",
      xd=0 if x >= 0 else 1,
      yh=f"{round(y * 10):04}",
      za=f"{round(z * 10):04}",
      zh=f"{round(minimum_height_at_beginning_of_a_command * 10):04}",
    )

  @_requires_head96
  async def head96_move_to_coordinate(
    self,
    coordinate: Coordinate,
    minimum_height_at_beginning_of_a_command: float = 342.5,
  ):
    """Move STAR(let) 96-Head to defined Coordinate

    Args:
      coordinate: Coordinate of A1 in mm
        - if tip present refers to tip bottom,
        - if not present refers to channel bottom
      minimum_height_at_beginning_of_a_command: Minimum height at beginning of a command [1mm]
        (refers to all channels independent of tip pattern parameter 'tm'). Must be between ? and
        342.5. Default 342.5.
    """
    self._check_96_position_legal(coordinate)

    return await self._star_head96.move_to_coordinate(
      coordinate=coordinate,
      minimum_height_at_beginning_of_a_command=minimum_height_at_beginning_of_a_command,
    )

  HEAD96_DISPENSING_DRIVE_VOL_LIMIT_BOTTOM = 0
  HEAD96_DISPENSING_DRIVE_VOL_LIMIT_TOP = 1244.59

  @_requires_head96
  async def head96_dispensing_drive_move_to_position(
    self,
    position,
    speed: float = 261.1,
    stop_speed: float = 0,
    acceleration: float = 17406.84,
    current_protection_limiter: int = 15,
  ):
    """Move dispensing drive to absolute position in uL

    Args:
      position: Position in uL. Between 0, 1244.59.
      speed: Speed in uL/s. Between 0.1, 1063.75.
      stop_speed: Stop speed in uL/s. Between 0, 1063.75.
      acceleration: Acceleration in uL/s^2. Between 96.7, 17406.84.
      current_protection_limiter: Current protection limiter (0-15), default 15
    """

    await self._star_head96.dispensing_drive_move_to_position(
      position=position,
      speed=speed,
      stop_speed=stop_speed,
      acceleration=acceleration,
      current_protection_limiter=current_protection_limiter,
    )

  async def move_core_96_head_x(self, x_position: float):
    """Move CoRe 96 Head X to absolute position

    .. deprecated::
      Use :meth:`head96_move_x` instead. Will be removed in 2026-06.
    """
    warnings.warn(
      "`move_core_96_head_x` is deprecated. Use `head96_move_x` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.head96_move_x(x_position)

  async def move_core_96_head_y(self, y_position: float):
    """Move CoRe 96 Head Y to absolute position

    .. deprecated::
      Use :meth:`head96_move_y` instead. Will be removed in 2026-06.
    """
    warnings.warn(
      "`move_core_96_head_y` is deprecated. Use `head96_move_y` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.head96_move_y(y_position)

  async def move_core_96_head_z(self, z_position: float):
    """Move CoRe 96 Head Z to absolute position

    .. deprecated::
      Use :meth:`head96_move_z` instead. Will be removed in 2026-06.
    """
    warnings.warn(
      "`move_core_96_head_z` is deprecated. Use `head96_move_z` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.head96_move_z(z_position)

  async def move_96head_to_coordinate(
    self,
    coordinate: Coordinate,
    minimum_height_at_beginning_of_a_command: float = 342.5,
  ):
    """Move STAR(let) 96-Head to defined Coordinate

    .. deprecated::
      Use :meth:`head96_move_to_coordinate` instead. Will be removed in 2026-06.
    """
    warnings.warn(
      "`move_96head_to_coordinate` is deprecated. Use `head96_move_to_coordinate` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.head96_move_to_coordinate(
      coordinate=coordinate,
      minimum_height_at_beginning_of_a_command=minimum_height_at_beginning_of_a_command,
    )

  # -------------- 3.10.5 Wash procedure commands using CoRe 96 Head --------------

  # TODO:(command:EG) Washing tips using CoRe 96 Head
  # TODO:(command:EU) Empty washed tips (end of wash procedure only)

  # -------------- 3.10.6 Query CoRe 96 Head --------------

  async def request_tip_presence_in_core_96_head(self):
    """Deprecated - use `head96_request_tip_presence` instead.

    Returns:
      dictionary with key qh:
        qh: 0 = no tips, 1 = tips are picked up
    """
    warnings.warn(  # TODO: remove 2026-06
      "`request_tip_presence_in_core_96_head` is deprecated and will be "
      "removed in 2026-06 use `head96_request_tip_presence` instead.",
      DeprecationWarning,
      stacklevel=2,
    )

    return await self.send_command(module="C0", command="QH", fmt="qh#")

  async def head96_request_tip_presence(self) -> int:
    """Request Tip presence on the 96-Head

    Note: this command requests this information from the STAR(let)'s
      internal memory.
      It does not directly sense whether tips are present.

    Returns:
      0 = no tips
      1 = firmware believes tips are on the 96-head
    """
    return await self._star_head96.request_tip_presence()

  async def request_position_of_core_96_head(self):
    """Deprecated - use `head96_request_position` instead."""

    warnings.warn(  # TODO: remove 2026-02
      "`request_position_of_core_96_head` is deprecated and will be "
      "removed in 2026-02 use `head96_request_position` instead.",
      DeprecationWarning,
      stacklevel=2,
    )

    return await self.head96_request_position()

  async def head96_request_position(self) -> Coordinate:
    """Request position of CoRe 96 Head (A1 considered to tip length)

    Returns:
      Coordinate: x, y, z in mm
    """
    return await self._star_head96.request_position()

  async def request_core_96_head_channel_tadm_status(self):
    """Request CoRe 96 Head channel TADM Status

    Returns:
      qx: TADM channel status 0 = off 1 = on
    """
    return await self._star_head96.request_tadm_status()

  async def request_core_96_head_channel_tadm_error_status(self):
    """Request CoRe 96 Head channel TADM error status

    Returns:
      vb: error pattern 0 = no error
    """
    return await self._star_head96.request_tadm_error_status()

  async def head96_dispensing_drive_request_position_mm(self) -> float:
    """Request 96 Head dispensing drive position in mm"""
    return await self._star_head96.dispensing_drive_request_position_mm()

  async def head96_dispensing_drive_request_position_uL(self) -> float:
    """Request 96 Head dispensing drive position in uL"""
    return await self._star_head96.dispensing_drive_request_position_uL()

  # -------------- 3.11 384 Head commands --------------

  # -------------- 3.11.1 Initialization --------------

  # -------------- 3.11.2 Tip handling using 384 Head --------------

  # -------------- 3.11.3 Liquid handling using 384 Head --------------

  # -------------- 3.11.4 Adjustment & movement commands --------------

  # -------------- 3.11.5 Wash procedure commands using 384 Head --------------

  # -------------- 3.11.6 Query 384 Head --------------

  # -------------- 3.12 Nano pipettor commands --------------

  # TODO: all nano pipettor commands

  # -------------- 3.12.1 Initialization --------------

  # TODO:(command:NI)
  # TODO:(command:NV)
  # TODO:(command:NP)

  # -------------- 3.12.2 Nano pipettor liquid handling commands --------------

  # TODO:(command:NA)
  # TODO:(command:ND)
  # TODO:(command:NF)

  # -------------- 3.12.3 Nano pipettor wash & clean commands --------------

  # TODO:(command:NW)
  # TODO:(command:NU)

  # -------------- 3.12.4 Nano pipettor adjustment & movements --------------

  # TODO:(command:NM)
  # TODO:(command:NT)

  # -------------- 3.12.5 Nano pipettor query --------------

  # TODO:(command:QL)
  # TODO:(command:QN)
  # TODO:(command:RN)
  # TODO:(command:QQ)
  # TODO:(command:QR)
  # TODO:(command:QO)
  # TODO:(command:RR)
  # TODO:(command:QU)

  # -------------- 3.13 Autoload commands --------------

  # -------------- 3.13.1 Initialization --------------

  async def initialize_auto_load(self):
    """Deprecated - use `initialize_autoload` instead."""
    warnings.warn(  # TODO: remove 2025-02
      "`initialize_auto_load` is deprecated and will be removed "
      "in 2025-02 use  `initialize_autoload` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.initialize_autoload()

  async def initialize_autoload(self):
    """Deprecated: use ``star.autoload._on_setup()``."""
    return await self._autoload._on_setup()

  async def move_auto_load_to_z_save_position(self):
    """Deprecated - use `move_autoload_to_safe_z_position` instead."""

    warnings.warn(  # TODO: remove 2025-02
      "`move_auto_load_to_z_save_position` is deprecated and will be "
      "removed in 2025-02 use `move_autoload_to_safe_z_position` instead.",
      DeprecationWarning,
      stacklevel=2,
    )

    return await self.move_autoload_to_safe_z_position()

  async def move_autoload_to_save_z_position(self):
    """Deprecated - use `move_autoload_to_safe_z_position` instead."""
    warnings.warn(  # TODO: remove 2025-02
      "`move_autoload_to_saVe_z_position` is deprecated and will be "
      "removed in 2025-02 use `move_autoload_to_safe_z_position` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.move_autoload_to_safe_z_position()

  async def move_autoload_to_safe_z_position(self):
    """Deprecated: use ``star.autoload.move_to_safe_z_position()``."""
    return await self._autoload.move_to_safe_z_position()

  async def request_auto_load_slot_position(self):
    """Deprecated - use `request_autoload_track` instead."""
    warnings.warn(  # TODO: remove 2025-02
      "`request_auto_load_slot_position` is deprecated and will be "
      "removed in 2025-02 use `request_autoload_track` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.request_autoload_track()

  async def request_autoload_track(self) -> int:
    """Deprecated: use ``star.autoload.request_track()``."""
    return await self._autoload.request_track()

  async def request_autoload_type(self) -> str:
    """Deprecated: use ``star.autoload.request_type()``."""
    return await self._autoload.request_type()

  # -------------- 3.13.2 Carrier sensing --------------

  def _decode_hex_bitmask_to_track_list(self, mask_hex: str) -> list[int]:
    """Deprecated: use ``STARAutoload._decode_hex_bitmask_to_track_list()``."""
    from pylabrobot.hamilton.liquid_handlers.star.autoload import STARAutoload

    return STARAutoload._decode_hex_bitmask_to_track_list(mask_hex)

  async def request_presence_of_carriers_on_deck(self) -> list[int]:
    """Deprecated: use ``star.autoload.request_presence_of_carriers_on_deck()``."""
    return await self._autoload.request_presence_of_carriers_on_deck()

  async def request_presence_of_carriers_on_loading_tray(self) -> list[int]:
    """Deprecated: use ``star.autoload.request_presence_of_carriers_on_loading_tray()``."""
    return await self._autoload.request_presence_of_carriers_on_loading_tray()

  async def request_presence_of_single_carrier_on_loading_tray(self, track: int) -> bool:
    """Deprecated: use ``star.autoload.request_presence_of_single_carrier_on_loading_tray()``."""
    return await self._autoload.request_presence_of_single_carrier_on_loading_tray(track)

  async def request_single_carrier_presence(self, carrier_position: int):
    """Request single carrier presence on the loading tray (not on deck)"""
    warnings.warn(  # TODO: remove 2025-02
      "`request_single_carrier_presence` is deprecated and will be "
      "removed in 2025-02 use `is_carrier_present_on_loading_tray` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    await self.request_presence_of_single_carrier_on_loading_tray(carrier_position)

  # -------------- 3.13.3 Autoload movement commands --------------

  def _compute_end_rail_of_carrier(self, carrier: Carrier, track_width: float = 22.5) -> int:
    """Compute end rail of carrier based on its location on the deck."""

    carrier_width = carrier.get_location_wrt(self.deck).x - 100 + carrier.get_absolute_size_x()
    carrier_end_rail = int(carrier_width / track_width)

    assert 1 <= carrier_end_rail <= 54, "carrier loading rail must be between 1 and 54"

    return carrier_end_rail

  async def move_autoload_to_slot(self, slot_number: int):
    """deprecated - use `move_autoload_to_track` instead."""

    warnings.warn(  # TODO: remove 2025-02
      "`move_autoload_to_slot` is deprecated and will be "
      "removed in 2025-02 use `move_autoload_to_track` instead.",
      DeprecationWarning,
      stacklevel=2,
    )

    return await self.move_autoload_to_track(track=slot_number)

  async def move_autoload_to_track(self, track: int):
    """Deprecated: use ``star.autoload.move_to_track()``."""
    return await self._autoload.move_to_track(track)

  async def park_autoload(self):
    """Deprecated: use ``star.autoload.park()``."""
    return await self._autoload.park()

  async def take_carrier_out_to_autoload_belt(self, carrier: Carrier):
    """Deprecated: use ``star.autoload.take_carrier_out_to_belt()``."""
    carrier_end_rail = self._compute_end_rail_of_carrier(carrier)
    return await self._autoload.take_carrier_out_to_belt(carrier_end_rail)

  # -------------- 3.13.4 Autoload barcode reading commands --------------

  # 1D barcode symbology bitmask
  # Each symbology corresponds to exactly one bit in the 8-bit barcode type field.
  # Bit definitions from spec:
  #   Bit 0 = ISBT Standard
  #   Bit 1 = Code 128 (Subset B and C)
  #   Bit 2 = Code 39
  #   Bit 3 = Codabar
  #   Bit 4 = Code 2of5 Interleaved
  #   Bit 5 = UPC A/E
  #   Bit 6 = YESN/EAN 8
  #   Bit 7 = (unused / undocumented)

  barcode_1d_symbology_dict: dict[Barcode1DSymbology, str] = {
    "ISBT Standard": "01",  # bit 0 → 0b00000001 → 0x01 → 1
    "Code 128 (Subset B and C)": "02",  # bit 1 → 0b00000010 → 0x02 → 2
    "Code 39": "04",  # bit 2 → 0b00000100 → 0x04 → 4
    "Codebar": "08",  # bit 3 → 0b00001000 → 0x08 → 8
    "Code 2of5 Interleaved": "10",  # bit 4 → 0b00010000 → 0x10 → 16
    "UPC A/E": "20",  # bit 5 → 0b00100000 → 0x20 → 32
    "YESN/EAN 8": "40",  # bit 6 → 0b01000000 → 0x40 → 64
    # Bit 7 → 0b10000000 → 0x80 → 128  (not documented, so omitted)
    "ANY 1D": "7F",  # bits 0-6 → 0b01111111 → 0x7F → 127
  }

  async def set_1d_barcode_type(
    self,
    barcode_symbology: Optional[Barcode1DSymbology],
  ) -> None:
    """Deprecated: use ``star.autoload.set_1d_barcode_type()``."""
    await self._autoload.set_1d_barcode_type(barcode_symbology)

  async def set_barcode_type(
    self,
    ISBT_Standard: bool = True,
    code128: bool = True,
    code39: bool = True,
    codebar: bool = True,
    code2_5: bool = True,
    UPC_AE: bool = True,
    EAN8: bool = True,
  ):
    """deprecated - use set_1d_barcode_type instead"""

    warnings.warn(  # TODO: remove 2025-02
      "`set_barcode_type` is deprecated and will be "
      "removed in 2025-02 use `set_1d_barcode_type` instead.",
      DeprecationWarning,
      stacklevel=2,
    )

    # Encode values into bit pattern. Last bit is always one.
    bt = ""
    for t in [
      ISBT_Standard,
      code128,
      code39,
      codebar,
      code2_5,
      UPC_AE,
      EAN8,
      True,
    ]:
      bt += "1" if t else "0"
    # Convert bit pattern to hex.
    bt_hex = hex(int(bt, base=2))
    return await self.send_command(module="C0", command="CB", bt=bt_hex)

  # TODO:(command:CW) Unload carrier finally

  async def load_carrier_from_tray_and_scan_carrier_barcode(
    self,
    carrier: Carrier,
    carrier_barcode_reading: bool = True,
    barcode_symbology: Optional[Barcode1DSymbology] = None,
    barcode_position: float = 4.3,  # mm
    barcode_reading_window_width: float = 38.0,  # mm
    reading_speed: float = 128.1,  # mm/sec
  ) -> Optional[Barcode]:
    """Deprecated: use ``star.autoload.load_carrier_from_tray_and_scan_carrier_barcode()``."""
    carrier_end_rail = self._compute_end_rail_of_carrier(carrier)
    return await self._autoload.load_carrier_from_tray_and_scan_carrier_barcode(
      carrier_end_rail=carrier_end_rail,
      carrier_barcode_reading=carrier_barcode_reading,
      barcode_symbology=barcode_symbology,
      barcode_position=barcode_position,
      barcode_reading_window_width=barcode_reading_window_width,
      reading_speed=reading_speed,
    )

  async def unload_carrier_after_carrier_barcode_scanning(self):
    """Deprecated: use ``star.autoload.unload_carrier_after_barcode_scanning()``."""
    return await self._autoload.unload_carrier_after_barcode_scanning()

  async def set_carrier_monitoring(self, should_monitor: bool = False):
    """Deprecated: use ``star.autoload.set_carrier_monitoring()``."""
    return await self._autoload.set_carrier_monitoring(should_monitor)

  async def load_carrier_from_autoload_belt(
    self,
    barcode_reading: bool = False,
    barcode_reading_direction: Literal["horizontal", "vertical"] = "horizontal",
    barcode_symbology: Optional[Barcode1DSymbology] = None,
    reading_position_of_first_barcode: float = 63.0,  # mm
    no_container_per_carrier: int = 5,
    distance_between_containers: float = 96.0,  # mm
    width_of_reading_window: float = 38.0,  # mm
    reading_speed: float = 128.1,  # mm/secs
    park_autoload_after: bool = True,
  ) -> dict[int, Optional[Barcode]]:
    """Deprecated: use ``star.autoload.load_carrier_from_belt()``."""
    return await self._autoload.load_carrier_from_belt(
      barcode_reading=barcode_reading,
      barcode_reading_direction=barcode_reading_direction,
      barcode_symbology=barcode_symbology,
      reading_position_of_first_barcode=reading_position_of_first_barcode,
      no_container_per_carrier=no_container_per_carrier,
      distance_between_containers=distance_between_containers,
      width_of_reading_window=width_of_reading_window,
      reading_speed=reading_speed,
      park_autoload_after=park_autoload_after,
    )

  # -------------- 3.13.5 Autoload carrier loading/unloading commands --------------

  async def load_carrier(
    self,
    carrier: Carrier,
    carrier_barcode_reading: bool = True,
    barcode_reading: bool = False,
    barcode_reading_direction: Literal["horizontal", "vertical"] = "horizontal",
    barcode_symbology: Optional[Barcode1DSymbology] = None,
    no_container_per_carrier: int = 5,
    reading_position_of_first_barcode: float = 63.0,  # mm
    distance_between_containers: float = 96.0,  # mm
    width_of_reading_window: float = 38.0,  # mm
    reading_speed: float = 128.1,  # mm/secs
    park_autoload_after: bool = True,
  ) -> dict:
    """Deprecated: use ``star.autoload.load_carrier()``."""
    carrier_end_rail = self._compute_end_rail_of_carrier(carrier)
    return await self._autoload.load_carrier(
      carrier_end_rail=carrier_end_rail,
      carrier_barcode_reading=carrier_barcode_reading,
      barcode_reading=barcode_reading,
      barcode_reading_direction=barcode_reading_direction,
      barcode_symbology=barcode_symbology,
      no_container_per_carrier=no_container_per_carrier,
      reading_position_of_first_barcode=reading_position_of_first_barcode,
      distance_between_containers=distance_between_containers,
      width_of_reading_window=width_of_reading_window,
      reading_speed=reading_speed,
      park_autoload_after=park_autoload_after,
    )

  async def set_loading_indicators(self, bit_pattern: List[bool], blink_pattern: List[bool]):
    """Deprecated: use ``star.autoload.set_loading_indicators()``."""
    return await self._autoload.set_loading_indicators(bit_pattern, blink_pattern)

  async def verify_and_wait_for_carriers(
    self,
    check_interval: float = 1.0,
  ):
    """Deprecated: use ``star.autoload.verify_and_wait_for_carriers()``."""
    # Compute carrier rails from deck children (geometry stays in legacy).
    carrier_rails: List[Tuple[int, int]] = []

    for child in self.deck.children:
      if isinstance(child, Carrier):
        carrier_x = child.get_location_wrt(self.deck).x
        carrier_start_rail = rails_for_x_coordinate(carrier_x)
        carrier_end_rail = rails_for_x_coordinate(carrier_x - 100.0 + child.get_absolute_size_x())
        carrier_start_rail = max(1, min(carrier_start_rail, 54))
        if 1 <= carrier_end_rail <= 54:
          carrier_rails.append((carrier_start_rail, carrier_end_rail))

    return await self._autoload.verify_and_wait_for_carriers(
      carrier_rails=carrier_rails,
      check_interval=check_interval,
    )

  async def unload_carrier(
    self,
    carrier: Carrier,
    park_autoload_after: bool = True,
  ):
    """Deprecated: use ``star.autoload.unload_carrier()``."""
    carrier_end_rail = self._compute_end_rail_of_carrier(carrier)
    return await self._autoload.unload_carrier(
      carrier_end_rail=carrier_end_rail,
      park_autoload_after=park_autoload_after,
    )

  # -------------- 3.14 G1-3/ CR Needle Washer commands --------------

  # TODO: All needle washer commands

  # TODO:(command:WI)
  # TODO:(command:WI)
  # TODO:(command:WS)
  # TODO:(command:WW)
  # TODO:(command:WR)
  # TODO:(command:WC)
  # TODO:(command:QF)

  # -------------- 3.15 Pump unit commands --------------

  async def request_pump_settings(self, pump_station: int = 1):
    """Deprecated: use ``star.wash_station.request_settings()``."""
    # Legacy returned the raw send_command dict; preserve that contract.
    assert 1 <= pump_station <= 3, "pump_station must be between 1 and 3"
    return await self.send_command(module="C0", command="ET", fmt="et#", ep=pump_station)

  # -------------- 3.15.1 DC Wash commands (only for revision up to 01) --------------

  # TODO:(command:FA) Start DC wash procedure
  # TODO:(command:FB) Stop DC wash procedure
  # TODO:(command:FP) Prime DC wash station

  # -------------- 3.15.2 Single chamber pump unit only --------------

  # TODO:(command:EW) Start circulation (single chamber only)
  # TODO:(command:EC) Check circulation (single chamber only)
  # TODO:(command:ES) Stop circulation (single chamber only)
  # TODO:(command:EF) Prime (single chamber only)
  # TODO:(command:EE) Drain & refill (single chamber only)
  # TODO:(command:EB) Fill (single chamber only)
  # TODO:(command:QE) Request single chamber pump station prime status

  # -------------- 3.15.3 Dual chamber pump unit only --------------

  async def initialize_dual_pump_station_valves(self, pump_station: int = 1):
    """Deprecated: use ``star.wash_station.initialize_valves()``."""
    return await self._wash_station.initialize_valves(station=pump_station)

  async def fill_selected_dual_chamber(
    self,
    pump_station: int = 1,
    drain_before_refill: bool = False,
    wash_fluid: int = 1,
    chamber: int = 2,
    waste_chamber_suck_time_after_sensor_change: int = 0,
  ):
    """Deprecated: use ``star.wash_station.fill_chamber()``."""
    return await self._wash_station.fill_chamber(
      station=pump_station,
      drain_before_refill=drain_before_refill,
      wash_fluid=wash_fluid,
      chamber=chamber,
      waste_chamber_suck_time_after_sensor_change=waste_chamber_suck_time_after_sensor_change,
    )

  # TODO:(command:EK) Drain selected chamber

  async def drain_dual_chamber_system(self, pump_station: int = 1):
    """Deprecated: use ``star.wash_station.drain()``."""
    return await self._wash_station.drain(station=pump_station)

  # TODO:(command:QD) Request dual chamber pump station prime status

  # -------------- 3.16 Incubator commands --------------

  # TODO: all incubator commands
  # TODO:(command:HC)
  # TODO:(command:HI)
  # TODO:(command:HF)
  # TODO:(command:RP)

  # -------------- 3.17 iSWAP commands --------------

  # -------------- 3.17.1 Pre & Initialization commands --------------

  async def initialize_iswap(self):
    """Deprecated: use ``star.iswap.initialize()``."""
    return await self._iswap.initialize()

  async def position_components_for_free_iswap_y_range(self):
    """Deprecated: use ``star.pip.backend.position_components_for_free_iswap_y_range()``."""
    return await self.driver.pip.position_components_for_free_iswap_y_range()

  async def move_iswap_x_relative(self, step_size: float, allow_splitting: bool = False):
    """Deprecated: use ``star.iswap.backend.move_relative_x()``."""
    return await self._iswap.move_relative_x(step_size=step_size, allow_splitting=allow_splitting)

  async def move_iswap_y_relative(self, step_size: float, allow_splitting: bool = False):
    """Deprecated: use ``star.iswap.backend.move_relative_y()``.

    Note: this legacy method includes a collision check against channel 0 that is not
    present in the new API. Callers relying on that safety check should perform it
    explicitly before calling ``move_relative_y``.
    """
    # Legacy collision check — kept here because it uses legacy-only helpers.
    if step_size < 0:
      y_pos_channel_0 = await self.request_y_pos_channel_n(0)
      current_y_pos_iswap = await self.iswap_rotation_drive_request_y()
      if current_y_pos_iswap + step_size < y_pos_channel_0:
        raise ValueError(
          f"iSWAP will hit the first (backmost) channel. Current iSWAP Y position: {current_y_pos_iswap} mm, "
          f"first channel Y position: {y_pos_channel_0} mm, requested step size: {step_size} mm"
        )
    return await self._iswap.move_relative_y(step_size=step_size, allow_splitting=allow_splitting)

  async def move_iswap_z_relative(self, step_size: float, allow_splitting: bool = False):
    """Deprecated: use ``star.iswap.backend.move_relative_z()``."""
    return await self._iswap.move_relative_z(step_size=step_size, allow_splitting=allow_splitting)

  async def move_iswap_x(self, x_position: float):
    """Deprecated: use ``star.iswap.move_x()``."""
    return await self._iswap.move_x(x_position)

  async def move_iswap_y(self, y_position: float):
    """Deprecated: use ``star.iswap.move_y()``."""
    return await self._iswap.move_y(y_position)

  async def move_iswap_z(self, z_position: float):
    """Deprecated: use ``star.iswap.move_z()``."""
    return await self._iswap.move_z(z_position)

  async def open_not_initialized_gripper(self):
    """Deprecated: use ``star.iswap.open_not_initialized_gripper()``."""
    return await self._iswap.open_not_initialized_gripper()

  async def iswap_open_gripper(self, open_position: Optional[float] = None):
    """Open gripper.

    Deprecated: use ``star.iswap.open_gripper()``.

    Args:
      open_position: Open position [mm]. Must be between 0 and 999.9.
                     Default 132.0 for iSWAP 4.0 (landscape), 91.0 for iSWAP 3 (portrait).
    """

    if open_position is None:
      open_position = 91.0 if (await self.get_iswap_version()).startswith("3") else 132.0

    assert 0 <= open_position <= 999.9, "open_position must be between 0 and 999.9"

    return await self._iswap.open_gripper(gripper_width=open_position)

  async def iswap_close_gripper(
    self,
    grip_strength: int = 5,
    plate_width: float = 0,
    plate_width_tolerance: float = 0,
  ):
    """Close gripper.

    Deprecated: use ``star.iswap.close_gripper()``.

    The gripper should be at the position plate_width+plate_width_tolerance+2.0mm before sending
    this command.

    Args:
      grip_strength: Grip strength. 0 = low . 9 = high. Default 5.
      plate_width: Plate width [mm]. Must be between 0 and 999.9.
      plate_width_tolerance: Plate width tolerance [mm]. Must be between 0 and 9.9. Default 2.0.
    """

    assert 0 <= grip_strength <= 9, "grip_strength must be between 0 and 9"
    assert 0 <= plate_width <= 999.9, "plate_width must be between 0 and 999.9"
    assert 0 <= plate_width_tolerance <= 9.9, "plate_width_tolerance must be between 0 and 9.9"

    from pylabrobot.hamilton.liquid_handlers.star.iswap import iSWAPBackend

    return await self._iswap.close_gripper(
      gripper_width=plate_width,
      backend_params=iSWAPBackend.CloseGripperParams(
        grip_strength=grip_strength,
        plate_width_tolerance=plate_width_tolerance,
      ),
    )

  # -------------- 3.17.2 Stack handling commands CP --------------

  async def park_iswap(
    self,
    minimum_traverse_height_at_beginning_of_a_command: int = 2840,
  ):
    """Park the iSWAP.

    Deprecated: use ``star.iswap.park()``.

    Args:
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
                of a command [0.1mm]. Must be between 0 and 3600. Default 2840.
    """

    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    )

    from pylabrobot.hamilton.liquid_handlers.star.iswap import iSWAPBackend

    return await self._iswap.park(
      backend_params=iSWAPBackend.ParkParams(
        minimum_traverse_height=minimum_traverse_height_at_beginning_of_a_command / 10,
      ),
    )

  async def iswap_get_plate(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    z_position_at_the_command_end: int = 3600,
    grip_strength: int = 5,
    open_gripper_position: int = 860,
    plate_width: int = 860,
    plate_width_tolerance: int = 860,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
    iswap_fold_up_sequence_at_the_end_of_process: bool = False,
  ):
    """Get plate using iswap.

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y,
            4 =negative X. Must be between 1 and 4. Default 1.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
            a command 0.1mm]. Must be between 0 and 3600. Default 3600.
      z_position_at_the_command_end: Z-Position at the command end [0.1mm]. Must be between 0
            and 3600. Default 3600.
      grip_strength: Grip strength 0 = low .. 9 = high. Must be between 1 and 9. Default 5.
      open_gripper_position: Open gripper position [0.1mm]. Must be between 0 and 9999.
            Default 860.
      plate_width: plate width [0.1mm]. Must be between 0 and 9999. Default 860.
      plate_width_tolerance: plate width tolerance [0.1mm]. Must be between 0 and 99. Default 860.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
                               Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4. Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4. Default 1.
      iswap_fold_up_sequence_at_the_end_of_process: fold up sequence at the end of process. Default False.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 1 <= grip_direction <= 4, "grip_direction must be between 1 and 4"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    )
    assert 0 <= z_position_at_the_command_end <= 3600, (
      "z_position_at_the_command_end must be between 0 and 3600"
    )
    assert 1 <= grip_strength <= 9, "grip_strength must be between 1 and 9"
    assert 0 <= open_gripper_position <= 9999, "open_gripper_position must be between 0 and 9999"
    assert 0 <= plate_width <= 9999, "plate_width must be between 0 and 9999"
    assert 0 <= plate_width_tolerance <= 99, "plate_width_tolerance must be between 0 and 99"
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"
    assert 0 <= acceleration_index_high_acc <= 4, (
      "acceleration_index_high_acc must be between 0 and 4"
    )
    assert 0 <= acceleration_index_low_acc <= 4, (
      "acceleration_index_low_acc must be between 0 and 4"
    )

    command_output = await self.send_command(
      module="C0",
      command="PP",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yd=y_direction,
      zj=f"{z_position:04}",
      zd=z_direction,
      gr=grip_direction,
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{z_position_at_the_command_end:04}",
      gw=grip_strength,
      go=f"{open_gripper_position:04}",
      gb=f"{plate_width:04}",
      gt=f"{plate_width_tolerance:02}",
      ga=collision_control_level,
      # xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}",
      gc=iswap_fold_up_sequence_at_the_end_of_process,
    )

    # Once the command has completed successfully, set _iswap_parked to false
    self._iswap._parked = False
    return command_output

  async def iswap_put_plate(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    z_position_at_the_command_end: int = 3600,
    open_gripper_position: int = 860,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
    iswap_fold_up_sequence_at_the_end_of_process: bool = False,
  ):
    """put plate

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y, 4 = negative
            X. Must be between 1 and 4. Default 1.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
            command 0.1mm]. Must be between 0 and 3600. Default 3600.
      z_position_at_the_command_end: Z-Position at the command end [0.1mm]. Must be between 0 and
            3600. Default 3600.
      open_gripper_position: Open gripper position [0.1mm]. Must be between 0 and 9999. Default
            860.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
            Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4.
            Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4.
            Default 1.
      iswap_fold_up_sequence_at_the_end_of_process: fold up sequence at the end of process. Default False.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 1 <= grip_direction <= 4, "grip_direction must be between 1 and 4"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    )
    assert 0 <= z_position_at_the_command_end <= 3600, (
      "z_position_at_the_command_end must be between 0 and 3600"
    )
    assert 0 <= open_gripper_position <= 9999, "open_gripper_position must be between 0 and 9999"
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"
    assert 0 <= acceleration_index_high_acc <= 4, (
      "acceleration_index_high_acc must be between 0 and 4"
    )
    assert 0 <= acceleration_index_low_acc <= 4, (
      "acceleration_index_low_acc must be between 0 and 4"
    )

    command_output = await self.send_command(
      module="C0",
      command="PR",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yd=y_direction,
      zj=f"{z_position:04}",
      zd=z_direction,
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{z_position_at_the_command_end:04}",
      gr=grip_direction,
      go=f"{open_gripper_position:04}",
      ga=collision_control_level,
      # xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}"
      gc=iswap_fold_up_sequence_at_the_end_of_process,
    )

    # Once the command has completed successfully, set _iswap_parked to false
    self._iswap._parked = False
    return command_output

  async def request_iswap_rotation_drive_position_increments(self) -> int:
    """Deprecated: use ``star.iswap.request_rotation_drive_position_increments()``."""
    return await self._iswap.request_rotation_drive_position_increments()

  async def request_iswap_rotation_drive_orientation(self) -> "RotationDriveOrientation":
    """Deprecated: use ``star.iswap.request_rotation_drive_orientation()``."""
    new_orient = await self._iswap.request_rotation_drive_orientation()
    return STARBackend.RotationDriveOrientation(new_orient.value)

  async def request_iswap_wrist_drive_position_increments(self) -> int:
    """Deprecated: use ``star.iswap.request_wrist_drive_position_increments()``."""
    return await self._iswap.request_wrist_drive_position_increments()

  async def request_iswap_wrist_drive_orientation(self) -> "WristDriveOrientation":
    """Deprecated: use ``star.iswap.request_wrist_drive_orientation()``."""
    new_orient = await self._iswap.request_wrist_drive_orientation()
    return STARBackend.WristDriveOrientation(new_orient.value)

  async def iswap_rotate(
    self,
    rotation_drive: "RotationDriveOrientation",
    grip_direction: GripDirection,
    gripper_velocity: int = 55_000,
    gripper_acceleration: int = 170,
    gripper_protection: Literal[0, 1, 2, 3, 4, 5, 6, 7] = 5,
    wrist_velocity: int = 48_000,
    wrist_acceleration: int = 145,
    wrist_protection: Literal[0, 1, 2, 3, 4, 5, 6, 7] = 5,
  ):
    """Deprecated: use ``star.iswap.rotate()``."""
    return await self._iswap.rotate(
      rotation_drive=rotation_drive,  # type: ignore[arg-type]
      grip_direction=grip_direction,  # type: ignore[arg-type]
      gripper_velocity=gripper_velocity,
      gripper_acceleration=gripper_acceleration,
      gripper_protection=gripper_protection,
      wrist_velocity=wrist_velocity,
      wrist_acceleration=wrist_acceleration,
      wrist_protection=wrist_protection,
    )

  async def iswap_dangerous_release_break(self):
    """Deprecated: use ``star.iswap.dangerous_release_brake()``."""
    return await self._iswap.dangerous_release_brake()

  async def iswap_reengage_break(self):
    """Deprecated: use ``star.iswap.reengage_brake()``."""
    return await self._iswap.reengage_brake()

  async def iswap_initialize_z_axis(self):
    """Deprecated: use ``star.iswap.initialize_z_axis()``."""
    return await self._iswap.initialize_z_axis()

  async def move_plate_to_position(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
  ):
    """Move plate to position.

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y, 4 = negative
            X. Must be between 1 and 4. Default 1.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
            command 0.1mm]. Must be between 0 and 3600. Default 3600.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
            Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4. Default 4.
      acceleration_index_low_acc: acceleration index low acc. Must be between 0 and 4. Default 1.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 1 <= grip_direction <= 4, "grip_direction must be between 1 and 4"
    assert 0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600, (
      "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    )
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"
    assert 0 <= acceleration_index_high_acc <= 4, (
      "acceleration_index_high_acc must be between 0 and 4"
    )
    assert 0 <= acceleration_index_low_acc <= 4, (
      "acceleration_index_low_acc must be between 0 and 4"
    )

    command_output = await self.send_command(
      module="C0",
      command="PM",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yd=y_direction,
      zj=f"{z_position:04}",
      zd=z_direction,
      gr=grip_direction,
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ga=collision_control_level,
      xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}",
    )
    # Once the command has completed successfully, set _iswap_parked to false
    self._iswap._parked = False
    return command_output

  async def collapse_gripper_arm(
    self,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    iswap_fold_up_sequence_at_the_end_of_process: bool = False,
  ):
    """Deprecated: use ``star.iswap.collapse_gripper_arm()``."""
    return await self._iswap.collapse_gripper_arm(
      minimum_traverse_height=minimum_traverse_height_at_beginning_of_a_command / 10,
      fold_up_at_end=iswap_fold_up_sequence_at_the_end_of_process,
    )

  # -------------- 3.17.3 Hotel handling commands --------------

  # implemented in UnSafe class

  # -------------- 3.17.4 Barcode commands --------------

  # TODO:(command:PB) Read barcode using iSWAP

  # -------------- 3.17.5 Teach in commands --------------

  async def prepare_iswap_teaching(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    location: int = 0,
    hotel_depth: int = 1300,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
  ):
    """Deprecated: use ``star.iswap.prepare_teaching()``."""
    return await self._iswap.prepare_teaching(
      x_position=x_position / 10,
      x_direction=x_direction,
      y_position=y_position / 10,
      y_direction=y_direction,
      z_position=z_position / 10,
      z_direction=z_direction,
      location=location,
      hotel_depth=hotel_depth / 10,
      grip_direction=grip_direction,
      minimum_traverse_height=minimum_traverse_height_at_beginning_of_a_command / 10,
      collision_control_level=collision_control_level,
      acceleration_index_high_acc=acceleration_index_high_acc,
      acceleration_index_low_acc=acceleration_index_low_acc,
    )

  async def get_logic_iswap_position(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    location: int = 0,
    hotel_depth: int = 1300,
    grip_direction: int = 1,
    collision_control_level: int = 1,
  ):
    """Deprecated: use ``star.iswap.get_logic_position()``."""
    return await self._iswap.get_logic_position(
      x_position=x_position / 10,
      x_direction=x_direction,
      y_position=y_position / 10,
      y_direction=y_direction,
      z_position=z_position / 10,
      z_direction=z_direction,
      location=location,
      hotel_depth=hotel_depth / 10,
      grip_direction=grip_direction,
      collision_control_level=collision_control_level,
    )

  # -------------- 3.17.6 iSWAP query --------------

  async def request_iswap_in_parking_position(self):
    """Deprecated: use ``star.iswap.request_in_parking_position()``."""
    return await self._iswap.request_in_parking_position()

  async def request_plate_in_iswap(self) -> bool:
    """Deprecated: use ``star.iswap.is_gripper_closed()``."""
    return await self._iswap.is_gripper_closed()

  async def request_iswap_position(self) -> Coordinate:
    """Deprecated: use ``star.iswap.get_gripper_location()``."""
    return (await self._iswap.request_gripper_location()).location

  async def iswap_rotation_drive_request_y(self) -> float:
    """Deprecated: use ``star.iswap.rotation_drive_request_y()``."""
    return await self._iswap.rotation_drive_request_y()

  async def request_iswap_initialization_status(self) -> bool:
    """Deprecated: use ``star.iswap.request_initialization_status()``."""
    return await self._iswap.request_initialization_status()

  async def request_iswap_version(self) -> str:
    """Deprecated: use ``star.iswap.version`` (property, available after setup)."""
    return await self._iswap._request_version()

  # -------------- 3.18 Cover and port control --------------

  async def lock_cover(self):
    """Deprecated: use ``star.cover.lock()``."""
    return await self._cover.lock()

  async def unlock_cover(self):
    """Deprecated: use ``star.cover.unlock()``."""
    return await self._cover.unlock()

  async def disable_cover_control(self):
    """Deprecated: use ``star.cover.disable()``."""
    return await self._cover.disable()

  async def enable_cover_control(self):
    """Deprecated: use ``star.cover.enable()``."""
    return await self._cover.enable()

  async def set_cover_output(self, output: int = 1):
    """Deprecated: use ``star.cover.set_output()``."""
    return await self._cover.set_output(output=output)

  async def reset_output(self, output: int = 1):
    """Deprecated: use ``star.cover.reset_output()``."""
    return await self._cover.reset_output(output=output)

  async def request_cover_open(self) -> bool:
    """Deprecated: use ``star.cover.is_open()``."""
    return await self._cover.is_open()

  # -------------- Extra - Probing labware with STAR - making STAR into a CMM --------------

  y_drive_mm_per_increment = 0.046302082
  z_drive_mm_per_increment = 0.01072765

  dispensing_drive_vol_per_increment = 0.046876  # uL / increment
  dispensing_drive_mm_per_increment = 0.002734375

  @staticmethod
  def mm_to_y_drive_increment(value_mm: float) -> int:
    return round(value_mm / STARBackend.y_drive_mm_per_increment)

  @staticmethod
  def y_drive_increment_to_mm(value_mm: int) -> float:
    return round(value_mm * STARBackend.y_drive_mm_per_increment, 2)

  @staticmethod
  def mm_to_z_drive_increment(value_mm: float) -> int:
    return round(value_mm / STARBackend.z_drive_mm_per_increment)

  @staticmethod
  def z_drive_increment_to_mm(value_increments: int) -> float:
    return round(value_increments * STARBackend.z_drive_mm_per_increment, 2)

  # Dispensing drive conversions
  # --- uL <-> increments ---
  @staticmethod
  def dispensing_drive_vol_to_increment(volume: float) -> int:
    return round(volume / STARBackend.dispensing_drive_vol_per_increment)

  @staticmethod
  def dispensing_drive_increment_to_volume(position_increment: int) -> float:
    return round(position_increment * STARBackend.dispensing_drive_vol_per_increment, 1)

  # --- mm <-> increments ---
  @staticmethod
  def dispensing_drive_mm_to_increment(position_mm: float) -> int:
    return round(position_mm / STARBackend.dispensing_drive_mm_per_increment)

  @staticmethod
  def dispensing_drive_increment_to_mm(position_increment: int) -> float:
    return round(position_increment * STARBackend.dispensing_drive_mm_per_increment, 3)

  # --- uL <-> mm ---
  @staticmethod
  def dispensing_drive_vol_to_mm(vol: float) -> float:
    inc = STARBackend.dispensing_drive_vol_to_increment(vol)
    return STARBackend.dispensing_drive_increment_to_mm(inc)

  @staticmethod
  def dispensing_drive_mm_to_vol(position_mm: float) -> float:
    inc = STARBackend.dispensing_drive_mm_to_increment(position_mm)
    return STARBackend.dispensing_drive_increment_to_volume(inc)

  async def clld_probe_x_position_using_channel(
    self,
    channel_idx: int,
    probing_direction: Literal["right", "left"],
    end_pos_search: Optional[float] = None,
    post_detection_dist: float = 2.0,
    tip_bottom_diameter: float = 1.2,
    read_timeout: float = 240.0,
  ) -> float:
    """Deprecated: use ``star.driver.left_x_arm.clld_probe_x_position()``."""
    if self.driver.left_x_arm is None:
      raise RuntimeError("left_x_arm not configured")
    return await self.driver.left_x_arm.clld_probe_x_position(
      channel_idx=channel_idx,
      probing_direction=probing_direction,
      end_pos_search=end_pos_search,
      post_detection_dist=post_detection_dist,
      tip_bottom_diameter=tip_bottom_diameter,
      read_timeout=read_timeout,
    )

  async def clld_probe_y_position_using_channel(
    self,
    channel_idx: int,
    probing_direction: Literal["forward", "backward"],
    start_pos_search: Optional[float] = None,
    end_pos_search: Optional[float] = None,
    channel_speed: float = 10.0,
    channel_acceleration_int: Literal[1, 2, 3, 4] = 4,
    detection_edge: int = 10,
    current_limit_int: Literal[1, 2, 3, 4, 5, 6, 7] = 7,
    post_detection_dist: float = 2.0,
    tip_bottom_diameter: float = 1.2,
  ) -> float:
    """Deprecated: use ``star.pip.backend.channels[n].clld_probe_y_position()``."""
    return await self.driver.pip.channels[channel_idx].clld_probe_y_position(
      probing_direction=probing_direction,
      start_pos_search=start_pos_search,
      end_pos_search=end_pos_search,
      channel_speed=channel_speed,
      channel_acceleration_int=channel_acceleration_int,
      detection_edge=detection_edge,
      current_limit_int=current_limit_int,
      post_detection_dist=post_detection_dist,
      tip_bottom_diameter=tip_bottom_diameter,
    )

  async def _move_z_drive_to_liquid_surface_using_clld(
    self,
    channel_idx: int,  # 0-based indexing of channels!
    lowest_immers_pos: float = 99.98,  # mm
    start_pos_search: float = 334.7,  # mm
    channel_speed: float = 10.0,  # mm
    channel_acceleration: float = 800.0,  # mm/sec**2
    detection_edge: int = 10,
    detection_drop: int = 2,
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_dist: float = 2.0,  # mm
  ):
    """Deprecated: use ``star.pip.backend.channels[n].search_z_using_clld()``."""
    return await self._pip_channels[channel_idx].search_z_using_clld(
      lowest_immers_pos=lowest_immers_pos,
      start_pos_search=start_pos_search,
      channel_speed=channel_speed,
      channel_acceleration=channel_acceleration,
      detection_edge=detection_edge,
      detection_drop=detection_drop,
      post_detection_trajectory=post_detection_trajectory,
      post_detection_dist=post_detection_dist,
    )

  async def clld_probe_z_height_using_channel(
    self,
    channel_idx: int,
    lowest_immers_pos: float = 99.98,
    start_pos_search: Optional[float] = None,
    channel_speed: float = 10.0,
    channel_acceleration: float = 800.0,
    detection_edge: int = 10,
    detection_drop: int = 2,
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_dist: float = 2.0,
    move_channels_to_safe_pos_after: bool = False,
  ) -> float:
    """Deprecated: use ``star.pip.backend.channels[n].clld_probe_z_height()``."""
    return await self.driver.pip.channels[channel_idx].clld_probe_z_height(
      lowest_immers_pos=lowest_immers_pos,
      start_pos_search=start_pos_search,
      channel_speed=channel_speed,
      channel_acceleration=channel_acceleration,
      detection_edge=detection_edge,
      detection_drop=detection_drop,
      post_detection_trajectory=post_detection_trajectory,
      post_detection_dist=post_detection_dist,
      move_channels_to_safe_pos_after=move_channels_to_safe_pos_after,
    )

  async def _search_for_surface_using_plld(
    self,
    channel_idx: int,  # 0-based indexing of channels!
    lowest_immers_pos: float = 99.98,  # mm of the head_probe!
    start_pos_search: float = 334.7,  # mm of the head_probe!
    channel_speed_above_start_pos_search: float = 120.0,  # mm/sec
    channel_speed: float = 10.0,  # mm
    channel_acceleration: float = 800.0,  # mm/sec**2
    z_drive_current_limit: int = 3,
    tip_has_filter: bool = False,
    dispense_drive_speed: float = 5.0,  # mm/sec
    dispense_drive_acceleration: float = 0.2,  # mm/sec**2
    dispense_drive_max_speed: float = 14.5,  # mm/sec
    dispense_drive_current_limit: int = 3,
    plld_detection_edge: int = 30,
    plld_detection_drop: int = 10,
    clld_verification: bool = False,  # cLLD Verification feature
    clld_detection_edge: int = 10,  # cLLD Verification feature
    clld_detection_drop: int = 2,  # cLLD Verification feature
    max_delta_plld_clld: float = 5.0,  # cLLD Verification feature; mm
    plld_mode: Optional[PressureLLDMode] = None,  # Foam feature
    plld_foam_detection_drop: int = 30,  # Foam feature
    plld_foam_detection_edge_tolerance: int = 30,  # Foam feature
    plld_foam_ad_values: int = 30,  # Foam feature; unknown unit
    plld_foam_search_speed: float = 10.0,  # Foam feature; mm/sec
    dispense_back_plld_volume: Optional[float] = None,  # uL
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_dist: float = 2.0,  # mm
  ) -> Tuple[float, float]:
    """Deprecated: use ``star.pip.backend.channels[n].search_z_using_plld()``."""
    new_plld_mode: Optional[_NewPressureLLDMode] = None
    if plld_mode is not None:
      new_plld_mode = _NewPressureLLDMode(plld_mode.value)
    return await self._pip_channels[channel_idx].search_z_using_plld(
      lowest_immers_pos=lowest_immers_pos,
      start_pos_search=start_pos_search,
      channel_speed_above_start_pos_search=channel_speed_above_start_pos_search,
      channel_speed=channel_speed,
      channel_acceleration=channel_acceleration,
      z_drive_current_limit=z_drive_current_limit,
      tip_has_filter=tip_has_filter,
      dispense_drive_speed=dispense_drive_speed,
      dispense_drive_acceleration=dispense_drive_acceleration,
      dispense_drive_max_speed=dispense_drive_max_speed,
      dispense_drive_current_limit=dispense_drive_current_limit,
      plld_detection_edge=plld_detection_edge,
      plld_detection_drop=plld_detection_drop,
      clld_verification=clld_verification,
      clld_detection_edge=clld_detection_edge,
      clld_detection_drop=clld_detection_drop,
      max_delta_plld_clld=max_delta_plld_clld,
      plld_mode=new_plld_mode,
      plld_foam_detection_drop=plld_foam_detection_drop,
      plld_foam_detection_edge_tolerance=plld_foam_detection_edge_tolerance,
      plld_foam_ad_values=plld_foam_ad_values,
      plld_foam_search_speed=plld_foam_search_speed,
      dispense_back_plld_volume=dispense_back_plld_volume,
      post_detection_trajectory=post_detection_trajectory,
      post_detection_dist=post_detection_dist,
    )

  async def plld_probe_z_height_using_channel(
    self,
    channel_idx: int,
    lowest_immers_pos: float = 99.98,
    start_pos_search: Optional[float] = None,
    channel_speed_above_start_pos_search: float = 120.0,
    channel_speed: float = 10.0,
    channel_acceleration: float = 800.0,
    z_drive_current_limit: int = 3,
    tip_has_filter: bool = False,
    dispense_drive_speed: float = 5.0,
    dispense_drive_acceleration: float = 0.2,
    dispense_drive_max_speed: float = 14.5,
    dispense_drive_current_limit: int = 3,
    plld_detection_edge: int = 30,
    plld_detection_drop: int = 10,
    clld_verification: bool = False,
    clld_detection_edge: int = 10,
    clld_detection_drop: int = 2,
    max_delta_plld_clld: float = 5.0,
    plld_mode: Optional[PressureLLDMode] = None,
    plld_foam_detection_drop: int = 30,
    plld_foam_detection_edge_tolerance: int = 30,
    plld_foam_ad_values: int = 30,
    plld_foam_search_speed: float = 10.0,
    dispense_back_plld_volume: Optional[float] = None,
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_dist: float = 2.0,
    move_channels_to_safe_pos_after: bool = False,
  ) -> Tuple[float, float]:
    """Deprecated: use ``star.pip.backend.channels[n].plld_probe_z_height()``."""
    new_plld_mode: Optional[_NewPressureLLDMode] = None
    if plld_mode is not None:
      new_plld_mode = _NewPressureLLDMode(plld_mode.value)
    return await self.driver.pip.channels[channel_idx].plld_probe_z_height(
      lowest_immers_pos=lowest_immers_pos,
      start_pos_search=start_pos_search,
      channel_speed_above_start_pos_search=channel_speed_above_start_pos_search,
      channel_speed=channel_speed,
      channel_acceleration=channel_acceleration,
      z_drive_current_limit=z_drive_current_limit,
      tip_has_filter=tip_has_filter,
      dispense_drive_speed=dispense_drive_speed,
      dispense_drive_acceleration=dispense_drive_acceleration,
      dispense_drive_max_speed=dispense_drive_max_speed,
      dispense_drive_current_limit=dispense_drive_current_limit,
      plld_detection_edge=plld_detection_edge,
      plld_detection_drop=plld_detection_drop,
      clld_verification=clld_verification,
      clld_detection_edge=clld_detection_edge,
      clld_detection_drop=clld_detection_drop,
      max_delta_plld_clld=max_delta_plld_clld,
      plld_mode=new_plld_mode,
      plld_foam_detection_drop=plld_foam_detection_drop,
      plld_foam_detection_edge_tolerance=plld_foam_detection_edge_tolerance,
      plld_foam_ad_values=plld_foam_ad_values,
      plld_foam_search_speed=plld_foam_search_speed,
      dispense_back_plld_volume=dispense_back_plld_volume,
      post_detection_trajectory=post_detection_trajectory,
      post_detection_dist=post_detection_dist,
      move_channels_to_safe_pos_after=move_channels_to_safe_pos_after,
    )

  async def request_probe_z_position(self, channel_idx: int) -> float:
    """Deprecated: use ``star.pip.backend.channels[n].request_probe_z_position()``."""
    return await self._pip_channels[channel_idx].request_probe_z_position()

  async def request_tip_len_on_channel(self, channel_idx: int) -> float:
    """Deprecated: use ``star.pip.backend.channels[n].request_tip_length()``."""
    return await self._pip_channels[channel_idx].request_tip_length()

  MAXIMUM_CHANNEL_Z_POSITION = 334.7  # mm (= z-drive increment 31_200)
  MINIMUM_CHANNEL_Z_POSITION = 99.98  # mm (= z-drive increment 9_320)
  DEFAULT_TIP_FITTING_DEPTH = 8  # mm, for 10, 50, 300, 1000 ul Hamilton tips

  async def ztouch_probe_z_height_using_channel(
    self,
    channel_idx: int,
    tip_len: Optional[float] = None,
    lowest_immers_pos: float = 99.98,
    start_pos_search: Optional[float] = None,
    channel_speed: float = 10.0,
    channel_acceleration: float = 800.0,
    channel_speed_upwards: float = 125.0,
    detection_limiter_in_PWM: int = 1,
    push_down_force_in_PWM: int = 0,
    post_detection_dist: float = 2.0,
    move_channels_to_safe_pos_after: bool = False,
  ) -> float:
    """Deprecated: use ``star.pip.backend.channels[n].ztouch_probe_z_height()``."""
    return await self.driver.pip.channels[channel_idx].ztouch_probe_z_height(
      tip_len=tip_len,
      lowest_immers_pos=lowest_immers_pos,
      start_pos_search=start_pos_search,
      channel_speed=channel_speed,
      channel_acceleration=channel_acceleration,
      channel_speed_upwards=channel_speed_upwards,
      detection_limiter_in_PWM=detection_limiter_in_PWM,
      push_down_force_in_PWM=push_down_force_in_PWM,
      post_detection_dist=post_detection_dist,
      move_channels_to_safe_pos_after=move_channels_to_safe_pos_after,
    )

  class RotationDriveOrientation(enum.Enum):
    LEFT = 1
    FRONT = 2
    RIGHT = 3
    PARKED_RIGHT = None

  async def rotate_iswap_rotation_drive(self, orientation: RotationDriveOrientation):
    """Deprecated: use ``star.iswap.rotate_rotation_drive()``."""
    return await self._iswap.rotate_rotation_drive(orientation)  # type: ignore[arg-type]

  class WristDriveOrientation(enum.Enum):
    RIGHT = 1
    STRAIGHT = 2
    LEFT = 3
    REVERSE = 4

  async def rotate_iswap_wrist(self, orientation: WristDriveOrientation):
    """Deprecated: use ``star.iswap.rotate_wrist()``."""
    return await self._iswap.rotate_wrist(orientation)  # type: ignore[arg-type]

  @staticmethod
  def channel_id(channel_idx: int) -> str:
    """channel_idx: plr style, 0-indexed from the back"""
    channel_ids = "123456789ABCDEFG"
    return "P" + channel_ids[channel_idx]

  async def get_channels_y_positions(self) -> Dict[int, float]:
    """Deprecated: use ``star.pip.backend.get_channels_y_positions()``."""
    resp = await self.send_command(
      module="C0",
      command="RY",
      fmt="ry#### (n)",
    )
    y_positions = [round(y / 10, 2) for y in resp["ry"]]

    # sometimes there is (likely) a floating point error and channels are reported to be
    # less than their minimum spacing apart (typically 9 mm). (When you set channels using
    # position_channels_in_y_direction, it will raise an error.) The minimum y is 6mm,
    # so we fix that first (in case that value is misreported). Then, we traverse the
    # list in reverse and enforce pairwise minimum spacing.
    min_y = self.extended_conf.left_arm_min_y_position
    if y_positions[-1] < min_y - 0.2:
      raise RuntimeError(
        "Channels are reported to be too close to the front of the machine. "
        f"The known minimum is {min_y}, which will be fixed automatically for "
        f"{min_y - 0.2}<y<{min_y}. "
        f"Reported values: {y_positions}."
      )
    elif min_y - 0.2 <= y_positions[-1] < min_y:
      y_positions[-1] = min_y

    for i in range(len(y_positions) - 2, -1, -1):
      spacing = self._min_spacing_between(i, i + 1)
      if y_positions[i] - y_positions[i + 1] < spacing:
        y_positions[i] = y_positions[i + 1] + spacing

    return {channel_idx: y for channel_idx, y in enumerate(y_positions)}

  @need_iswap_parked
  async def position_channels_in_y_direction(self, ys: Dict[int, float], make_space=True):
    """Deprecated: use ``star.pip.backend.position_channels_in_y_direction()``."""

    # check that the locations of channels after the move will respect pairwise minimum
    # spacing and be in descending order
    channel_locations = await self.get_channels_y_positions()

    for channel_idx, y in ys.items():
      channel_locations[channel_idx] = y

    if make_space:
      use_channels = list(ys.keys())
      back_channel = min(use_channels)
      front_channel = max(use_channels)

      # Position channels in between used channels
      for intermediate_ch in range(back_channel + 1, front_channel):
        if intermediate_ch not in ys:
          channel_locations[intermediate_ch] = channel_locations[
            intermediate_ch - 1
          ] - self._min_spacing_between(intermediate_ch - 1, intermediate_ch)

      # For the channels to the back of `back_channel`, make sure the space between them is
      # >=9mm. We start with the channel closest to `back_channel`, and make sure the
      # channel behind it is at least 9mm, updating if needed. Iterating from the front (closest
      # to `back_channel`) to the back (channel 0), all channels are put at the correct location.
      # This order matters because the channel in front of any channel may have been moved in the
      # previous iteration.
      # Note that if a channel is already spaced at >=9mm, it is not moved.
      for channel_idx in range(back_channel, 0, -1):
        spacing = self._min_spacing_between(channel_idx - 1, channel_idx)
        if (channel_locations[channel_idx - 1] - channel_locations[channel_idx]) < spacing:
          channel_locations[channel_idx - 1] = channel_locations[channel_idx] + spacing

      # Similarly for the channels to the front of `front_channel`, make sure they are all
      # spaced >= channel_minimum_y_spacing (usually 9mm) apart. This time, we iterate from
      # back (closest to `front_channel`) to the front (lh.backend.num_channels - 1), and
      # put each channel >= channel_minimum_y_spacing before the one behind it.
      for channel_idx in range(front_channel, self.num_channels - 1):
        spacing = self._min_spacing_between(channel_idx, channel_idx + 1)
        if (channel_locations[channel_idx] - channel_locations[channel_idx + 1]) < spacing:
          channel_locations[channel_idx + 1] = channel_locations[channel_idx] - spacing

    # Quick checks before movement.
    if channel_locations[0] > 650:
      raise ValueError("Channel 0 would hit the back of the robot")

    if channel_locations[self.num_channels - 1] < 6:
      raise ValueError("Channel N would hit the front of the robot")

    for i in range(len(channel_locations) - 1):
      required = self._min_spacing_between(i, i + 1)
      actual = channel_locations[i] - channel_locations[i + 1]
      if round(actual * 1000) < round(required * 1000):  # compare in um to avoid float issues
        raise ValueError(
          f"Channels {i} and {i + 1} must be at least {required}mm apart, "
          f"but are {actual:.2f}mm apart."
        )

    yp = " ".join([f"{round(y * 10):04}" for y in channel_locations.values()])
    return await self.send_command(
      module="C0",
      command="JY",
      yp=yp,
    )

  async def get_channels_z_positions(self) -> Dict[int, float]:
    """Deprecated: use ``star.pip.backend.get_channels_z_positions()``."""
    resp = await self.send_command(
      module="C0",
      command="RZ",
      fmt="rz#### (n)",
    )
    return {channel_idx: round(y / 10, 2) for channel_idx, y in enumerate(resp["rz"])}

  async def position_channels_in_z_direction(self, zs: Dict[int, float]):
    """Deprecated: use ``star.pip.backend.position_channels_in_z_direction()``."""
    channel_locations = await self.get_channels_z_positions()

    for channel_idx, z in zs.items():
      channel_locations[channel_idx] = z

    return await self.send_command(
      module="C0", command="JZ", zp=[f"{round(z * 10):04}" for z in channel_locations.values()]
    )

  async def pierce_foil(
    self,
    wells: Union[Well, List[Well]],
    piercing_channels: List[int],
    hold_down_channels: List[int],
    move_inwards: float,
    spread: Literal["wide", "tight"] = "wide",
    one_by_one: bool = False,
    distance_from_bottom: float = 20.0,
  ):
    """Deprecated: use ``star.pip.backend.pierce_foil()``."""
    await self._pip.pierce_foil(
      wells=wells,
      piercing_channels=piercing_channels,
      hold_down_channels=hold_down_channels,
      move_inwards=move_inwards,
      deck=self.deck,
      spread=spread,
      one_by_one=one_by_one,
      distance_from_bottom=distance_from_bottom,
    )

  async def step_off_foil(
    self,
    wells: Union[Well, List[Well]],
    front_channel: int,
    back_channel: int,
    move_inwards: float = 2,
    move_height: float = 15,
  ):
    """Deprecated: use ``star.pip.backend.step_off_foil()``."""
    await self._pip.step_off_foil(
      wells=wells,
      front_channel=front_channel,
      back_channel=back_channel,
      deck=self.deck,
      move_inwards=move_inwards,
      move_height=move_height,
    )

  async def request_volume_in_tip(self, channel: int) -> float:
    """Deprecated: use ``star.pip.backend.channels[n].request_volume_in_tip()``."""
    return await self._pip_channels[channel].request_volume_in_tip()

  @asynccontextmanager
  async def slow_iswap(self, wrist_velocity: int = 20_000, gripper_velocity: int = 20_000):
    """Deprecated: use ``star.iswap.slow()``."""
    async with self._iswap.slow(wrist_velocity=wrist_velocity, gripper_velocity=gripper_velocity):
      yield

  # ------------ STAR(RS-232/TCC1/2)-connected Hamilton Heater Cooler (HHS) -------------

  async def check_type_is_hhc(self, device_number: int):
    """
    Convenience method to check that connected device is an HHC.
    Executed through firmware query
    """

    firmware_version = await self.send_command(module=f"T{device_number}", command="RF")
    if "Hamilton Heater Cooler" not in firmware_version:
      raise ValueError(
        f"Device number {device_number} does not connect to a Hamilton"
        f" Heater-Cooler, found {firmware_version} instead."
        f"Have you called the wrong device number?"
      )

  async def initialize_hhc(self, device_number: int) -> str:
    """Initialize Hamilton Heater Cooler (HHC) at specified TCC port

    Args:
      device_number: TCC connect number to the HHC
    """

    module_pointer = f"T{device_number}"

    # Request module configuration
    try:
      await self.send_command(module=module_pointer, command="QU")
    except TimeoutError as exc:
      error_message = (
        f"No Hamilton Heater Cooler found at device_number {device_number}"
        f", have you checked your connections? Original error: {exc}"
      )
      raise ValueError(error_message) from exc

    await self.check_type_is_hhc(device_number)

    # Request module configuration
    hhc_init_status = await self.send_command(module=module_pointer, command="QW", fmt="qw#")
    hhc_init_status = hhc_init_status["qw"]

    info = "HHC already initialized"
    # Initializing HHS if necessary
    if hhc_init_status != 1:
      # Initialize device
      await self.send_command(module=module_pointer, command="LI")
      info = f"HHS at device number {device_number} initialized."

    return info

  async def start_temperature_control_at_hhc(
    self,
    device_number: int,
    temp: Union[float, int],
  ):
    """Start temperature regulation of specified HHC"""

    await self.check_type_is_hhc(device_number)
    assert 0 < temp <= 105

    # Ensure proper temperature input handling
    if isinstance(temp, (float, int)):
      safe_temp_str = f"{round(temp * 10):04d}"
    else:
      safe_temp_str = str(temp)

    return await self.send_command(
      module=f"T{device_number}",
      command="TA",  # temperature adjustment
      ta=safe_temp_str,
      tb="1800",  # TODO: identify precise purpose?
      tc="0020",  # TODO: identify precise purpose?
    )

  async def get_temperature_at_hhc(self, device_number: int) -> dict:
    """Query current temperatures of both sensors of specified HHC"""

    await self.check_type_is_hhc(device_number)

    request_temperature = await self.send_command(module=f"T{device_number}", command="RT")
    processed_t_info = [int(x) / 10 for x in request_temperature.split("+")[-2:]]

    return {
      "middle_T": processed_t_info[0],
      "edge_T": processed_t_info[-1],
    }

  async def query_whether_temperature_reached_at_hhc(self, device_number: int):
    """Stop temperature regulation of specified HHC"""

    await self.check_type_is_hhc(device_number)
    query_current_control_status = await self.send_command(
      module=f"T{device_number}", command="QD", fmt="qd#"
    )

    return query_current_control_status["qd"] == 0

  async def stop_temperature_control_at_hhc(self, device_number: int):
    """Stop temperature regulation of specified HHC"""

    await self.check_type_is_hhc(device_number)

    return await self.send_command(module=f"T{device_number}", command="TO")

  # -------------- Extra - Probing labware with STAR - making STAR into a CMM --------------


class UnSafe:
  """
  Namespace for actions that are unsafe to perform.
  For example, actions that send the iSWAP outside of the Hamilton Deck
  """

  def __init__(self, star: "STARBackend"):
    self.star = star

  async def put_in_hotel(
    self,
    hotel_center_x_coord: int = 0,
    hotel_center_y_coord: int = 0,
    hotel_center_z_coord: int = 0,
    hotel_center_x_direction: Literal[0, 1] = 0,
    hotel_center_y_direction: Literal[0, 1] = 0,
    hotel_center_z_direction: Literal[0, 1] = 0,
    clearance_height: int = 50,
    hotel_depth: int = 1_300,
    grip_direction: GripDirection = GripDirection.FRONT,
    traverse_height_at_beginning: int = 3_600,
    z_position_at_end: int = 3_600,
    grip_strength: Literal[0, 1, 2, 3, 4, 5, 6, 7, 8, 9] = 5,
    open_gripper_position: int = 860,
    collision_control: Literal[0, 1] = 1,
    high_acceleration_index: Literal[1, 2, 3, 4] = 4,
    low_acceleration_index: Literal[1, 2, 3, 4] = 1,
    fold_up_at_end: bool = True,
  ):
    """
    A hotel is a location to store a plate. This can be a loading
    dock for an external machine such as a cytomat or a centrifuge.

    Take care when using this command to interact with hotels located
    outside of the hamilton deck area. Ensure that rotations of the
    iSWAP arm don't collide with anything.

    tip: set the hotel depth big enough so that the boundary is inside the
    hamilton deck. The iSWAP rotations will happen before it enters the hotel.

    The units of all relevant variables are in 0.1mm
    """

    assert 0 <= hotel_center_x_coord <= 99_999
    assert 0 <= hotel_center_y_coord <= 6_500
    assert 0 <= hotel_center_z_coord <= 3_500
    assert 0 <= clearance_height <= 999
    assert 0 <= hotel_depth <= 3_000
    assert 0 <= traverse_height_at_beginning <= 3_600
    assert 0 <= z_position_at_end <= 3_600
    assert 0 <= open_gripper_position <= 9_999

    return await self.star.send_command(
      module="C0",
      command="PI",
      xs=f"{hotel_center_x_coord:05}",
      xd=hotel_center_x_direction,
      yj=f"{hotel_center_y_coord:04}",
      yd=hotel_center_y_direction,
      zj=f"{hotel_center_z_coord:04}",
      zd=hotel_center_z_direction,
      zc=f"{clearance_height:03}",
      hd=f"{hotel_depth:04}",
      gr={
        GripDirection.FRONT: 1,
        GripDirection.RIGHT: 2,
        GripDirection.BACK: 3,
        GripDirection.LEFT: 4,
      }[grip_direction],
      th=f"{traverse_height_at_beginning:04}",
      te=f"{z_position_at_end:04}",
      gw=grip_strength,
      go=f"{open_gripper_position:04}",
      ga=collision_control,
      xe=f"{high_acceleration_index} {low_acceleration_index}",
      gc=int(fold_up_at_end),
    )

  async def get_from_hotel(
    self,
    hotel_center_x_coord: int = 0,
    hotel_center_y_coord: int = 0,
    hotel_center_z_coord: int = 0,
    # for direction, 0 is positive, 1 is negative
    hotel_center_x_direction: Literal[0, 1] = 0,
    hotel_center_y_direction: Literal[0, 1] = 0,
    hotel_center_z_direction: Literal[0, 1] = 0,
    clearance_height: int = 50,
    hotel_depth: int = 1_300,
    grip_direction: GripDirection = GripDirection.FRONT,
    traverse_height_at_beginning: int = 3_600,
    z_position_at_end: int = 3_600,
    grip_strength: Literal[0, 1, 2, 3, 4, 5, 6, 7, 8, 9] = 5,
    open_gripper_position: int = 860,
    plate_width: int = 800,
    plate_width_tolerance: int = 20,
    collision_control: Literal[0, 1] = 1,
    high_acceleration_index: Literal[1, 2, 3, 4] = 4,
    low_acceleration_index: Literal[1, 2, 3, 4] = 1,
    fold_up_at_end: bool = True,
  ):
    """
    A hotel is a location to store a plate. This can be a loading
    dock for an external machine such as a cytomat or a centrifuge.

    Take care when using this command to interact with hotels located
    outside of the hamilton deck area. Ensure that rotations of the
    iSWAP arm don't collide with anything.

    tip: set the hotel depth big enough so that the boundary is inside the
    hamilton deck. The iSWAP rotations will happen before it enters the hotel.

    The units of all relevant variables are in 0.1mm
    """

    assert 0 <= hotel_center_x_coord <= 99_999
    assert 0 <= hotel_center_y_coord <= 6_500
    assert 0 <= hotel_center_z_coord <= 3_500
    assert 0 <= clearance_height <= 999
    assert 0 <= hotel_depth <= 3_000
    assert 0 <= traverse_height_at_beginning <= 3_600
    assert 0 <= z_position_at_end <= 3_600
    assert 0 <= open_gripper_position <= 9_999
    assert 0 <= plate_width <= 9_999
    assert 0 <= plate_width_tolerance <= 99

    return await self.star.send_command(
      module="C0",
      command="PO",
      xs=f"{hotel_center_x_coord:05}",
      xd=hotel_center_x_direction,
      yj=f"{hotel_center_y_coord:04}",
      yd=hotel_center_y_direction,
      zj=f"{hotel_center_z_coord:04}",
      zd=hotel_center_z_direction,
      zc=f"{clearance_height:03}",
      hd=f"{hotel_depth:04}",
      gr={
        GripDirection.FRONT: 1,
        GripDirection.RIGHT: 2,
        GripDirection.BACK: 3,
        GripDirection.LEFT: 4,
      }[grip_direction],
      th=f"{traverse_height_at_beginning:04}",
      te=f"{z_position_at_end:04}",
      gw=grip_strength,
      go=f"{open_gripper_position:04}",
      gb=f"{plate_width:04}",
      gt=f"{plate_width_tolerance:02}",
      ga=collision_control,
      xe=f"{high_acceleration_index} {low_acceleration_index}",
      gc=int(fold_up_at_end),
    )

  async def violently_shoot_down_tip(self, channel_idx: int):
    """Deprecated: use ``star.pip.backend.channels[n].violently_shoot_down_tip()``."""
    return await self.star._pip_channels[channel_idx].violently_shoot_down_tip()


# Deprecated alias with warning # TODO: remove mid May 2025 (giving people 1 month to update)
# https://github.com/PyLabRobot/pylabrobot/issues/466


class STAR(STARBackend):
  def __init__(self, *args, **kwargs):
    warnings.warn(
      "`STAR` is deprecated and will be removed in a future release. "
      "Please use `STARBackend` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    super().__init__(*args, **kwargs)
