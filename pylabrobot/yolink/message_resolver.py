"""YoLink cloud message resolver."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from math import log2
from typing import TYPE_CHECKING, Any

from .const import (
  ATTR_DEVICE_MULTI_WATER_METER_CONTROLLER,
  ATTR_DEVICE_SMART_REMOTER,
  ATTR_DEVICE_SOIL_TH_SENSOR,
  ATTR_DEVICE_SPRINKLER,
  ATTR_DEVICE_SPRINKLER_V2,
  ATTR_DEVICE_WATER_DEPTH_SENSOR,
  ATTR_DEVICE_WATER_METER_CONTROLLER,
)
from .unit_helper import UnitOfVolume, VolumeConverter

if TYPE_CHECKING:
  from .device import YoLinkDevice


def smart_remoter_message_resolve(msg_data: dict[str, Any], event_type: str) -> None:
  """SmartRemoter message resolve."""
  if msg_data is not None:
    btn_press_event = msg_data.get("event")
    if btn_press_event is not None:
      if event_type == "Report":
        msg_data["event"] = None
      else:
        key_mask = btn_press_event["keyMask"]
        button_sequence = 0 if key_mask == 0 else (int(log2(key_mask)) + 1)
        # replace with button sequence
        msg_data["event"]["keyMask"] = button_sequence


def water_depth_sensor_message_resolve(msg_data: dict[str, Any], dev_attrs: dict[str, Any]) -> None:
  """WaterDepthSensor message resolve."""
  if msg_data is not None:
    depth_value = msg_data.get("waterDepth")
    if depth_value is not None:
      # default range settings if range and desity was not set.
      dev_range = 5
      dev_density = 1
      if dev_attrs is not None and (range_attrs := dev_attrs.get("range")) is not None:
        dev_range = range_attrs["range"]
        dev_density = range_attrs["density"]
      msg_data["waterDepth"] = round((dev_range * (depth_value / 1000)) / dev_density, 3)


def water_meter_controller_message_resolve(msg_data: dict[str, Any], device_model: str) -> None:
  """WaterMeterController message resolve."""
  if msg_data is not None and ((meter_state := msg_data.get("state")) is not None):
    meter_step_factor: int = 10
    # for some reason meter value can't be read
    meter_value = meter_state.get("meter")
    if meter_value is not None:
      meter_unit = UnitOfVolume.GALLONS
      if (meter_attrs := msg_data.get("attributes")) is not None:
        if device_model.startswith("YS5009"):
          meter_step_factor = (
            1 / (_meter_step_factor / (1000 * 100))
            if (_meter_step_factor := meter_attrs.get("meterStepFactor")) is not None
            else 10
          )
        else:
          meter_step_factor = (
            _meter_step_factor
            if (_meter_step_factor := meter_attrs.get("meterStepFactor")) is not None
            else 10
          )
        meter_unit = (
          UnitOfVolume(_meter_unit)
          if (_meter_unit := meter_attrs.get("meterUnit")) is not None
          else UnitOfVolume.GALLONS
        )
      _meter_reading = None
      if meter_step_factor < 0:
        _meter_reading = meter_value * abs(meter_step_factor)
      else:
        _meter_reading = meter_value / meter_step_factor
      meter_value = VolumeConverter.convert(_meter_reading, meter_unit, UnitOfVolume.CUBIC_METERS)
      msg_data["meter_reading"] = float(
        Decimal(meter_value).quantize(Decimal(".00000"), rounding=ROUND_DOWN)
      )
    msg_data["valve_state"] = meter_state["valve"]


def multi_water_meter_controller_message_resolve(
  msg_data: dict[str, Any],
  device_model: str,
) -> None:
  """MultiWaterMeterController message resolve."""
  if msg_data is not None and ((meter_state := msg_data.get("state")) is not None):
    meter_step_factor: int = 10
    meter_reading_values: dict = meter_state.get("meters")
    if meter_reading_values is not None:
      meter_unit = UnitOfVolume.GALLONS
      if (meter_attrs := msg_data.get("attributes")) is not None:
        if device_model.startswith("YS5029"):
          meter_step_factor = (
            1 / (_meter_step_factor / (1000 * 100))
            if (_meter_step_factor := meter_attrs.get("meterStepFactor")) is not None
            else 10
          )
        else:
          meter_step_factor = (
            _meter_step_factor
            if (_meter_step_factor := meter_attrs.get("meterStepFactor")) is not None
            else 10
          )
        meter_unit = (
          UnitOfVolume(_meter_unit)
          if (_meter_unit := meter_attrs.get("meterUnit")) is not None
          else UnitOfVolume.GALLONS
        )
      _meter_1_reading = None
      if meter_step_factor < 0:
        _meter_1_reading = meter_reading_values["0"] * abs(meter_step_factor)
      else:
        _meter_1_reading = meter_reading_values["0"] / meter_step_factor
      meter_reading_values["0"] = VolumeConverter.convert(
        _meter_1_reading,
        meter_unit,
        UnitOfVolume.CUBIC_METERS,
      )
      _meter_2_reading = None
      if meter_step_factor < 0:
        _meter_2_reading = meter_reading_values["1"] * abs(meter_step_factor)
      else:
        _meter_2_reading = meter_reading_values["1"] / meter_step_factor
      meter_reading_values["1"] = VolumeConverter.convert(
        _meter_2_reading,
        meter_unit,
        UnitOfVolume.CUBIC_METERS,
      )
      msg_data["meter_1_reading"] = float(
        Decimal(meter_reading_values["0"]).quantize(Decimal(".00000"), rounding=ROUND_DOWN)
      )
      msg_data["meter_2_reading"] = float(
        Decimal(meter_reading_values["1"]).quantize(Decimal(".00000"), rounding=ROUND_DOWN)
      )
    # for some reason meter value can't be read
    if (meter_valves := meter_state.get("valves")) is not None:
      msg_data["valve_1_state"] = meter_valves["0"]
      msg_data["valve_2_state"] = meter_valves["1"]


def soil_thc_sensor_message_resolve(
  msg_data: dict[str, Any],
) -> None:
  """SoilThcSensor message resolve."""
  if msg_data is not None and ((state := msg_data.get("state")) is not None):
    msg_data["temperature"] = state.get("temperature")
    msg_data["humidity"] = state.get("humidity")
    msg_data["conductivity"] = state.get("conductivity")


def sprinkler_message_resolve(
  device: YoLinkDevice,
  msg_data: dict[str, Any],
  msg_type: str | None = None,
) -> None:
  """Sprinkler message resolve."""
  if msg_data is not None:
    if (state := msg_data.get("state")) is not None:
      device._state = {"mode": state.get("mode")}
      if (watering_data := state.get("watering")) is not None:
        msg_data["valve"] = watering_data["left"] != watering_data["total"]
    if msg_type == "waterReport":
      if device._state is not None:
        msg_data["state"] = {"mode": device._state.get("mode")}
      if (event := msg_data.get("event")) is not None:
        msg_data["valve"] = event == "start"


def sprinkler_v2_message_resolve(
  msg_data: dict[str, Any],
) -> None:
  """Sprinkler V2 message resolve."""
  if msg_data is not None and ((state := msg_data.get("state")) is not None):
    msg_data["valve"] = state.get("running")


def resolve_message(device: YoLinkDevice, msg_data: dict[str, Any], msg_type: str | None) -> None:
  """Resolve device message."""
  if device.device_type == ATTR_DEVICE_WATER_DEPTH_SENSOR:
    water_depth_sensor_message_resolve(msg_data, device.device_attrs)
  elif device.device_type == ATTR_DEVICE_WATER_METER_CONTROLLER:
    water_meter_controller_message_resolve(msg_data, device.device_model_name)
  elif device.device_type == ATTR_DEVICE_MULTI_WATER_METER_CONTROLLER:
    multi_water_meter_controller_message_resolve(msg_data, device.device_model_name)
  elif device.device_type == ATTR_DEVICE_SOIL_TH_SENSOR:
    soil_thc_sensor_message_resolve(msg_data)
  elif device.device_type == ATTR_DEVICE_SPRINKLER:
    sprinkler_message_resolve(device, msg_data, msg_type)
  elif device.device_type == ATTR_DEVICE_SPRINKLER_V2:
    sprinkler_v2_message_resolve(msg_data)


def resolve_sub_message(device: YoLinkDevice, msg_data: dict[str, Any], msg_type: str) -> None:
  """Resolve device pushing message."""
  if device.device_type == ATTR_DEVICE_SMART_REMOTER:
    smart_remoter_message_resolve(msg_data, msg_type)
  else:
    resolve_message(device, msg_data, msg_type)
