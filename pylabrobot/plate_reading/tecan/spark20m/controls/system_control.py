from typing import Optional

from .base_control import BaseControl
from .spark_enums import ModuleType, SimulationState


class SystemControl(BaseControl):
  async def get_status(self):
    return await self.send_command("?INSTRUMENT STATE")

  async def terminate(self):
    """Terminates the connection to the device."""
    return await self.send_command("TERMINATE")

  async def get_version(
    self, hw_module: Optional[ModuleType] = None, number=None, subcomponent=None
  ):
    """Gets the version details."""
    command = "?VERSION"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def get_alias(self):
    """Gets the instrument alias."""
    return await self.send_command("?ALIAS NAME")

  async def set_alias(self, alias):
    """Sets the instrument alias."""
    return await self.send_command(f"ALIAS NAME={alias}")

  async def reset(self, hw_module: Optional[ModuleType] = None, number=None, subcomponent=None):
    """Resets the specified module or the entire instrument."""
    command = "RESET"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def reset_with_reset_all(self, hw_module: ModuleType, number=None, subcomponent=None):
    """Resets the specified module with the ALL option."""
    command = "RESET ALL"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def reset_to_bootloader(
    self, hw_module: Optional[ModuleType] = None, number=None, subcomponent=None
  ):
    """Resets the specified module to the bootloader."""
    command = "RESET TO_BOOTLOADER"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def reset_all(self):
    """Resets all modules."""
    return await self.send_command("RESET ALL")

  async def get_available_debug_levels(self):
    """Gets the available debug levels."""
    return await self.send_command("#DEBUG LEVEL")

  async def set_debug_level(self, level):
    """Sets the debug level."""
    return await self.send_command(f"DEBUG LEVEL={level}")

  async def get_current_debug_level(self):
    """Gets the current debug level."""
    return await self.send_command("?DEBUG LEVEL")

  async def is_simulation_active(self):
    """Checks if simulation mode is active."""
    response = await self.send_command("?SIMULATE STATE")
    return response == f"STATE={SimulationState.ON.value}"

  async def set_simulation_state(self, state: SimulationState):
    """Sets the simulation state."""
    return await self.send_command(f"SIMULATE STATE={state.value}")

  async def turn_simulate_on(self):
    """Turns simulation mode on."""
    return await self.set_simulation_state(SimulationState.ON)

  async def turn_simulate_off(self):
    """Turns simulation mode off."""
    return await self.set_simulation_state(SimulationState.OFF)

  async def define_simulation_data_generation_pattern(self, measurement_mode, data_pattern):
    """Defines the data generation pattern for simulation mode."""
    return await self.send_command(
      f"SIMULATION MEASMODE={measurement_mode} GENERATE_DATA={data_pattern}"
    )

  async def _get_time_range(self, option):
    return await self.send_command(f"#TIME {option.upper()}")

  async def _set_time(self, option, value, label=None):
    label_str = f" LABEL={label}" if label is not None else ""
    return await self.send_command(f"TIME{label_str} {option.upper()}={value}")

  async def _get_time(self, option, label=None):
    label_str = f" LABEL={label}" if label is not None else ""
    return await self.send_command(f"?TIME{label_str} {option.upper()}")

  async def get_settle_time_range(self):
    """Gets the range for settle time."""
    return await self._get_time_range("SETTLE")

  async def set_settle_time(self, value):
    """Sets the settle time."""
    return await self._set_time("SETTLE", value)

  async def get_current_settle_time(self):
    """Gets the current settle time."""
    return await self._get_time("SETTLE")

  async def get_lag_time_range(self):
    """Gets the range for lag time."""
    return await self._get_time_range("LAG")

  async def set_lag_time(self, value, label=None):
    """Sets the lag time."""
    return await self._set_time("LAG", value, label)

  async def get_current_lag_time(self, label=None):
    """Gets the current lag time."""
    return await self._get_time("LAG", label)

  async def get_integration_time_range(self):
    """Gets the range for integration time."""
    return await self._get_time_range("INTEGRATION")

  async def set_integration_time(self, value, label=None):
    """Sets the integration time."""
    return await self._set_time("INTEGRATION", value, label)

  async def get_current_integration_time(self, label=None):
    """Gets the current integration time."""
    return await self._get_time("INTEGRATION", label)

  async def get_excitation_time_range(self):
    """Gets the range for excitation time."""
    return await self._get_time_range("EXCITATION")

  async def set_excitation_time(self, value, label=None):
    """Sets the excitation time."""
    return await self._set_time("EXCITATION", value, label)

  async def get_current_excitation_time(self, label=None):
    """Gets the current excitation time."""
    return await self._get_time("EXCITATION", label)

  async def get_dead_time(self):
    """Gets the dead time."""
    return await self.send_command("?TIME DEADTIME")
