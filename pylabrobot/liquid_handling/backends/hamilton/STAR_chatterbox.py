import copy
import datetime
import warnings
from contextlib import asynccontextmanager
from typing import Dict, List, Literal, Optional, Union

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.hamilton.STAR_backend import (
  DriveConfiguration,
  ExtendedConfiguration,
  Head96Information,
  MachineConfiguration,
  STARBackend,
)
from pylabrobot.liquid_handling.pipette_batch_scheduling import (
  validate_probing_inputs,
)
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.well import Well

# Type alias for nested enum (for cleaner signatures)
LLDMode = STARBackend.LLDMode

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


class STARChatterboxBackend(STARBackend):
  """Chatterbox backend for 'STAR'"""

  def __init__(
    self,
    num_channels: int = 8,
    machine_configuration: MachineConfiguration = _DEFAULT_MACHINE_CONFIGURATION,
    extended_configuration: ExtendedConfiguration = _DEFAULT_EXTENDED_CONFIGURATION,
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
      self._head96_information = Head96Information(
        fw_version=datetime.date(2023, 1, 1),
        supports_clot_monitoring_clld=False,
        stop_disc_type="core_ii",
        instrument_type="FM-STAR",
        head_type="96 head II",
      )
    else:
      self._head96_information = None

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
    print(cmd)
    return None

  async def send_raw_command(
    self,
    command: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    print(command)
    return None

  # # # # # # # # STAR configuration # # # # # # # #

  async def request_machine_configuration(self) -> MachineConfiguration:
    return self._machine_configuration

  async def request_extended_configuration(self) -> ExtendedConfiguration:
    assert self._extended_conf is not None
    return self._extended_conf

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
    print(f"moving channel {channel} to y: {y}")

  async def move_channel_x(self, channel: int, x: float):
    print(f"moving channel {channel} to x: {x}")

  async def move_all_channels_in_z_safety(self):
    print("moving all channels to z safety")

  async def position_channels_in_z_direction(self, zs: Dict[int, float]):
    print(f"positioning channels in z: {zs}")

  # # # # # # # # 1_000 uL Channel: Complex Commands # # # # # # # #

  async def step_off_foil(
    self,
    wells: Union[Well, List[Well]],
    front_channel: int,
    back_channel: int,
    move_inwards: float = 2,
    move_height: float = 15,
  ):
    print(
      f"stepping off foil | wells: {wells} | front channel: {front_channel} | "
      f"back channel: {back_channel} | move inwards: {move_inwards} | move height: {move_height}"
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
    print(
      f"piercing foil | wells: {wells} | piercing channels: {piercing_channels} | "
      f"hold down channels: {hold_down_channels} | move inwards: {move_inwards} | "
      f"spread: {spread} | one by one: {one_by_one} | distance from bottom: {distance_from_bottom}"
    )

  # # # # # # # # Extension: 96-Head # # # # # # # #

  async def head96_request_firmware_version(self) -> datetime.date:
    """Return mock 96-head firmware version."""
    return datetime.date(2023, 1, 1)

  # # # # # # # # Extension: iSWAP # # # # # # # #

  async def request_iswap_initialization_status(self) -> bool:
    """Return mock iSWAP initialization status."""
    return True

  @property
  def iswap_parked(self) -> bool:
    return self._iswap_parked is True

  async def move_iswap_x(self, x_position: float):
    print("moving iswap x to", x_position)

  async def move_iswap_y(self, y_position: float):
    print("moving iswap y to", y_position)

  async def move_iswap_z(self, z_position: float):
    print("moving iswap z to", z_position)

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
      print(" | ".join(messages))

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
    print("positioning channels in y:", ys, "make_space:", make_space)

  async def request_pip_height_last_lld(self):
    return list(range(12))

  async def probe_liquid_heights(
    self,
    containers: List[Container],
    use_channels: Optional[List[int]] = None,
    resource_offsets: Optional[List[Coordinate]] = None,
    lld_mode: LLDMode = LLDMode.GAMMA,
    search_speed: float = 10.0,
    n_replicates: int = 1,
    move_to_z_safety_after: bool = True,
    min_traverse_height_at_beginning_of_command: Optional[float] = None,
    min_traverse_height_during_command: Optional[float] = None,
    z_position_at_end_of_command: Optional[float] = None,
    x_grouping_tolerance: Optional[float] = None,
  ) -> List[float]:
    """Probe liquid heights by computing from tracked container volumes.

    Instead of simulating hardware LLD, this mock computes liquid heights directly from
    each container's volume tracker using ``container.compute_height_from_volume()``.

    Args:
      containers: List of Container objects to probe, one per channel.
      use_channels: Channel indices to use (0-indexed). Defaults to ``[0, ..., len(containers)-1]``.
      All other parameters: Accepted for API compatibility but unused in mock.

    Returns:
      Liquid heights in mm from cavity bottom for each container, computed from tracked volumes.

    Raises:
      ValueError: If ``use_channels`` is empty, contains out-of-range indices, or if
        ``containers`` and ``use_channels`` have different lengths.
      NoTipError: If any specified channel lacks a tip.
    """
    use_channels = validate_probing_inputs(
      containers=containers,
      use_channels=use_channels,
      num_channels=self.num_channels,
    )

    # Validate tip presence using tip tracker
    for ch in use_channels:
      self.head[ch].get_tip()  # Raises NoTipError if no tip

    heights: List[float] = []
    for container in containers:
      volume = container.tracker.get_used_volume()
      if volume == 0:
        heights.append(0.0)
      else:
        height = container.compute_height_from_volume(volume)
        heights.append(height)

    print(f"probe_liquid_heights: {[f'{h:.2f}' for h in heights]} mm")
    return heights

