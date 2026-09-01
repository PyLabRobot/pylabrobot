from dataclasses import dataclass
from typing import Optional

from pylabrobot.hamilton.star.driver.features.x_arm import XArmConfiguration


@dataclass
class DeviceConfiguration:
  """The instrument's installed hardware and geometry.

  Holds both halves of the machine's configuration: the RM (Request Machine Configuration)
  fields and the QM (Request Extended Configuration) fields, which together match the
  instrument-configuration parameter set.
  """

  # kb byte (configuration data 1)
  pip_type_1000ul: bool = False
  """Bit 0: PIP Type. False = 300ul, True = 1000ul."""
  kb_iswap_installed: bool = False
  """Bit 1: ISWAP. False = none, True = installed."""
  main_front_cover_monitoring_installed: bool = False
  """Bit 2: Main front cover monitoring. False = none, True = installed."""
  autoload_installed: bool = False
  """Bit 3: Autoload. False = none, True = installed."""
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

  # ka (configuration data 2, 24-bit)
  left_x_drive_large: bool = False
  """Bit 0: Left X drive. False = small, True = large."""
  ka_head96_installed: bool = False
  """Bit 1: 96-head. False = none, True = installed."""
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
  autoload_size_slots: int = 54
  """Autoload size in slots (xa). Default: 54."""
  tip_waste_x_position: float = 1340.0
  """Tip waste X-position [mm] (xw). Default: 1340.0."""
  left_arm: Optional[XArmConfiguration] = None
  """Left X-arm configuration (xl + xn)."""
  right_arm: Optional[XArmConfiguration] = None
  """Right X-arm configuration (xr + xo), or None when no right arm is installed."""
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
