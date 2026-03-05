import datetime
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


class STARChatterboxBackend(STARBackend):
  """Chatterbox backend for 'STAR'"""

  def __init__(
    self,
    num_channels: int = 8,
    core96_head_installed: bool = True,
    iswap_installed: bool = True,
    channels_minimum_y_spacing: Optional[List[float]] = None,
  ):
    """Initialize a chatter box backend.

    Args:
      num_channels: Number of pipetting channels (default: 8)
      core96_head_installed: Whether the CoRe 96 head is installed (default: True)
      iswap_installed: Whether the iSWAP robotic arm is installed (default: True)
      channels_minimum_y_spacing: Per-channel minimum Y spacing in mm. If None, defaults to
        9.0 for all channels.
    """
    super().__init__()
    self._num_channels = num_channels
    self._iswap_parked = True
    self._core96_head_installed = core96_head_installed
    self._iswap_installed = iswap_installed
    if channels_minimum_y_spacing is not None:
      if len(channels_minimum_y_spacing) != num_channels:
        raise ValueError(
          f"channels_minimum_y_spacing has {len(channels_minimum_y_spacing)} entries, "
          f"expected {num_channels}."
        )
      self._channels_minimum_y_spacing = list(channels_minimum_y_spacing)
    else:
      self._channels_minimum_y_spacing = [9.0] * num_channels

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
    """Return mock machine configuration data.

    Configuration byte `kb` value of 11 (0b00001011) corresponds to a STARlet with:
    1000ul PIP type, iSWAP installed, auto load installed.
    """
    return MachineConfiguration(
      configuration_data_1=ConfigurationData1.from_int(11),
      num_pip_channels=self.num_channels,
    )

  async def request_extended_configuration(self) -> ExtendedConfiguration:
    """Return mock extended configuration data.

    Dynamically generated based on __init__ parameters.
    """
    xl_value = 0
    if self._iswap_installed:
      xl_value |= 0b10  # iSWAP (bit 1)
    if self._core96_head_installed:
      xl_value |= 0b100  # 96-head (bit 2)

    self._extended_conf = ExtendedConfiguration(
      configuration_data_2=ConfigurationData2.from_int(65537),
      configuration_data_3=0,
      instrument_size_slots=30,
      auto_load_size_slots=30,
      tip_waste_x_position=800.0,
      left_x_drive_config_byte_1=XDriveConfigByte1.from_int(xl_value),
      left_x_drive_config_byte_2=XDriveConfigByte2.from_int(0),
      right_x_drive_config_byte_1=XDriveConfigByte1.from_int(0),
      right_x_drive_config_byte_2=XDriveConfigByte2.from_int(0),
      min_iswap_collision_free_position=350.0,
      max_iswap_collision_free_position=600.0,
      left_x_arm_width=370.0,
      right_x_arm_width=370.0,
      num_pip_channels=self.num_channels,
      num_xl_channels=0,
      num_robotic_channels=0,
      min_raster_pitch_pip_channels=9.0,
      min_raster_pitch_xl_channels=36.0,
      min_raster_pitch_robotic_channels=36.0,
      pip_maximal_y_position=606.5,
      left_arm_min_y_position=6.0,
      right_arm_min_y_position=6.0,
    )
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
