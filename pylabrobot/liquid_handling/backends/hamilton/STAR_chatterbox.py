import copy
import datetime
import logging
import warnings
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Dict, List, Literal, Optional, Tuple, Union

from pylabrobot.io.validation_utils import LOG_LEVEL_IO
from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.hamilton.STAR_backend import (
  DriveConfiguration,
  ExtendedConfiguration,
  Head96Information,
  MachineConfiguration,
  STARBackend,
  iSWAPInformation,
)
from pylabrobot.resources.container import Container
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonDeck
from pylabrobot.resources.tip_tracker import does_tip_tracking
from pylabrobot.resources.well import Well

logger = logging.getLogger("pylabrobot")

_DEFAULT_MACHINE_CONFIGURATION = MachineConfiguration(
  pip_type_1000ul=True,
  kb_iswap_installed=True,
  auto_load_installed=True,
  num_pip_channels=8,
)

_DEFAULT_EXTENDED_CONFIGURATION = ExtendedConfiguration(
  left_x_drive_large=True,
  iswap_gripper_wide=True,
  instrument_size_slots=30,
  auto_load_size_slots=30,
  tip_waste_x_position=800.0,
  left_x_drive=DriveConfiguration(iswap_installed=True, core_96_head_installed=True),
  min_iswap_collision_free_position=350.0,
  max_iswap_collision_free_position=600.0,
)

# Minimal left-drive X position of a dual-rail arm. Validated against real hardware;
# the single-rail minimum is not yet known (#822). Only the chatterbox needs this
# literal - a physical STAR reports its own value from the drive-range query.
_DUAL_RAIL_LEFT_X_MIN = 95.0

# Hamilton factory defaults. Per-machine EEPROM calibration will differ
# slightly (e.g., L1=137.8, L2=137.7, STRAIGHT=-45.01 on one tested machine);
# these defaults are accurate enough for simulation but not for
# calibration-sensitive applications.
_DEFAULT_ISWAP_INFORMATION = iSWAPInformation(
  fw_version="simulated",
  rotation_drive_x_offset=34.0,
  rotation_drive_y_max=627.4,
  link_1_length=138.0,
  link_2_length=138.0,
  rotation_drive_predefined_increments={
    STARBackend.RotationDriveOrientation.LEFT: -29068,  # ~-90 deg
    STARBackend.RotationDriveOrientation.FRONT: 0,  # ~+0 deg
    STARBackend.RotationDriveOrientation.RIGHT: 29068,  # ~+90 deg
    STARBackend.RotationDriveOrientation.PARKED_RIGHT: 29500,  # ~+91 deg
  },
  wrist_drive_predefined_increments={
    STARBackend.WristDriveOrientation.RIGHT: -26577,  # ~-135 deg
    STARBackend.WristDriveOrientation.STRAIGHT: -8859,  # ~-45 deg
    STARBackend.WristDriveOrientation.LEFT: 8859,  # ~+45 deg
    STARBackend.WristDriveOrientation.REVERSE: 26577,  # ~+135 deg
  },
)


class STARChatterboxBackend(STARBackend):
  """Chatterbox backend for 'STAR'"""

  def __init__(
    self,
    num_channels: int = 8,
    machine_configuration: MachineConfiguration = _DEFAULT_MACHINE_CONFIGURATION,
    extended_configuration: ExtendedConfiguration = _DEFAULT_EXTENDED_CONFIGURATION,
    iswap_information: Optional[iSWAPInformation] = None,
    channels_minimum_y_spacing: Optional[List[float]] = None,
    # deprecated parameters
    core96_head_installed: Optional[bool] = None,
    iswap_installed: Optional[bool] = None,
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
      core96_head_installed: Deprecated. Set `extended_configuration.left_x_drive
        .core_96_head_installed` instead.
      iswap_installed: Deprecated. Set `extended_configuration.left_x_drive
        .iswap_installed` instead.
    """
    super().__init__()
    self._num_channels = num_channels
    self._iswap_parked = True
    self._sim_iswap_information = iswap_information  # None means use default at setup

    if core96_head_installed is not None or iswap_installed is not None:
      extended_configuration = copy.deepcopy(extended_configuration)
      xl = copy.deepcopy(extended_configuration.left_x_drive)
      if core96_head_installed is not None:
        warnings.warn(
          "core96_head_installed is deprecated. Pass an ExtendedConfiguration with "
          "left_x_drive.core_96_head_installed set instead.",
          DeprecationWarning,
          stacklevel=2,
        )
        xl.core_96_head_installed = core96_head_installed
      if iswap_installed is not None:
        warnings.warn(
          "iswap_installed is deprecated. Pass an ExtendedConfiguration with "
          "left_x_drive.iswap_installed set instead.",
          DeprecationWarning,
          stacklevel=2,
        )
        xl.iswap_installed = iswap_installed
      extended_configuration.left_x_drive = xl

    self._machine_configuration = machine_configuration
    self._extended_conf = extended_configuration

    if channels_minimum_y_spacing is not None:
      if len(channels_minimum_y_spacing) != num_channels:
        raise ValueError(
          f"channels_minimum_y_spacing has {len(channels_minimum_y_spacing)} entries, "
          f"expected {num_channels}."
        )
      self._channels_minimum_y_spacing = list(channels_minimum_y_spacing)
    else:
      self._channels_minimum_y_spacing = [
        extended_configuration.min_raster_pitch_pip_channels
      ] * num_channels

  async def setup(
    self,
    skip_instrument_initialization=False,
    skip_pip=False,
    skip_autoload=False,
    skip_iswap=False,
    skip_core96_head=False,
  ):
    """Initialize the chatterbox backend and detect installed modules.

    Args:
      skip_instrument_initialization: If True, skip instrument initialization.
      skip_pip: If True, skip pipetting channel initialization.
      skip_autoload: If True, skip initializing the autoload module, if applicable.
      skip_iswap: If True, skip initializing the iSWAP module, if applicable.
      skip_core96_head: If True, skip initializing the CoRe 96 head module, if applicable.
    """
    await LiquidHandlerBackend.setup(self)

    self.id_ = 0

    # Request machine information
    self._machine_conf = await self.request_machine_configuration()
    self._extended_conf = await self.request_extended_configuration()

    # Mock firmware information for 96-head if installed
    if self.extended_conf.left_x_drive.core_96_head_installed and not skip_core96_head:
      fw_version = datetime.date(2023, 1, 1)
      instrument_type: Head96Information.InstrumentType = "FM-STAR"
      self._head96_information = Head96Information(
        fw_version=fw_version,
        x_offset=365.0,  # factory default; hardware reads the per-machine value from EEPROM (kf)
        supports_clot_monitoring_clld=False,
        stop_disc_type="core_ii",
        instrument_type=instrument_type,
        head_type="96 head II",
        z_range=self._head96_resolve_z_range(instrument_type),
      )
      # Seed the mutable drive defaults from the machine (mirrors STARBackend); the head96_request_*
      # overrides below return the canned 2013+ factory registers.
      self._head96_y_drive_speed_default = await self.head96_request_y_speed()
      self._head96_y_drive_acceleration_default = await self.head96_request_y_acceleration()
      self._head96_z_drive_speed_default = await self.head96_request_z_speed()
      self._head96_z_drive_acceleration_default = await self.head96_request_z_acceleration()
    else:
      self._head96_information = None

    # Mock iSWAP setup state if installed. One assignment - constructor override
    # (if given) takes precedence over the factory-default record.
    if self.extended_conf.left_x_drive.iswap_installed and not skip_iswap:
      self._iswap_information = self._sim_iswap_information or _DEFAULT_ISWAP_INFORMATION
    else:
      self._iswap_information = None

  async def stop(self):
    await LiquidHandlerBackend.stop(self)
    self._setup_done = False

  # # # # # # # # Low-level command sending/receiving # # # # # # # #

  async def _write_and_read_command(
    self,
    id_: Optional[int],
    cmd: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    logger.log(LOG_LEVEL_IO, "%s", cmd)
    return None

  async def send_raw_command(
    self,
    command: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    logger.log(LOG_LEVEL_IO, "%s", command)
    return None

  # # # # # # # # STAR configuration # # # # # # # #

  async def request_machine_configuration(self) -> MachineConfiguration:
    return self._machine_configuration

  async def request_extended_configuration(self) -> ExtendedConfiguration:
    """Return the configured extended configuration with X-arm geometry resolved.

    Mirrors STARBackend.request_extended_configuration: fills each installed drive's
    geometry from the mocked X-drive range/envelope replies. A right drive that was not
    configured (None) stays None.
    """
    conf = self._extended_conf
    assert conf is not None
    ranges = await self.request_maximal_ranges_of_x_drives()
    wraps = await self.request_working_envelopes_per_arm()

    def _with_geometry(
      drive: Optional[DriveConfiguration], side: str, width: float
    ) -> Optional[DriveConfiguration]:
      if drive is None:
        return None
      wrap, workspace_range = wraps[side]
      if wrap == 0:  # arm not installed
        return None
      return replace(drive, width=width, x_range=ranges[side], workspace_range=workspace_range)

    left_x_drive = _with_geometry(conf.left_x_drive, "left", conf.left_x_arm_width)
    assert left_x_drive is not None, "STAR must have a left X-arm"

    return replace(
      conf,
      left_x_drive=left_x_drive,
      right_x_drive=_with_geometry(conf.right_x_drive, "right", conf.right_x_arm_width),
    )

  def _simulated_x_reach_max(self) -> float:
    """Rightmost reachable X (mm) in simulation, from the deck's reachable range."""
    deck = self._deck
    if isinstance(deck, HamiltonDeck):
      return deck.rails_to_location(deck.num_rails).x
    if deck is not None:
      return deck.get_size_x()
    return 1338.0  # nominal STAR reach

  async def request_maximal_ranges_of_x_drives(self) -> Dict[str, Tuple[float, float]]:
    x_range = (_DUAL_RAIL_LEFT_X_MIN, self._simulated_x_reach_max())
    return {"left": x_range, "right": x_range}

  async def request_working_envelopes_per_arm(
    self,
  ) -> Dict[str, Tuple[float, Tuple[float, float]]]:
    workspace = (-323.2, self._simulated_x_reach_max())
    left = (595.2, workspace)  # wrap, workspace
    # A wrap of 0 signals "arm not installed" (per the base method's contract).
    right_installed = self.extended_conf.right_x_drive is not None
    right = (595.2, workspace) if right_installed else (0.0, (0.0, 0.0))
    return {"left": left, "right": right}

  # # # # # # # # 1_000 uL Channel: Basic Commands # # # # # # # #

  async def request_tip_presence(self) -> List[Optional[bool]]:
    """Return mock tip presence based on the tip tracker state.

    Returns:
      A list of length `num_channels` where each element is `True` if a tip is mounted,
      `False` if not, or `None` if unknown.
    """
    return [self.head[ch].has_tip for ch in range(self.num_channels)]

  async def request_z_pos_channel_n(self, channel: int) -> float:
    return 285.0

  async def channel_dispensing_drive_request_position(
    self, channel_idx: int, simulated_value: float = 0.0
  ) -> float:
    """Override to return mock dispensing drive position.

    This method is called when the system needs to know the current position
    of a channel's dispensing drive (e.g., before emptying tips).

    Returns a mock position with a default value of 0.0 for all channels.
    """
    if not (0 <= channel_idx < self.num_channels):
      raise ValueError(f"channel_idx must be between 0 and {self.num_channels - 1}")

    return simulated_value

  async def channel_request_y_minimum_spacing(self, channel_idx: int) -> float:
    """Return mock minimum Y spacing for the given channel.

    Returns the value stored in ``_channels_minimum_y_spacing`` (set during
    ``__init__()``) without issuing any hardware commands.
    """
    if not 0 <= channel_idx <= self.num_channels - 1:
      raise ValueError(
        f"channel_idx must be between 0 and {self.num_channels - 1}, got {channel_idx}."
      )
    return self._channels_minimum_y_spacing[channel_idx]

  async def channels_request_y_minimum_spacing(self) -> List[float]:
    """Return mock per-channel minimum Y spacings for all channels."""
    return list(self._channels_minimum_y_spacing)

  async def move_channel_y(self, channel: int, y: float):
    logger.info("moving channel %s to y: %s", channel, y)

  async def move_channel_x(self, channel: int, x: float):
    logger.info("moving channel %s to x: %s", channel, x)

  async def move_all_channels_in_z_safety(self):
    logger.info("moving all channels to z safety")

  async def position_channels_in_z_direction(self, zs: Dict[int, float]):
    logger.info("positioning channels in z: %s", zs)

  # # # # # # # # 1_000 uL Channel: Complex Commands # # # # # # # #

  async def step_off_foil(
    self,
    wells: Union[Well, List[Well]],
    front_channel: int,
    back_channel: int,
    move_inwards: float = 2,
    move_height: float = 15,
  ):
    logger.info(
      "stepping off foil | wells: %s | front channel: %s | "
      "back channel: %s | move inwards: %s | move height: %s",
      wells,
      front_channel,
      back_channel,
      move_inwards,
      move_height,
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
    logger.info(
      "piercing foil | wells: %s | piercing channels: %s | "
      "hold down channels: %s | move inwards: %s | "
      "spread: %s | one by one: %s | distance from bottom: %s",
      wells,
      piercing_channels,
      hold_down_channels,
      move_inwards,
      spread,
      one_by_one,
      distance_from_bottom,
    )

  # # # # # # # # Extension: 96-Head # # # # # # # #

  async def head96_request_firmware_version(self) -> datetime.date:
    """Return mock 96-head firmware version."""
    return datetime.date(2023, 1, 1)

  # The Y/Z drive speed/acceleration registers a 2013+ (2023 mock) head reports at setup, returned
  # through the real unit conversions so the seeded defaults match a live machine's factory values.
  async def head96_request_y_speed(self) -> float:
    return self._head96_y_drive_increment_to_mm(25000)

  async def head96_request_y_acceleration(self) -> float:
    return self._head96_y_drive_increment_to_mm(35000)

  async def head96_request_z_speed(self) -> float:
    return 85.0

  async def head96_request_z_acceleration(self) -> float:
    return 400.0

  async def head96_request_tip_presence(self) -> int:
    """Mock 96-head tip presence from the tip tracker: 1 if any channel holds a tip, else 0.

    Raises if tip tracking is disabled, since the tracker is then not updated and has no state to report.
    """
    if not does_tip_tracking() or self.head96 is None:
      raise RuntimeError(
        "cannot report 96-head tip presence with tip tracking disabled in simulation; "
        "enable it with set_tip_tracking(True) or call with requires_tip=False"
      )
    return int(any(tracker.has_tip for tracker in self.head96.values()))

  # # # # # # # # Extension: iSWAP # # # # # # # #

  async def request_iswap_initialization_status(self) -> bool:
    """Return mock iSWAP initialization status."""
    return True

  @property
  def iswap_parked(self) -> bool:
    return self._iswap_parked is True

  async def move_iswap_x(
    self,
    x_position: float,
    acceleration_level: int = 3,
    current_protection_limiter: int = 7,
  ):
    logger.info("moving iswap x to %s", x_position)

  async def move_iswap_y(
    self,
    y_position: float,
    speed: float = 220.0,
    acceleration_level: int = 2,
    current_protection_limiter: int = 7,
    make_space: bool = False,
  ):
    logger.info("moving iswap y to %s", y_position)

  async def move_iswap_z(
    self,
    z_position: float,
    speed: float = 118.0,
    acceleration: float = 643.66,
    current_protection_limiter: int = 6,
  ):
    logger.info("moving iswap z to %s", z_position)

  @asynccontextmanager
  async def slow_iswap(self, wrist_velocity: int = 20_000, gripper_velocity: int = 20_000):
    """A context manager that sets the iSWAP to slow speed during the context."""
    assert 20 <= gripper_velocity <= 75_000, "Gripper velocity out of range."
    assert 20 <= wrist_velocity <= 65_000, "Wrist velocity out of range."

    messages = ["start slow iswap"]
    try:
      yield
    finally:
      messages.append("end slow iswap")
      logger.info("%s", " | ".join(messages))

  # # # # # # # # Liquid Level Detection (LLD) # # # # # # # #

  async def request_tip_len_on_channel(self, channel_idx: int) -> float:
    """Return tip length from the tip tracker.

    Args:
      channel_idx: Index of the pipetting channel (0-indexed).

    Returns:
      The tip length in mm from the tip tracker.

    Raises:
      NoTipError: If no tip is present on the channel (via tip tracker).
    """
    tip = self.head[channel_idx].get_tip()
    return tip.total_tip_length

  async def position_channels_in_y_direction(self, ys, make_space=True):
    logger.info("positioning channels in y: %s make_space: %s", ys, make_space)

  async def request_pip_height_last_lld(self):
    return list(range(12))

  async def _run_lld_on_channel_batch(
    self,
    batch,
    containers: List[Container],
    tip_lengths: List[float],
    z_cavity_bottom: List[float],
    z_top: List[float],
    lld_mode: List["STARBackend.LLDMode"],
    search_speed: float,
    n_replicates: int,
  ) -> Dict[int, List[Optional[float]]]:
    """Simulate LLD by computing absolute heights from each container's volume tracker.

    Empty containers report the cavity-bottom Z (relative height 0). Non-empty
    containers report ``cavity_bottom + compute_height_from_volume(volume)`` so the
    parent ``probe_liquid_heights`` can subtract ``z_cavity_bottom`` consistently.
    """
    measurements: Dict[int, List[Optional[float]]] = {}
    for orig_idx in batch.indices:
      container = containers[orig_idx]
      volume = container.tracker.get_used_volume()
      if volume == 0:
        absolute_height = z_cavity_bottom[orig_idx]
      else:
        absolute_height = z_cavity_bottom[orig_idx] + container.compute_height_from_volume(volume)
      measurements[orig_idx] = [absolute_height] * n_replicates
    return measurements
