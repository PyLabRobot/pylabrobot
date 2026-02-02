from enum import Enum
from typing import Optional

from .base_control import BaseControl
from .spark_enums import (
  FilterType,
  FluorescenceCarrier,
  MirrorType,
  ModuleType,
)


class MirrorCarrier(Enum):
  MIRROR1 = "MIRROR1"


class LaserPowerState(Enum):
  ON = "ON"
  OFF = "OFF"


class LightingState(Enum):
  ON = "ON"
  OFF = "OFF"


class OpticsControl(BaseControl):
  async def get_beam_diameter_list(self) -> Optional[str]:
    """Gets the list of possible beam diameters."""
    return await self.send_command("#BEAM DIAMETER")

  async def set_beam_diameter(self, value: int) -> Optional[str]:
    """Sets the beam diameter."""
    return await self.send_command(f"BEAM DIAMETER={value}")

  async def get_current_beam_diameter(self) -> Optional[str]:
    """Gets the current beam diameter."""
    return await self.send_command("?BEAM DIAMETER")

  async def get_emission_carrier_list(self) -> Optional[str]:
    """Gets the list of emission carriers."""
    return await self.send_command("#EMISSION CARRIER")

  async def set_emission_carrier(self, carrier: FluorescenceCarrier) -> Optional[str]:
    """Sets the emission carrier."""
    return await self.send_command(f"EMISSION CARRIER={carrier.value}")

  async def get_emission_filter_type_list(
    self, carrier_name: Optional[FluorescenceCarrier] = None
  ) -> Optional[str]:
    """Gets the list of emission filter types."""
    command = "#EMISSION TYPE"
    if carrier_name:
      command = f"#EMISSION CARRIER={carrier_name.value} TYPE"
    response = await self.send_command(command)
    return response

  async def get_emission_filter_wavelength_list(
    self,
    carrier_name: Optional[FluorescenceCarrier] = None,
    module: Optional[ModuleType] = None,
    sub_module: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the list of emission filter wavelengths."""
    command = "#EMISSION WAVELENGTH"
    if carrier_name:
      command = f"#EMISSION CARRIER={carrier_name.value} WAVELENGTH"
    if module:
      command += f" MODULE={module.value}"
    if sub_module:
      command += f" SUB={sub_module}"
    return await self.send_command(command)

  async def get_emission_filter_bandwidth_list(
    self, carrier_name: Optional[FluorescenceCarrier] = None
  ) -> Optional[str]:
    """Gets the list of emission filter bandwidths."""
    command = "#EMISSION BANDWIDTH"
    if carrier_name:
      command = f"#EMISSION CARRIER={carrier_name.value} BANDWIDTH"
    return await self.send_command(command)

  async def get_emission_filter_attenuation_list(
    self, carrier_name: Optional[FluorescenceCarrier] = None
  ) -> Optional[str]:
    """Gets the list of emission filter attenuations."""
    command = "#EMISSION ATTENUATION"
    if carrier_name:
      command = f"#EMISSION CARRIER={carrier_name.value} ATTENUATION"
    return await self.send_command(command)

  async def get_current_emission_filter(
    self, label: Optional[int] = None, carrier: Optional[FluorescenceCarrier] = None
  ) -> Optional[str]:
    """Gets the current emission filter."""
    command = "?EMISSION"
    if carrier:
      command += f" CARRIER={carrier.value}"
    if label is not None:
      command += f" LABEL={label}"
    return await self.send_command(command)

  async def get_emission_filter_descriptions(self, carrier: FluorescenceCarrier) -> Optional[str]:
    """Gets the emission filter descriptions."""
    return await self.send_command(f"#EMISSION CARRIER={carrier.value} DESCRIPTION")

  async def get_emission_flash_counters(self, carrier: FluorescenceCarrier) -> Optional[str]:
    """Gets the emission flash counters."""
    return await self.send_command(f"#EMISSION CARRIER={carrier.value} FLASH_COUNTER")

  async def get_emission_filter_slide_usage(
    self, carrier_name: FluorescenceCarrier
  ) -> Optional[str]:
    """Gets the emission filter slide usage."""
    return await self.send_command(f"?EMISSION CARRIER={carrier_name.value} SLIDE_USAGE")

  async def get_emission_filter_slide_description(
    self, carrier_name: FluorescenceCarrier
  ) -> Optional[str]:
    """Gets the emission filter slide description."""
    return await self.send_command(f"?EMISSION CARRIER={carrier_name.value} SLIDE_DESCRIPTION")

  async def set_emission_filter(
    self,
    filter_type: FilterType,
    wavelength: Optional[int] = None,
    bandwidth: Optional[int] = None,
    attenuation: Optional[int] = None,
    label: Optional[int] = None,
    carrier: Optional[FluorescenceCarrier] = None,
  ) -> Optional[str]:
    """Sets the emission filter."""
    command = "EMISSION"
    if carrier:
      command += f" CARRIER={carrier.value}"
    if filter_type:
      command += f" TYPE={filter_type.value}"
    if wavelength is not None:
      command += f" WAVELENGTH={wavelength}"
    if bandwidth is not None:
      command += f" BANDWIDTH={bandwidth}"
    if attenuation is not None:
      command += f" ATTENUATION={attenuation}"
    if label is not None:
      command += f" LABEL={label}"
    return await self.send_command(command)

  async def get_emission_empty_position_wavelength_limit_list(self) -> Optional[str]:
    """Gets the empty position wavelength limit list for emission."""
    return await self.send_command("#EMISSION WAVELENGTH TYPE=EMPTY")

  async def get_empty_position_wavelength_limit_lower(
    self, hw_module: Optional[ModuleType] = None, number: Optional[int] = None
  ) -> Optional[str]:
    """Gets the lower wavelength limit for the empty position in the emission filter."""
    command = "?CONFIG FILTER=EMISSION TYPE=EMPTY"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    return await self.send_command(command)

  async def get_empty_position_wavelength_limit_upper(
    self, hw_module: Optional[ModuleType] = None, number: Optional[int] = None
  ) -> Optional[str]:
    """Gets the upper wavelength limit for the empty position in the emission filter."""
    command = "?CONFIG FILTER=EMISSION TYPE=EMPTY"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    return await self.send_command(command)

  async def set_empty_position_wavelength_limits(
    self,
    lower: int,
    upper: int,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
  ) -> Optional[str]:
    """Sets the wavelength limits for the empty position in the emission filter."""
    command = f"CONFIG FILTER=EMISSION TYPE=EMPTY LOWERWAVELENGTH={lower} UPPERWAVELENGTH={upper}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    return await self.send_command(command)

  async def get_excitation_carrier_list(self) -> Optional[str]:
    """Gets the list of excitation carriers."""
    return await self.send_command("#EXCITATION CARRIER")

  async def set_excitation_carrier(self, carrier: FluorescenceCarrier) -> Optional[str]:
    """Sets the excitation carrier."""
    return await self.send_command(f"EXCITATION CARRIER={carrier.value}")

  async def get_excitation_filter_type_list(
    self, carrier_name: Optional[FluorescenceCarrier] = None
  ) -> Optional[str]:
    """Gets the list of excitation filter types."""
    command = "#EXCITATION TYPE"
    if carrier_name:
      command = f"#EXCITATION CARRIER={carrier_name.value} TYPE"
    response = await self.send_command(command)
    return response

  async def get_excitation_filter_wavelength_list(
    self,
    carrier_name: Optional[FluorescenceCarrier] = None,
    module: Optional[ModuleType] = None,
    sub_module: Optional[str] = None,
  ) -> Optional[str]:
    """Gets the list of excitation filter wavelengths."""
    command = "#EXCITATION WAVELENGTH"
    if carrier_name:
      command = f"#EXCITATION CARRIER={carrier_name.value} WAVELENGTH"
    if module:
      command += f" MODULE={module.value}"
    if sub_module:
      command += f" SUB={sub_module}"
    return await self.send_command(command)

  async def get_excitation_filter_bandwidth_list(
    self, carrier_name: Optional[FluorescenceCarrier] = None
  ) -> Optional[str]:
    """Gets the list of excitation filter bandwidths."""
    command = "#EXCITATION BANDWIDTH"
    if carrier_name:
      command = f"#EXCITATION CARRIER={carrier_name.value} BANDWIDTH"
    return await self.send_command(command)

  async def get_excitation_filter_attenuation_list(
    self, carrier_name: Optional[FluorescenceCarrier] = None
  ) -> Optional[str]:
    """Gets the list of excitation filter attenuations."""
    command = "#EXCITATION ATTENUATION"
    if carrier_name:
      command = f"#EXCITATION CARRIER={carrier_name.value} ATTENUATION"
    return await self.send_command(command)

  async def get_current_excitation_filter(
    self, label: Optional[int] = None, carrier: Optional[FluorescenceCarrier] = None
  ) -> Optional[str]:
    """Gets the current excitation filter."""
    command = "?EXCITATION"
    if carrier:
      command += f" CARRIER={carrier.value}"
    if label is not None:
      command += f" LABEL={label}"
    return await self.send_command(command)

  async def get_excitation_filter_descriptions(self, carrier: FluorescenceCarrier) -> Optional[str]:
    """Gets the excitation filter descriptions."""
    return await self.send_command(f"#EXCITATION CARRIER={carrier.value} DESCRIPTION")

  async def get_excitation_flash_counters(self, carrier: FluorescenceCarrier) -> Optional[str]:
    """Gets the excitation flash counters."""
    return await self.send_command(f"#EXCITATION CARRIER={carrier.value} FLASH_COUNTER")

  async def get_excitation_filter_slide_usage(
    self, carrier_name: FluorescenceCarrier
  ) -> Optional[str]:
    """Gets the excitation filter slide usage."""
    return await self.send_command(f"?EXCITATION CARRIER={carrier_name.value} SLIDE_USAGE")

  async def get_excitation_filter_slide_description(
    self, carrier_name: FluorescenceCarrier
  ) -> Optional[str]:
    """Gets the excitation filter slide description."""
    return await self.send_command(f"?EXCITATION CARRIER={carrier_name.value} SLIDE_DESCRIPTION")

  async def set_excitation_filter(
    self,
    filter_type: FilterType,
    wavelength: Optional[int] = None,
    bandwidth: Optional[int] = None,
    attenuation: Optional[int] = None,
    label: Optional[int] = None,
    carrier: Optional[FluorescenceCarrier] = None,
  ) -> Optional[str]:
    """Sets the excitation filter."""
    command = "EXCITATION"
    if carrier:
      command += f" CARRIER={carrier.value}"
    if filter_type:
      command += f" TYPE={filter_type.value}"
    if wavelength is not None:
      command += f" WAVELENGTH={wavelength}"
    if bandwidth is not None:
      command += f" BANDWIDTH={bandwidth}"
    if attenuation is not None:
      command += f" ATTENUATION={attenuation}"
    if label is not None:
      command += f" LABEL={label}"
    return await self.send_command(command)

  async def get_excitation_empty_position_wavelength_limit_list(self) -> Optional[str]:
    """Gets the empty position wavelength limit list for excitation."""
    return await self.send_command("#EXCITATION WAVELENGTH TYPE=EMPTY")

  async def define_filter_read(self, name: str) -> Optional[str]:
    """Reads the filter definition."""
    return await self.send_command(f"DEFINE FILTER READ NAME={name}")

  async def define_filter_write(self, name: str) -> Optional[str]:
    """Writes the filter definition."""
    return await self.send_command(f"DEFINE FILTER WRITE NAME={name}")

  async def get_defined_filter_wavelength(self, name: str, position: int) -> Optional[str]:
    """Gets the wavelength of a defined filter at a specific position."""
    return await self.send_command(f"?DEFINE FILTER NAME={name} POSITION={position} WAVELENGTH")

  async def set_defined_filter_wavelength(
    self, name: str, position: int, wavelength: int
  ) -> Optional[str]:
    """Sets the wavelength of a defined filter at a specific position."""
    return await self.send_command(
      f"DEFINE FILTER NAME={name} POSITION={position} WAVELENGTH={wavelength}"
    )

  async def get_defined_filter_bandwidth(self, name: str, position: int) -> Optional[str]:
    """Gets the bandwidth of a defined filter at a specific position."""
    return await self.send_command(f"?DEFINE FILTER NAME={name} POSITION={position} BANDWIDTH")

  async def set_defined_filter_bandwidth(
    self, name: str, position: int, bandwidth: int
  ) -> Optional[str]:
    """Sets the bandwidth of a defined filter at a specific position."""
    return await self.send_command(
      f"DEFINE FILTER NAME={name} POSITION={position} BANDWIDTH={bandwidth}"
    )

  async def get_defined_filter_type(self, name: str, position: int) -> Optional[str]:
    """Gets the type of a defined filter at a specific position."""
    return await self.send_command(f"?DEFINE FILTER NAME={name} POSITION={position} TYPE")

  async def set_defined_filter_type(
    self, name: str, position: int, filter_type: str
  ) -> Optional[str]:
    """Sets the type of a defined filter at a specific position."""
    return await self.send_command(
      f"DEFINE FILTER NAME={name} POSITION={position} TYPE={filter_type}"
    )

  async def get_defined_filter_flash_counter(self, name: str, position: int) -> Optional[str]:
    """Gets the flash counter of a defined filter at a specific position."""
    return await self.send_command(f"?DEFINE FILTER NAME={name} POSITION={position} FLASH_COUNTER")

  async def set_defined_filter_flash_counter(
    self, name: str, position: int, flash_counter: int
  ) -> Optional[str]:
    """Sets the flash counter of a defined filter at a specific position."""
    return await self.send_command(
      f"DEFINE FILTER NAME={name} POSITION={position} FLASH_COUNTER={flash_counter}"
    )

  async def get_defined_filter_name(self, name: str, position: int) -> Optional[str]:
    """Gets the name/description of a defined filter at a specific position."""
    return await self.send_command(f"?DEFINE FILTER NAME={name} POSITION={position} DESCRIPTION")

  async def set_defined_filter_name(
    self, name: str, position: int, filter_name: str
  ) -> Optional[str]:
    """Sets the name/description of a defined filter at a specific position."""
    return await self.send_command(
      f"DEFINE FILTER NAME={name} POSITION={position} DESCRIPTION={filter_name}"
    )

  async def get_defined_filter_slide_description(self, name: str) -> Optional[str]:
    """Gets the slide description of a defined filter."""
    return await self.send_command(f"?DEFINE FILTER NAME={name} SLIDE_DESCRIPTION")

  async def set_defined_filter_slide_description(
    self, name: str, description: str
  ) -> Optional[str]:
    """Sets the slide description of a defined filter."""
    return await self.send_command(f"DEFINE FILTER NAME={name} SLIDE_DESCRIPTION={description}")

  async def get_defined_filter_slide_usage(self, name: str) -> Optional[str]:
    """Gets the slide usage of a defined filter."""
    return await self.send_command(f"?DEFINE FILTER NAME={name} SLIDE_USAGE")

  async def set_defined_filter_slide_usage(self, name: str, usage: str) -> Optional[str]:
    """Sets the slide usage of a defined filter."""
    return await self.send_command(f"DEFINE FILTER NAME={name} SLIDE_USAGE={usage}")

  async def get_allowed_signal_gain_range(self) -> Optional[str]:
    """Gets the allowed signal gain range."""
    return await self.send_command("#GAIN SIGNAL")

  async def set_signal_gain(
    self,
    gain: int,
    label: Optional[int] = None,
    wavelength: Optional[int] = None,
    channel: Optional[int] = None,
  ) -> Optional[str]:
    """Sets the signal gain."""
    command = "GAIN"
    if label is not None:
      command += f" LABEL={label}"
    command += f" SIGNAL={gain}"
    if wavelength is not None:
      command += f" WAVELENGTH={wavelength}"
    if channel is not None:
      command += f" CHANNEL={channel}"
    return await self.send_command(command)

  async def get_current_signal_gain(self, label: Optional[int] = None) -> Optional[str]:
    """Gets the current signal gain."""
    command = "?GAIN"
    if label is not None:
      command += f" LABEL={label}"
    command += " SIGNAL"
    return await self.send_command(command)

  async def get_allowed_reference_gain_range(self) -> Optional[str]:
    """Gets the allowed reference gain range."""
    return await self.send_command("#GAIN REFERENCE")

  async def set_reference_gain(
    self,
    gain: int,
    label: Optional[int] = None,
    wavelength: Optional[int] = None,
    carrier: Optional[FluorescenceCarrier] = None,
  ) -> Optional[str]:
    """Sets the reference gain."""
    command = "GAIN"
    if label is not None:
      command += f" LABEL={label}"
    command += f" REFERENCE={gain}"
    if wavelength is not None:
      command += f" WAVELENGTH={wavelength}"
    if carrier:
      command += f" CARRIER={carrier.value}"
    return await self.send_command(command)

  async def get_current_reference_gain(self, label: Optional[int] = None) -> Optional[str]:
    """Gets the current reference gain."""
    command = "?GAIN"
    if label is not None:
      command += f" LABEL={label}"
    command += " REFERENCE"
    return await self.send_command(command)

  async def set_laser_power(
    self,
    state: LaserPowerState,
    hw_module: Optional[ModuleType] = None,
    number: Optional[int] = None,
    subcomponent: Optional[str] = None,
  ) -> Optional[str]:
    """Sets the laser power state."""
    command = f"LASER POWER={state.value}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if number is not None:
      command += f" NUMBER={number}"
    if subcomponent:
      command += f" SUB={subcomponent}"
    return await self.send_command(command)

  async def set_lighting_state(self, light_type: str, state: LightingState) -> Optional[str]:
    """Sets the state of the specified lighting type."""
    return await self.send_command(f"LIGHTING TYPE={light_type.upper()} STATE={state.value}")

  async def set_lighting_intensity(self, light_type: str, intensity: int) -> Optional[str]:
    """Sets the intensity of the specified lighting type."""
    return await self.send_command(f"LIGHTING TYPE={light_type.upper()} INTENSITY={intensity}")

  async def get_lighting_current_state(self, light_type: str) -> Optional[str]:
    """Gets the current state of the specified lighting type."""
    return await self.send_command(f"?LIGHTING TYPE={light_type.upper()} STATE")

  async def get_lighting_current_intensity(self, light_type: str) -> Optional[str]:
    """Gets the current intensity of the specified lighting type."""
    return await self.send_command(f"?LIGHTING TYPE={light_type.upper()} INTENSITY")

  async def get_lighting_states(self) -> Optional[str]:
    """Gets the available lighting states."""
    response = await self.send_command("#LIGHTING STATE")
    return response

  async def get_lighting_types(self) -> Optional[str]:
    """Gets the available lighting types."""
    response = await self.send_command("#LIGHTING TYPE")
    return response

  async def get_lighting_intensity_range(self) -> Optional[str]:
    """Gets the allowed lighting intensity range."""
    return await self.send_command("#LIGHTING INTENSITY")

  async def get_current_lighting_details_fim(self) -> Optional[str]:
    """Gets the current lighting details for FIM."""
    return await self.send_command("#LIGHTING")

  async def select_lighting_fim(self, name: str, light_type: str) -> Optional[str]:
    """Selects the lighting for FIM."""
    return await self.send_command(f"LIGHTING TYPE={light_type.upper()} NAME={name.upper()}")

  async def set_intensity_fim(
    self,
    name: str,
    light_type: str,
    intensity: int,
    hw_module: Optional[ModuleType] = None,
    module_number: Optional[int] = None,
  ) -> Optional[str]:
    """Sets the intensity for FIM lighting."""
    command = f"LIGHTING TYPE={light_type.upper()} NAME={name.upper()} INTENSITY={intensity}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if module_number is not None:
      command += f" NUMBER={module_number}"
    return await self.send_command(command)

  async def set_state_fim(self, name: str, light_type: str, is_on: LightingState) -> Optional[str]:
    """Sets the state for FIM lighting."""
    return await self.send_command(
      f"LIGHTING TYPE={light_type.upper()} NAME={name.upper()} STATE={is_on.value}"
    )

  async def get_current_lighting_values_fim(self) -> Optional[str]:
    """Gets the current lighting values for FIM."""
    return await self.send_command("?LIGHTING")

  async def try_get_current_lighting_values_fim(self) -> Optional[str]:
    """Tries to get the current lighting values for FIM."""
    return await self.send_command("?LIGHTING")

  async def get_lighting_values_fim(self, name: str, light_type: str) -> Optional[str]:
    """Gets the lighting values for a specific FIM lighting."""
    return await self.send_command(f"?LIGHTING TYPE={light_type.upper()} NAME={name.upper()}")

  async def get_lighting_details_fim(self, name: str, light_type: str) -> Optional[str]:
    """Gets the lighting details for a specific FIM lighting intensity."""
    return await self.send_command(
      f"#LIGHTING TYPE={light_type.upper()} NAME={name.upper()} INTENSITY"
    )

  async def get_lighting_types_fim(self) -> Optional[str]:
    """Gets the available lighting types for FIM."""
    response = await self.send_command("#LIGHTING TYPE")
    return response

  async def get_lighting_names_fim(self) -> Optional[str]:
    """Gets the available lighting names for FIM."""
    response = await self.send_command("#LIGHTING NAME")
    return response

  async def get_lighting_intensity_ranges_fim(self) -> Optional[str]:
    """Gets the lighting intensity ranges for FIM."""
    return await self.send_command("#LIGHTING INTENSITY")

  async def set_mirror(
    self,
    mirror_type: MirrorType,
    mirror_name: Optional[str] = None,
    carrier: Optional[MirrorCarrier] = None,
    label: Optional[int] = None,
  ) -> Optional[str]:
    """Sets the mirror type and name."""
    command = f"MIRROR TYPE={mirror_type.value}"
    if mirror_name:
      command += f" NAME={mirror_name}"
    if carrier:
      command += f" CARRIER={carrier.value}"
    if label is not None:
      command += f" LABEL={label}"
    return await self.send_command(command)

  async def get_current_mirror(self, label: Optional[int] = None) -> Optional[str]:
    """Gets the current mirror settings."""
    command = "?MIRROR"
    if label is not None:
      command += f" LABEL={label}"
    return await self.send_command(command)

  async def get_mirror(self, carrier: MirrorCarrier, label: Optional[int] = None) -> Optional[str]:
    """Gets the mirror settings for a specific carrier."""
    command = f"?MIRROR CARRIER={carrier.value}"
    if label is not None:
      command += f" LABEL={label}"
    return await self.send_command(command)

  async def get_mirror_types(self, carrier: Optional[MirrorCarrier] = None) -> Optional[str]:
    """Gets the available mirror types."""
    command = "#MIRROR"
    if carrier:
      command += f" CARRIER={carrier.value}"
    command += " TYPE"
    response = await self.send_command(command)
    return response

  async def get_mirror_names(self, carrier: Optional[MirrorCarrier] = None) -> Optional[str]:
    """Gets the available mirror names."""
    command = "#MIRROR"
    if carrier:
      command += f" CARRIER={carrier.value}"
    command += " NAME"
    response = await self.send_command(command)
    return response

  async def _get_mirror_wavelengths(
    self, option: Optional[str] = None, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    command = "#MIRROR"
    if carrier:
      command += f" CARRIER={carrier.value}"
    if option:
      command += f" {option}"
    response = await self.send_command(command)
    return response

  async def get_mirror_start_ex_wavelengths(
    self, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    """Gets the mirror start excitation wavelengths."""
    return await self._get_mirror_wavelengths("EXCITATION_START", carrier)

  async def get_mirror_end_ex_wavelengths(
    self, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    """Gets the mirror end excitation wavelengths."""
    return await self._get_mirror_wavelengths("EXCITATION_END", carrier)

  async def get_mirror_start_em_wavelengths(
    self, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    """Gets the mirror start emission wavelengths."""
    return await self._get_mirror_wavelengths("EMISSION_START", carrier)

  async def get_mirror_end_em_wavelengths(
    self, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    """Gets the mirror end emission wavelengths."""
    return await self._get_mirror_wavelengths("EMISSION_END", carrier)

  async def get_mirror_measurement_modes(
    self, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    """Gets the mirror measurement modes."""
    command = "#MIRROR"
    if carrier:
      command += f" CARRIER={carrier.value}"
    command += " MEAS_MODE"
    return await self.send_command(command)

  async def get_mirror_carrier(self) -> Optional[str]:
    """Gets the mirror carrier."""
    response = await self.send_command("#MIRROR CARRIER")
    return response

  def _create_mirror_settings_target(self) -> str:
    return f" MODULE={ModuleType.FLUORESCENCE.value}"

  async def define_mirror_name(
    self, name: str, position: int, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    """Defines the name for a mirror at a specific position."""
    command = "DEFINE MIRROR"
    if carrier:
      command += f" CARRIER={carrier.value}"
    command += f" POSITION={position} NAME={name} {self._create_mirror_settings_target()}"
    return await self.send_command(command)

  async def define_mirror_type(
    self, mirror_type: MirrorType, position: int, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    """Defines the type for a mirror at a specific position."""
    command = "DEFINE MIRROR"
    if carrier:
      command += f" CARRIER={carrier.value}"
    command += (
      f" POSITION={position} TYPE={mirror_type.value}{self._create_mirror_settings_target()}"
    )
    return await self.send_command(command)

  async def define_mirror_excitation_start(
    self, ex_start: int, position: int, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    """Defines the excitation start wavelength for a mirror."""
    command = "DEFINE MIRROR"
    if carrier:
      command += f" CARRIER={carrier.value}"
    command += (
      f" POSITION={position} EXCITATION_START={ex_start}{self._create_mirror_settings_target()}"
    )
    return await self.send_command(command)

  async def define_mirror_excitation_end(
    self, ex_end: int, position: int, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    """Defines the excitation end wavelength for a mirror."""
    command = "DEFINE MIRROR"
    if carrier:
      command += f" CARRIER={carrier.value}"
    command += (
      f" POSITION={position} EXCITATION_END={ex_end}{self._create_mirror_settings_target()}"
    )
    return await self.send_command(command)

  async def define_mirror_emission_start(
    self, em_start: int, position: int, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    """Defines the emission start wavelength for a mirror."""
    command = "DEFINE MIRROR"
    if carrier:
      command += f" CARRIER={carrier.value}"
    command += (
      f" POSITION={position} EMISSION_START={em_start}{self._create_mirror_settings_target()}"
    )
    return await self.send_command(command)

  async def define_mirror_emission_end(
    self, em_end: int, position: int, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    """Defines the emission end wavelength for a mirror."""
    command = "DEFINE MIRROR"
    if carrier:
      command += f" CARRIER={carrier.value}"
    command += f" POSITION={position} EMISSION_END={em_end}{self._create_mirror_settings_target()}"
    return await self.send_command(command)

  async def get_definable_mirror_positions(
    self, carrier: Optional[MirrorCarrier] = None
  ) -> Optional[str]:
    """Gets the definable mirror positions."""
    command = "#DEFINE MIRROR"
    if carrier:
      command += f" CARRIER={carrier.value}"
    command += f" POSITION{self._create_mirror_settings_target()}"
    response = await self.send_command(command)
    return response

  async def set_objective(self, objective_type: str) -> Optional[str]:
    """Sets the objective type."""
    return await self.send_command(f"OBJECTIVE TYPE={objective_type.upper()}")

  async def get_current_objective(self) -> Optional[str]:
    """Gets the current objective type."""
    return await self.send_command("?OBJECTIVE TYPE")

  async def get_objective_types(self) -> Optional[str]:
    """Gets the list of available objective types."""
    response = await self.send_command("#OBJECTIVE TYPE")
    return response

  async def get_objective_details(
    self,
    objective_type: str,
    hw_module: Optional[ModuleType] = None,
    module_number: Optional[int] = None,
  ) -> Optional[str]:
    """Gets the details for a specific objective type."""
    command = f"?OBJECTIVE TYPE={objective_type.upper()}"
    if hw_module:
      command += f" MODULE={hw_module.value}"
    if module_number is not None:
      command += f" NUMBER={module_number}"
    return await self.send_command(command)

  async def get_mtp_allowed_area(self, objective_type: str, mtp_area_type: str) -> Optional[str]:
    """Gets the allowed MTP area for the given objective and area type."""
    return await self.send_command(
      f"#OBJECTIVE TYPE={objective_type.upper()} RANGE={mtp_area_type.upper()}"
    )
