import dataclasses
import datetime
import json
import typing
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from pylabrobot.hamilton.star.driver.features.autoload import AutoloadConfiguration
from pylabrobot.hamilton.star.driver.features.head96 import Head96Configuration
from pylabrobot.hamilton.star.driver.features.head384 import Head384Configuration
from pylabrobot.hamilton.star.driver.features.iswap import iSWAPConfiguration
from pylabrobot.hamilton.star.driver.features.pipettes import PipettesConfiguration
from pylabrobot.hamilton.star.driver.features.x_arm import XArmConfiguration


@dataclass
class DeviceConfiguration:
  """The device's installed hardware and geometry.

  Holds both halves of what the master answers about the device it is on: the device-configuration
  fields and the extended-configuration fields.
  """

  # -- which device this is, and what it is running --
  serial_number: Optional[str] = None
  """What the device calls itself. Distinct from the USB serial a driver picks a device off the bus
  with: this is what the device answers."""
  firmware_version: Optional[str] = None
  """The master's firmware version."""
  firmware_date: Optional[datetime.date] = None
  """The date in that version."""

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
  head96_installed: bool = False
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
  head384_installed: bool = False
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
  """Device size in slots, X range (xt). Default: 54."""
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


# -- reading and writing these as JSON ------------------------------------------------------------
# JSON loses three things these configurations rely on: a tuple comes back a list, a dict key comes
# back a string, and a date comes back its own text. What each field is declared to be is enough to
# put all three back, so writing is `dataclasses.fields` and reading is the same walk against the
# declared types.


def to_jsonable(value: Any) -> Any:
  """The value as JSON holds it.

  Args:
    value: what to convert - a configuration, or anything one holds.

  Returns:
    The same value in types `json.dump` accepts.
  """
  if dataclasses.is_dataclass(value) and not isinstance(value, type):
    return {
      field.name: to_jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)
    }
  if isinstance(value, datetime.date):
    return value.isoformat()
  if isinstance(value, (list, tuple)):
    return [to_jsonable(item) for item in value]
  if isinstance(value, dict):
    # Keys are written as text because JSON has no other kind. What they were is on the field.
    return {str(key): to_jsonable(item) for key, item in value.items()}
  return value


def _restore(hint: Any, value: Any) -> Any:
  """One value, back in the type its field is declared to hold.

  Args:
    hint: the declared type.
    value: the value as JSON held it.

  Returns:
    The value in the declared type.
  """
  if value is None:
    return None

  origin = typing.get_origin(hint)
  args = typing.get_args(hint)

  if origin is Union:  # Optional[X] is Union[X, None]; the None case returned above.
    declared = [arg for arg in args if arg is not type(None)]
    return _restore(declared[0], value) if len(declared) == 1 else value
  if origin is tuple:
    # Fixed-length tuples name a type per position; `Tuple[X, ...]` names one for all of them.
    if len(args) == 2 and args[1] is Ellipsis:
      return tuple(_restore(args[0], item) for item in value)
    return tuple(_restore(arg, item) for arg, item in zip(args, value))
  if origin is list:
    return [_restore(args[0], item) for item in value]
  if origin is dict:
    key_hint, value_hint = args
    return {_restore(key_hint, key): _restore(value_hint, item) for key, item in value.items()}
  if hint is int and isinstance(value, str):
    # A dict keyed by int: JSON wrote the key as text, and the field says what it was.
    return int(value)
  if hint is datetime.date:
    return datetime.date.fromisoformat(value)
  if dataclasses.is_dataclass(hint) and isinstance(hint, type):
    # A nested configuration: rebuilt field by field against what its own class declares. Names the
    # class does not have are left out, so a file written by a driver that has since dropped a
    # field still loads.
    field_types = typing.get_type_hints(hint)
    named = {field.name for field in dataclasses.fields(hint)}
    return hint(**{n: _restore(field_types[n], v) for n, v in value.items() if n in named})
  return value


# What each name in a saved configuration is, so reading one back knows what to build. A feature an
# arm carries is looked up in the first; one fitted to the device itself in the second.
ARM_FEATURE_CONFIGURATIONS: Dict[str, type] = {
  "pipettes": PipettesConfiguration,
  "head96": Head96Configuration,
  "head384": Head384Configuration,
  "iswap": iSWAPConfiguration,
}
DEVICE_FEATURE_CONFIGURATIONS: Dict[str, type] = {
  "autoload": AutoloadConfiguration,
}


def read_configuration(path: str) -> Dict[str, Any]:
  """Read a saved configuration back into the dataclasses it was written from.

  Shaped as the device is: the device's own configuration, the features each arm carries under the
  side that carries them, and the features fitted to the device itself beside them. Nothing here
  says how many of anything there may be, so a device that grows a second head reads back without
  this having to change.

  Args:
    path: a file `STARDriver.save_configuration` wrote.

  Returns:
    `{"device": DeviceConfiguration, "arms": {side: {name: configuration}}, <name>: configuration}`.
    Names this driver does not know are left out.
  """
  with open(path, encoding="utf-8") as f:
    saved = json.load(f)

  read: Dict[str, Any] = {}
  if "device" in saved:
    read["device"] = _restore(DeviceConfiguration, saved["device"])
  read["arms"] = {
    side: {
      name: _restore(ARM_FEATURE_CONFIGURATIONS[name], value)
      for name, value in carried.items()
      if name in ARM_FEATURE_CONFIGURATIONS
    }
    for side, carried in saved.get("arms", {}).items()
  }
  for name, configuration in DEVICE_FEATURE_CONFIGURATIONS.items():
    if name in saved:
      read[name] = _restore(configuration, saved[name])
  return read
