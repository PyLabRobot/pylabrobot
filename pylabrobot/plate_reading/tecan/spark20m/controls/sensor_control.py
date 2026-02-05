import logging
from typing import Optional

from .base_control import BaseControl
from .spark_enums import (
  ChillerState,
  InstrumentMessageType,
  ModuleType,
  TemperatureDevice,
  TemperatureState,
)


class SensorControl(BaseControl):
  async def read_barcode(self, force_reading: bool = False) -> Optional[str]:
    """Reads the barcode."""
    command = "BARCODE READ"
    if force_reading:
      command += " FORCE=TRUE"
    # This command uses data channel, which is not fully implemented yet.
    logging.warning("Barcode reading uses data channel, not fully implemented.")
    return await self.send_command(command)

  async def get_barcode_position(self) -> Optional[str]:
    """Gets the barcode reader position."""
    return await self.send_command("?BARCODE POSITION")

  async def get_motors(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the list of motors that can be checked for steploss."""
    command = "#CHECK STEPLOSS MOTOR"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    response = await self.send_command(command)
    return response

  async def check_step_loss(
    self,
    motor: Optional[str] = None,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Checks for step loss on the specified motor."""
    command = "CHECK STEPLOSS"
    if motor:
      command += f" MOTOR={motor}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_step_loss_result(
    self,
    motor: Optional[str] = None,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the result of the last step loss check."""
    command = "?CHECK STEPLOSS"
    if motor:
      command += f" MOTOR={motor}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def check_lid(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Checks the lid status."""
    command = "CHECK LID"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    # This command may use the data channel, not fully implemented yet.
    logging.warning("Lid check may use data channel, not fully implemented.")
    return await self.send_command(command)

  async def get_firmware_counter(
    self,
    counter_name: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the value of a firmware counter."""
    command = "?COUNTER"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" {counter_name}"
    return await self.send_command(command)

  async def get_software_counter(
    self,
    counter_name: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the value of a software counter."""
    command = "?SW_COUNTER"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" {counter_name}"
    return await self.send_command(command)

  async def set_software_counter(
    self,
    counter_name: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Sets the value of a software counter."""
    command = "SW_COUNTER"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" {counter_name}={value}"
    return await self.send_command(command)

  async def get_module_counter(self, counter: str, module: ModuleType) -> Optional[str]:
    """Gets the value of a specific counter for a module."""
    return await self.send_command(f"?COUNTER {counter} MODULE={module.value}")

  async def get_buzzer_states(self) -> Optional[str]:
    """Gets the available buzzer states."""
    response = await self.send_command("#GASCONTROL BUZZER")
    return response

  async def enable_hardware_button(self, button: str) -> Optional[str]:
    """Enables the specified hardware button."""
    return await self.send_command(f"HWBUTTON {button.upper()}=ENABLED")

  async def disable_hardware_button(self, button: str) -> Optional[str]:
    """Disables the specified hardware button."""
    return await self.send_command(f"HWBUTTON {button.upper()}=DISABLED")

  async def is_hardware_button_enabled(self, button: str) -> bool:
    """Checks if the specified hardware button is enabled."""
    response = await self.send_command(f"?HWBUTTON {button.upper()}")
    return response == f"{button.upper()}=ENABLED"

  async def get_instrument_state(self) -> Optional[str]:
    """Gets the instrument state."""
    return await self.send_command("?INSTRUMENT STATE")

  async def get_instrument_checksum(self) -> Optional[str]:
    """Gets the instrument checksum."""
    return await self.send_command("?INSTRUMENT CHECKSUM_ALL")

  async def is_plate_in_instrument(self) -> bool:
    """Checks if a plate is in the instrument."""
    response = await self.send_command("?INSTRUMENT PLATEPOS")
    return response == "PLATEPOS=TAKEN"

  async def set_default_plate_out_position(self, left: bool) -> Optional[str]:
    """Sets the default plate out position."""
    position = "LEFT" if left else "RIGHT"
    return await self.send_command(f"INSTRUMENT PLATEOUT_POSITION={position}")

  async def get_current_default_plate_out_position(self) -> Optional[str]:
    """Gets the current default plate out position."""
    return await self.send_command("?INSTRUMENT PLATEOUT_POSITION")

  async def _get_sensor_range(
    self, option: str, hw_module: Optional[ModuleType] = None, number: Optional[int] = None
  ) -> Optional[str]:
    command = f"#SENSOR {option.upper()}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    response = await self.send_command(command)
    return response

  async def get_all_sensor_groups(
    self, hw_module: Optional[ModuleType] = None, number: Optional[int] = None
  ) -> Optional[str]:
    """Gets all available sensor groups."""
    command = "#SENSOR"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    response = await self.send_command(command)
    return response

  async def get_temperature_sensors(
    self, hw_module: Optional[ModuleType] = None, number: Optional[int] = None
  ) -> Optional[str]:
    """Gets the available temperature sensors."""
    return await self._get_sensor_range("TEMPERATURE", hw_module, number)

  async def get_plate_states(self) -> Optional[str]:
    """Gets the possible plate states."""
    return await self._get_sensor_range("PLATEPOS")

  async def get_plate_transport_positions(self) -> Optional[str]:
    """Gets the possible plate transport positions."""
    return await self._get_sensor_range("LOADPOS")

  async def get_injector_carrier_states(self) -> Optional[str]:
    """Gets the possible injector carrier states."""
    return await self._get_sensor_range("INJECTOR_CARRIER")

  async def get_rotary_encoder_states(self) -> Optional[str]:
    """Gets the possible rotary encoder states."""
    return await self._get_sensor_range("ROTARYENCODER")

  async def get_current_plate_state(self) -> Optional[str]:
    """Gets the current plate state."""
    return await self.send_command("?SENSOR PLATEPOS")

  async def get_current_injector_carrier_state(self) -> Optional[str]:
    """Gets the current injector carrier state."""
    return await self.send_command("?SENSOR INJECTOR_CARRIER")

  async def get_current_temperature(self, device: TemperatureDevice) -> Optional[str]:
    """Gets the current temperature for a specific device."""
    return await self.send_command(f"?TEMPERATURE DEVICE={device.value} CURRENT")

  async def get_current_plate_transport_position(self) -> Optional[str]:
    """Gets the current plate transport position."""
    return await self.send_command("?SENSOR LOADPOS")

  async def get_current_rotary_encoder_state(self) -> Optional[str]:
    """Gets the current rotary encoder state."""
    return await self.send_command("?SENSOR ROTARYENCODER")

  async def get_analog_digital_temperature_value(self, sensor: str) -> Optional[str]:
    """Gets the analog/digital temperature value for a specific sensor."""
    return await self.send_command(f"?SENSORVALUE TEMPERATURE {sensor}")

  async def get_analog_digital_sensor_value(self, sensor: str) -> Optional[str]:
    """Gets the analog/digital value for a specific sensor."""
    return await self.send_command(f"?SENSORVALUE {sensor}")

  async def get_led_state(self) -> Optional[str]:
    """Gets the power LED state."""
    return await self.send_command("?LED DEVICE=POWER STATE")

  async def set_led_state(self, state: str) -> Optional[str]:
    """Sets the power LED state."""
    return await self.send_command(f"LED DEVICE=POWER STATE={state.upper()}")

  async def get_led_color(self) -> Optional[str]:
    """Gets the power LED color."""
    return await self.send_command("?LED DEVICE=POWER COLOUR")

  async def set_led_color(self, color: str) -> Optional[str]:
    """Sets the power LED color."""
    return await self.send_command(f"LED DEVICE=POWER COLOUR={color.upper()}")

  async def get_temperature_devices(self) -> Optional[str]:
    """Gets the available temperature devices."""
    response = await self.send_command("#TEMPERATURE DEVICE")
    return response

  async def get_temperature_parameters(self, device: TemperatureDevice) -> Optional[str]:
    """Gets the parameters for a specific temperature device."""
    response = await self.send_command(f"#TEMPERATURE DEVICE={device.value}")
    return response

  async def get_temperature_target_range(self, device: TemperatureDevice) -> Optional[str]:
    """Gets the target temperature range for a specific device."""
    return await self.send_command(f"#TEMPERATURE DEVICE={device.value} TARGET")

  async def get_temperature_target_modes(self, device: TemperatureDevice) -> Optional[str]:
    """Gets the available target modes for a specific temperature device."""
    response = await self.send_command(f"#TEMPERATURE DEVICE={device.value} TARGET_MODE")
    return response

  async def get_temperature_states(self, device: TemperatureDevice) -> Optional[str]:
    """Gets the available states for a specific temperature device."""
    response = await self.send_command(f"#TEMPERATURE DEVICE={device.value} STATE")
    return response

  async def get_temperature_target(self, device: TemperatureDevice) -> Optional[str]:
    """Gets the current target temperature for a specific device."""
    return await self.send_command(f"?TEMPERATURE DEVICE={device.value} TARGET")

  async def set_temperature_target(self, device: TemperatureDevice, target: float) -> Optional[str]:
    """Sets the target temperature for a specific device."""
    return await self.send_command(f"TEMPERATURE DEVICE={device.value} TARGET={target}")

  async def get_temperature_target_mode(self, device: TemperatureDevice) -> Optional[str]:
    """Gets the current target mode for a specific temperature device."""
    return await self.send_command(f"?TEMPERATURE DEVICE={device.value} TARGET_MODE")

  async def set_temperature_target_mode(
    self, device: TemperatureDevice, target_mode: str
  ) -> Optional[str]:
    """Sets the target mode for a specific temperature device."""
    return await self.send_command(f"TEMPERATURE DEVICE={device.value} TARGET_MODE={target_mode}")

  async def get_temperature_state(self, device: TemperatureDevice) -> Optional[str]:
    """Gets the current state for a specific temperature device."""
    return await self.send_command(f"?TEMPERATURE DEVICE={device.value} STATE")

  async def set_temperature_state(
    self, device: TemperatureDevice, state: TemperatureState
  ) -> Optional[str]:
    """Sets the state for a specific temperature device."""
    return await self.send_command(f"TEMPERATURE DEVICE={device.value} STATE={state.value}")

  async def get_current_chiller_state(self, device: TemperatureDevice) -> Optional[str]:
    """Gets the current chiller state for a specific device."""
    return await self.send_command(f"?TEMPERATURE DEVICE={device.value} CHILLER")

  async def set_chiller_state(
    self, device: TemperatureDevice, state: ChillerState
  ) -> Optional[str]:
    """Sets the chiller state for a specific device."""
    return await self.send_command(f"TEMPERATURE DEVICE={device.value} CHILLER={state.value}")

  async def get_temperature_time_interval_range(self) -> Optional[str]:
    """Gets the range for the temperature message time interval."""
    return await self.send_command(
      f"#MESSAGE TYPE={InstrumentMessageType.TEMPERATURE.value} TIME_INTERVAL"
    )

  async def get_temperature_time_interval(self) -> Optional[str]:
    """Gets the current temperature message time interval."""
    return await self.send_command(
      f"?MESSAGE TYPE={InstrumentMessageType.TEMPERATURE.value} TIME_INTERVAL"
    )

  async def set_temperature_time_interval(self, interval: int) -> Optional[str]:
    """Sets the temperature message time interval."""
    return await self.send_command(
      f"MESSAGE TYPE={InstrumentMessageType.TEMPERATURE.value} TIME_INTERVAL={interval}"
    )
