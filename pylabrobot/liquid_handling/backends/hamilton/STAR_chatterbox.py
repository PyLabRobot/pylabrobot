import copy
import datetime
import warnings
from contextlib import asynccontextmanager
from typing import Dict, List, Literal, Optional, Union

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.hamilton.STAR_backend import (
  ConfigurationData1,
  ConfigurationData2,
  ExtendedConfiguration,
  Head96Information,
  MachineConfiguration,
  STARBackend,
  XDriveConfigByte1,
  XDriveConfigByte2,
)
from pylabrobot.resources.well import Well

_DEFAULT_MACHINE_CONFIGURATION = MachineConfiguration(
  configuration_data_1=ConfigurationData1(
    pip_type_1000ul=True,
    iswap_installed=True,
    main_front_cover_monitoring_installed=False,
    auto_load_installed=True,
    wash_station_1_installed=False,
    wash_station_2_installed=False,
    temp_controlled_carrier_1_installed=False,
    temp_controlled_carrier_2_installed=False,
  ),
  num_pip_channels=8,
)

_DEFAULT_EXTENDED_CONFIGURATION = ExtendedConfiguration(
  configuration_data_2=ConfigurationData2(
    left_x_drive_large=True,
    core_96_head_installed=False,
    right_x_drive_large=False,
    pump_station_1_installed=False,
    pump_station_2_installed=False,
    wash_station_1_type_cr=False,
    wash_station_2_type_cr=False,
    left_cover_installed=False,
    right_cover_installed=False,
    additional_front_cover_monitoring_installed=False,
    pump_station_3_installed=False,
    multi_channel_nano_pipettor_installed=False,
    dispensing_head_384_installed=False,
    xl_channels_installed=False,
    tube_gripper_installed=False,
    waste_direction_left=False,
    iswap_gripper_wide=True,
    additional_channel_nano_pipettor_installed=False,
    imaging_channel_installed=False,
    robotic_channel_installed=False,
    channel_order_ox_first=False,
    x0_interface_ham_can=False,
    park_heads_with_iswap_off=False,
  ),
  configuration_data_3=0,
  instrument_size_slots=30,
  auto_load_size_slots=30,
  tip_waste_x_position=800.0,
  left_x_drive_config_byte_1=XDriveConfigByte1(
    pip_installed=False,
    iswap_installed=True,
    core_96_head_installed=True,
    nano_pipettor_installed=False,
    dispensing_head_384_installed=False,
    xl_channels_installed=False,
    tube_gripper_installed=False,
    imaging_channel_installed=False,
  ),
  left_x_drive_config_byte_2=XDriveConfigByte2(robotic_channel_installed=False),
  right_x_drive_config_byte_1=XDriveConfigByte1(
    pip_installed=True,
    iswap_installed=True,
    core_96_head_installed=True,
    nano_pipettor_installed=False,
    dispensing_head_384_installed=False,
    xl_channels_installed=False,
    tube_gripper_installed=False,
    imaging_channel_installed=False,
  ),
  right_x_drive_config_byte_2=XDriveConfigByte2(robotic_channel_installed=False),
  min_iswap_collision_free_position=350.0,
  max_iswap_collision_free_position=600.0,
  left_x_arm_width=370.0,
  right_x_arm_width=370.0,
  num_pip_channels=8,
  num_xl_channels=0,
  num_robotic_channels=0,
  min_raster_pitch_pip_channels=9.0,
  min_raster_pitch_xl_channels=36.0,
  min_raster_pitch_robotic_channels=36.0,
  pip_maximal_y_position=606.5,
  left_arm_min_y_position=6.0,
  right_arm_min_y_position=6.0,
)


class STARChatterboxBackend(STARBackend):
  """Chatterbox backend for 'STAR'"""

  def __init__(
    self,
    num_channels: int = 8,
    machine_configuration: MachineConfiguration = _DEFAULT_MACHINE_CONFIGURATION,
    extended_configuration: ExtendedConfiguration = _DEFAULT_EXTENDED_CONFIGURATION,
    # deprecated parameters
    core96_head_installed: Optional[bool] = None,
    iswap_installed: Optional[bool] = None,
  ):
    """Initialize a chatter box backend.

    Args:
      num_channels: Number of pipetting channels (default: 8)
      machine_configuration: Machine configuration to return from `request_machine_configuration`.
      extended_configuration: Extended configuration to return from `request_extended_configuration`.
      core96_head_installed: Deprecated. Set `extended_configuration.left_x_drive_config_byte_1
        .core_96_head_installed` instead.
      iswap_installed: Deprecated. Set `extended_configuration.left_x_drive_config_byte_1
        .iswap_installed` instead.
    """
    super().__init__()
    self._num_channels = num_channels
    self._iswap_parked = True

    if core96_head_installed is not None or iswap_installed is not None:
      extended_configuration = copy.deepcopy(extended_configuration)
      xl = copy.deepcopy(extended_configuration.left_x_drive_config_byte_1)
      if core96_head_installed is not None:
        warnings.warn(
          "core96_head_installed is deprecated. Pass an ExtendedConfiguration with "
          "left_x_drive_config_byte_1.core_96_head_installed set instead.",
          DeprecationWarning,
          stacklevel=2,
        )
        xl.core_96_head_installed = core96_head_installed
      if iswap_installed is not None:
        warnings.warn(
          "iswap_installed is deprecated. Pass an ExtendedConfiguration with "
          "left_x_drive_config_byte_1.iswap_installed set instead.",
          DeprecationWarning,
          stacklevel=2,
        )
        xl.iswap_installed = iswap_installed
      extended_configuration.left_x_drive_config_byte_1 = xl

    self._machine_configuration = machine_configuration
    self._extended_conf = extended_configuration

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
    conf = await self.request_machine_configuration()
    self._extended_conf = await self.request_extended_configuration()

    self.autoload_installed = conf.configuration_data_1.auto_load_installed
    self.iswap_installed = self.extended_conf.left_x_drive_config_byte_1.iswap_installed
    self.core96_head_installed = (
      self.extended_conf.left_x_drive_config_byte_1.core_96_head_installed
    )

    # Mock firmware information for 96-head if installed
    if self.core96_head_installed and not skip_core96_head:
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
