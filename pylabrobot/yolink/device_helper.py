"""Helper functions for YoLink devices."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from .device import YoLinkDevice

from .const import (
  ATTR_DEVICE_CO_SMOKE_SENSOR,
  ATTR_DEVICE_DIMMER,
  ATTR_DEVICE_DOOR_SENSOR,
  ATTR_DEVICE_FINGER,
  ATTR_DEVICE_HUB,
  ATTR_DEVICE_LEAK_SENSOR,
  ATTR_DEVICE_LOCK,
  ATTR_DEVICE_LOCK_V2,
  ATTR_DEVICE_MANIPULATOR,
  ATTR_DEVICE_MOTION_SENSOR,
  ATTR_DEVICE_MULTI_OUTLET,
  ATTR_DEVICE_MULTI_WATER_METER_CONTROLLER,
  ATTR_DEVICE_OUTLET,
  ATTR_DEVICE_POWER_FAILURE_ALARM,
  ATTR_DEVICE_SIREN,
  ATTR_DEVICE_SMART_REMOTER,
  ATTR_DEVICE_SMOKE_ALARM,
  ATTR_DEVICE_SOIL_TH_SENSOR,
  ATTR_DEVICE_SPEAKER_HUB,
  ATTR_DEVICE_SPRINKLER,
  ATTR_DEVICE_SPRINKLER_V2,
  ATTR_DEVICE_SWITCH,
  ATTR_DEVICE_TH_SENSOR,
  ATTR_DEVICE_THERMOSTAT,
  ATTR_DEVICE_VIBRATION_SENSOR,
  ATTR_DEVICE_WATER_DEPTH_SENSOR,
  ATTR_DEVICE_WATER_METER_CONTROLLER,
  ATTR_GARAGE_DOOR_CONTROLLER,
)


def get_device_net_mode(device: YoLinkDevice) -> str | None:
  """Get device network mode."""
  # Assuming all devices are WiFi for this example
  device_type = device.device_type
  device_model = device.device_model_name
  device_short_model = None
  if device_model is not None:
    device_short_model = device_model.split("-")[0]
  if device_type in [
    ATTR_DEVICE_LEAK_SENSOR,
    ATTR_DEVICE_DOOR_SENSOR,
    ATTR_DEVICE_TH_SENSOR,
    ATTR_DEVICE_MOTION_SENSOR,
    ATTR_DEVICE_CO_SMOKE_SENSOR,
    ATTR_DEVICE_POWER_FAILURE_ALARM,
    ATTR_DEVICE_SOIL_TH_SENSOR,
    ATTR_DEVICE_VIBRATION_SENSOR,
    ATTR_DEVICE_SMART_REMOTER,
    ATTR_DEVICE_WATER_DEPTH_SENSOR,
    ATTR_DEVICE_SMOKE_ALARM,
  ]:
    if device_short_model in [
      "YS7A02",
      "YS8006",
    ]:
      return "D"
    return "A"
  if device_type in [
    ATTR_DEVICE_MANIPULATOR,
    ATTR_DEVICE_OUTLET,
    ATTR_DEVICE_MULTI_OUTLET,
    ATTR_DEVICE_THERMOSTAT,
    ATTR_DEVICE_SIREN,
    ATTR_DEVICE_SWITCH,
    ATTR_GARAGE_DOOR_CONTROLLER,
    ATTR_DEVICE_DIMMER,
    ATTR_DEVICE_SPRINKLER,
  ]:
    if device_short_model in [
      #
      "YS4909",
      # Mainpulator(Class D)
      "YS5001",
      "YS5002",
      "YS5003",
      "YS5012",
      # Switch(Class D)
      "YS5709",
      # Siren(Class D)
      "YS7104",
      "YS7105",
      "YS7107",
    ]:
      return "D"
    return "C"
  if device_type in [
    ATTR_DEVICE_FINGER,
    ATTR_DEVICE_LOCK,
    ATTR_DEVICE_LOCK_V2,
    ATTR_DEVICE_WATER_METER_CONTROLLER,
    ATTR_DEVICE_MULTI_WATER_METER_CONTROLLER,
    ATTR_DEVICE_SPRINKLER_V2,
  ]:
    if device_short_model in ["YS5007"]:
      return "A"
    return "D"
  if device_type in [ATTR_DEVICE_HUB, ATTR_DEVICE_SPEAKER_HUB]:
    return "Hub"
  return None


def get_device_keepalive_time(device: YoLinkDevice) -> int:
  """Get device keepalive time in seconds."""
  device_class_mode = get_device_net_mode(device)
  if device_class_mode in ["A", "D"]:
    return 32400
  if device_class_mode == "C":
    return 3600
  if device_class_mode == "Hub":
    return 600
