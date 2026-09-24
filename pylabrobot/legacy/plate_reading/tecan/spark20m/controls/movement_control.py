from enum import Enum
from typing import Any, Dict, Optional

from .base_control import BaseControl
from .spark_enums import ModuleType, ShakingMode, ShakingName


class LidLiftState(Enum):
  ON = "ON"
  OFF = "OFF"


class RetractionState(Enum):
  ENABLED = "ENABLED"
  DISABLED = "DISABLED"


class MovementControl(BaseControl):
  """
  This class provides methods for controlling various movement operations on the device.
  It includes functionalities to move motors to absolute or relative positions,
  manage motor configurations, and handle micro-stepping.
  """

  async def move_motors(
    self,
    motor_values: Dict[str, Any],
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Moves motors to absolute positions."""
    command = "ABSOLUTE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    for key, value in motor_values.items():
      command += f" {key}={value}"
    return await self.send_command(command)

  async def move_to_named_position(
    self,
    position: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Moves to a predefined named position."""
    command = "ABSOLUTE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" POSITION={position}"
    return await self.send_command(command)

  async def get_movement_motors(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the list of available motors for movement."""
    command = "#ABSOLUTE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    response = await self.send_command(command)
    return response

  async def get_motor_movement_range(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the movement range for a specific motor."""
    command = "#ABSOLUTE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" {motor}"
    return await self.send_command(command)

  async def get_available_movement_positions(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the list of available predefined movement positions."""
    command = "#ABSOLUTE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += " POSITION"
    response = await self.send_command(command)
    return response

  async def get_current_module_motor_values(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the current motor values for the module."""
    command = "?ABSOLUTE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_current_motor_value(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the current value of a specific motor."""
    command = "?ABSOLUTE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" {motor}"
    return await self.send_command(command)

  async def get_current_movement_position(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the current predefined movement position."""
    command = "?ABSOLUTE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += " POSITION"
    return await self.send_command(command)

  async def _get_movement_config_int(
    self,
    motor: str,
    option: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    command = "?CONFIG MOVEMENT"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" MOTOR={motor} {option}"
    return await self.send_command(command)

  async def _set_movement_config_int(
    self,
    motor: str,
    option: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    command = "CONFIG MOVEMENT"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" MOTOR={motor} {option}={value}"
    return await self.send_command(command)

  async def get_motor_start_frequency(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(
      motor, "STARTFREQUENCY", hw_module, number, subcomponent
    )

  async def set_motor_start_frequency(
    self,
    motor: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._set_movement_config_int(
      motor, "STARTFREQUENCY", value, hw_module, number, subcomponent
    )

  async def get_motor_end_frequency(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(
      motor, "ENDFREQUENCY", hw_module, number, subcomponent
    )

  async def set_motor_end_frequency(
    self,
    motor: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._set_movement_config_int(
      motor, "ENDFREQUENCY", value, hw_module, number, subcomponent
    )

  async def get_motor_ramp_step(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(motor, "RAMPSTEP", hw_module, number, subcomponent)

  async def set_motor_ramp_step(
    self,
    motor: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._set_movement_config_int(
      motor, "RAMPSTEP", value, hw_module, number, subcomponent
    )

  async def get_motor_step_loss(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(motor, "STEPLOSS", hw_module, number, subcomponent)

  async def set_motor_step_loss(
    self,
    motor: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._set_movement_config_int(
      motor, "STEPLOSS", value, hw_module, number, subcomponent
    )

  async def get_motor_micro_stepping(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(
      motor, "MICROSTEPPING", hw_module, number, subcomponent
    )

  async def set_motor_micro_stepping(
    self,
    motor: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._set_movement_config_int(
      motor, "MICROSTEPPING", value, hw_module, number, subcomponent
    )

  async def get_motor_resolution(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(motor, "RESOLUTION", hw_module, number, subcomponent)

  async def set_motor_resolution(
    self,
    motor: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._set_movement_config_int(
      motor, "RESOLUTION", value, hw_module, number, subcomponent
    )

  async def get_motor_operating_current(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(motor, "CURRENT", hw_module, number, subcomponent)

  async def set_motor_operating_current(
    self,
    motor: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._set_movement_config_int(
      motor, "CURRENT", value, hw_module, number, subcomponent
    )

  async def get_motor_standby_current(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(
      motor, "STANDBYCURRENT", hw_module, number, subcomponent
    )

  async def set_motor_standby_current(
    self,
    motor: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._set_movement_config_int(
      motor, "STANDBYCURRENT", value, hw_module, number, subcomponent
    )

  async def get_motor_min_travel(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(motor, "MINPOS", hw_module, number, subcomponent)

  async def set_motor_min_travel(
    self,
    motor: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._set_movement_config_int(
      motor, "MINPOS", value, hw_module, number, subcomponent
    )

  async def get_motor_max_travel(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(motor, "MAXPOS", hw_module, number, subcomponent)

  async def set_motor_max_travel(
    self,
    motor: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._set_movement_config_int(
      motor, "MAXPOS", value, hw_module, number, subcomponent
    )

  async def get_motor_positive_direction(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(
      motor, "POSITIVEDIRECTION", hw_module, number, subcomponent
    )

  async def set_motor_positive_direction(
    self,
    motor: str,
    direction: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    command = "CONFIG MOVEMENT"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" MOTOR={motor} POSITIVEDIRECTION={direction.upper()}"
    return await self.send_command(command)

  async def get_motor_sense_resistor(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(
      motor, "SENSERESISTOR", hw_module, number, subcomponent
    )

  async def set_motor_sense_resistor(
    self,
    motor: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._set_movement_config_int(
      motor, "SENSERESISTOR", value, hw_module, number, subcomponent
    )

  async def get_motor_ramp_scale(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._get_movement_config_int(motor, "RAMPSCALE", hw_module, number, subcomponent)

  async def set_motor_ramp_scale(
    self,
    motor: str,
    value: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    return await self._set_movement_config_int(
      motor, "RAMPSCALE", value, hw_module, number, subcomponent
    )

  async def move_micro_step(
    self,
    relative: bool,
    motor_parameters: Dict[str, Any],
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Moves motors by micro steps, absolute or relative."""
    step_type = "STEPREL" if relative else "STEPABS"
    command = f"{step_type}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    for key, value in motor_parameters.items():
      command += f" {key}={value}"
    return await self.send_command(command)

  async def get_micro_step_motors(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the list of motors supporting micro stepping."""
    command = "#STEPABS"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    response = await self.send_command(command)
    return response

  async def get_absolute_micro_step_motor_range(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the absolute micro step range for a motor."""
    command = "#STEPABS"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" {motor}"
    return await self.send_command(command)

  async def get_current_absolute_micro_step_values(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the current absolute micro step values for all motors."""
    command = "?STEPABS"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_current_absolute_micro_step_value(
    self,
    motor: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the current absolute micro step value for a specific motor."""
    command = "?STEPABS"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    command += f" {motor}"
    return await self.send_command(command)

  async def move_carrier(
    self,
    carrier: str,
    position: str,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
  ) -> Optional[str]:
    """Moves a carrier to a specific position."""
    command = "MOVE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    command += f" CARRIER={carrier} POSITION={position}"
    return await self.send_command(command)

  async def get_position_config_name(self, index: int) -> Optional[str]:
    """Gets the name of the position configuration at the given index."""
    return await self.send_command(f"?CONFIG ABSOLUTE POSITION INDEX={index} NAME")

  async def set_position_config_name(self, index: int, name: str) -> Optional[str]:
    """Sets the name of the position configuration at the given index."""
    return await self.send_command(f"CONFIG ABSOLUTE POSITION INDEX={index} NAME={name}")

  async def get_position_config_x(self, index: int) -> Optional[str]:
    """Gets the X coordinate of the position configuration at the given index."""
    return await self.send_command(f"?CONFIG ABSOLUTE POSITION INDEX={index} X")

  async def set_position_config_x(self, index: int, x: int) -> Optional[str]:
    """Sets the X coordinate of the position configuration at the given index."""
    return await self.send_command(f"CONFIG ABSOLUTE POSITION INDEX={index} X={x}")

  async def get_position_config_y(self, index: int) -> Optional[str]:
    """Gets the Y coordinate of the position configuration at the given index."""
    return await self.send_command(f"?CONFIG ABSOLUTE POSITION INDEX={index} Y")

  async def set_position_config_y(self, index: int, y: int) -> Optional[str]:
    """Sets the Y coordinate of the position configuration at the given index."""
    return await self.send_command(f"CONFIG ABSOLUTE POSITION INDEX={index} Y={y}")

  async def get_position_config_z(self, index: int) -> Optional[str]:
    """Gets the Z coordinate of the position configuration at the given index."""
    return await self.send_command(f"?CONFIG ABSOLUTE POSITION INDEX={index} Z")

  async def set_position_config_z(self, index: int, z: int) -> Optional[str]:
    """Sets the Z coordinate of the position configuration at the given index."""
    return await self.send_command(f"CONFIG ABSOLUTE POSITION INDEX={index} Z={z}")

  async def get_maximum_position_indexes(self) -> Optional[str]:
    """Gets the maximum number of position configuration indexes."""
    return await self.send_command("CONFIG ABSOLUTE POSITION MAXINDEX")

  async def activate_retraction(self, retractable_element: str) -> Optional[str]:
    """Activates the automatic retraction for the given element."""
    return await self.set_retraction_state(retractable_element, RetractionState.ENABLED)

  async def deactivate_retraction(self, retractable_element: str) -> Optional[str]:
    """Deactivates the automatic retraction for the given element."""
    return await self.set_retraction_state(retractable_element, RetractionState.DISABLED)

  async def set_retraction_state(
    self, retractable_element: str, state: RetractionState
  ) -> Optional[str]:
    """Sets the retraction state for the given element."""
    return await self.send_command(f"AUTO_IN {retractable_element}={state.value}")

  async def is_retraction_active(self, retractable_element: str) -> bool:
    """Checks if automatic retraction is active for the given element."""
    response = await self.send_command(f"?AUTO_IN {retractable_element}")
    return response == f"{retractable_element}={RetractionState.ENABLED.value}"

  async def is_retraction_inactive(self, retractable_element: str) -> bool:
    """Checks if automatic retraction is inactive for the given element."""
    response = await self.send_command(f"?AUTO_IN {retractable_element}")
    return response == f"{retractable_element}={RetractionState.DISABLED.value}"

  async def get_lid_lift_states(self) -> Optional[str]:
    """Gets the available lid lift states."""
    response = await self.send_command("#LIDLIFT STATE")
    return response

  async def get_plate_height_range(self) -> Optional[str]:
    """Gets the allowed range for plate height."""
    return await self.send_command("#LIDLIFT PLATEHEIGHT")

  async def set_lid_lifter_state(self, state: LidLiftState, plate_height: int) -> Optional[str]:
    """Sets the lid lifter state and plate height."""
    return await self.send_command(f"LIDLIFT STATE={state.value} PLATEHEIGHT={plate_height}")

  async def get_lid_lifter_current_state(self) -> Optional[str]:
    """Gets the current state of the lid lifter."""
    return await self.send_command("?LIDLIFT STATE")

  async def injector_move_to_position(
    self, plate_height: int, x: Optional[int] = None, y: Optional[int] = None
  ) -> Optional[str]:
    """Moves the injector to the specified position."""
    command = f"ABSOLUTE INJECTOR PLATEHEIGHT={plate_height}"
    if x is not None:
      command += f" X={x}"
    if y is not None:
      command += f" Y={y}"
    return await self.send_command(command)

  async def get_available_shaking_modes(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the available shaking modes."""
    command = "#MODE SHAKING"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_available_shaking_amplitudes(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the available shaking amplitudes."""
    command = "#SHAKING AMPLITUDE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_available_shaking_frequencies(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the available shaking frequencies."""
    command = "#SHAKING FREQUENCY"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_available_shaking_names(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the available shaking names."""
    command = "#SHAKING NAME"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_available_shaking_time_span(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the available shaking time span."""
    command = "#SHAKING TIME"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def start_shaking(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Starts the shaking."""
    command = "SHAKING START"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_current_shaking_name(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the current shaking name."""
    command = "?SHAKING NAME"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_current_shaking_frequency(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the current shaking frequency."""
    command = "?SHAKING FREQUENCY"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_current_shaking_amplitude(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the current shaking amplitude."""
    command = "?SHAKING AMPLITUDE"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_current_shaking_time(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the current shaking time."""
    command = "?SHAKING TIME"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_current_shaking_mode(
    self,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the current shaking mode."""
    command = "?MODE SHAKING"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def set_shaking_mode(
    self,
    mode: ShakingMode,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Sets the shaking mode."""
    command = f"MODE SHAKING={mode.value}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def set_shaking_time(
    self,
    time: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Sets the shaking time."""
    command = f"SHAKING TIME={time}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def set_shaking_by_name(
    self,
    name: ShakingName,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Sets the shaking by name."""
    command = f"SHAKING NAME={name.value}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def set_shaking_amplitude_and_frequency(
    self,
    amplitude: int,
    frequency: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Sets the shaking amplitude and frequency."""
    command = f"SHAKING AMPLITUDE={amplitude} FREQUENCY={frequency}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)
