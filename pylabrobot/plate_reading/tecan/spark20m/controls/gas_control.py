from typing import Optional

from .base_control import BaseControl
from .spark_enums import GasOption, GasPowerState


class GasControl(BaseControl):
  async def get_gas_options(self) -> Optional[str]:
    """Gets the available gas options."""
    response = await self.send_command("#GASCONTROL GAS")
    return response

  async def get_current_gas_concentration(self, gas_option: GasOption) -> Optional[str]:
    """Gets the current gas concentration for the given option."""
    return await self.send_command(f"?GASCONTROL GAS={gas_option.value} ACTUAL_CONCENTRATION")

  async def get_gas_target_range(self, gas_option: GasOption) -> Optional[str]:
    """Gets the target concentration range for the given gas option."""
    return await self.send_command(f"#GASCONTROL GAS={gas_option.value} RATED_CONCENTRATION")

  async def get_gas_modes(self) -> Optional[str]:
    """Gets the available gas control modes."""
    response = await self.send_command("#GASCONTROL MODE")
    return response

  async def get_gas_mode(self, gas_option: GasOption) -> Optional[str]:
    """Gets the current gas control mode for the given option."""
    return await self.send_command(f"?GASCONTROL GAS={gas_option.value} MODE")

  async def set_gas_mode(self, gas_option: GasOption, mode: str) -> Optional[str]:
    """Sets the gas control mode for the given option."""
    return await self.send_command(f"GASCONTROL GAS={gas_option.value} MODE={mode}")

  async def get_gas_states(self) -> Optional[str]:
    """Gets the available gas states."""
    response = await self.send_command("#GASCONTROL STATUS")
    return response

  async def get_gas_state(self, gas_option: GasOption) -> Optional[str]:
    """Gets the current gas state for the given option."""
    return await self.send_command(f"?GASCONTROL GAS={gas_option.value} STATUS")

  async def get_gas_target_concentration(self, gas_option: GasOption) -> Optional[str]:
    """Gets the target gas concentration for the given option."""
    return await self.send_command(f"?GASCONTROL GAS={gas_option.value} RATED_CONCENTRATION")

  async def set_gas_target_concentration(self, gas_option: GasOption, target: int) -> Optional[str]:
    """Sets the target gas concentration for the given option."""
    return await self.send_command(
      f"GASCONTROL GAS={gas_option.value} RATED_CONCENTRATION={target}"
    )

  async def set_gas_sensor_power(self, state: GasPowerState) -> Optional[str]:
    """Sets the gas sensor power state (True for ON, False for OFF)."""
    return await self.send_command(f"GASCONTROL POWER={state.value}")

  async def acknowledge_audio_gas_warning(self) -> Optional[str]:
    """Acknowledges the audio gas warning, turning off the buzzer."""
    return await self.send_command("GASCONTROL BUZZER=OFF")

  async def get_altitude(self) -> Optional[str]:
    """Gets the current altitude setting for gas control."""
    return await self.send_command("?GASCONTROL ALTITUDE")

  async def set_altitude(self, altitude: int) -> Optional[str]:
    """Sets the altitude for gas control."""
    return await self.send_command(f"GASCONTROL ALTITUDE={altitude}")

  async def get_altitude_range(self) -> Optional[str]:
    """Gets the allowed altitude range for gas control."""
    return await self.send_command("#GASCONTROL ALTITUDE")
