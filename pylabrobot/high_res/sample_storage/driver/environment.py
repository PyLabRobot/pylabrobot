import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from pylabrobot.events import evented_operation, resource_reference

from .errors import HighResSampleStorageError
from .types import EnvironmentParameter

if TYPE_CHECKING:
  from .driver import HighResSampleStorage

logger = logging.getLogger(__name__)


def _control_event_context(self: "EnvironmentControl") -> Dict[str, Any]:
  return {"device": resource_reference(self._driver), "resources": []}


def _set_temperature_event_context(
  self: "EnvironmentControl", temperature: float
) -> Dict[str, Any]:
  return {
    **_control_event_context(self),
    "target_temperature": float(temperature),
    "passive": False,
  }


def _set_humidity_event_context(self: "EnvironmentControl", humidity: float) -> Dict[str, Any]:
  return {**_control_event_context(self), "target_humidity": float(humidity)}


def _set_co2_event_context(self: "EnvironmentControl", co2: float) -> Dict[str, Any]:
  return {**_control_event_context(self), "target_co2": float(co2)}


def _set_o2_event_context(self: "EnvironmentControl", o2: float) -> Dict[str, Any]:
  return {**_control_event_context(self), "target_o2": float(o2)}


class EnvironmentControl:
  """Temperature, humidity, and gas control for a HighRes sample store.

  Concentrations and relative humidity use fractions in the public API. The
  device protocol uses percentages, so ``0.05`` CO2 is sent as ``5``.
  """

  def __init__(self, driver: "HighResSampleStorage"):
    super().__init__()
    self._driver = driver
    self._parameters: Dict[str, EnvironmentParameter] = {}

  async def refresh(self) -> Dict[str, EnvironmentParameter]:
    """Read and cache all environmental channels reported by the device."""
    self._parameters = await self._driver.request_environment()
    return dict(self._parameters)

  @property
  def parameters(self) -> Dict[str, EnvironmentParameter]:
    """The environmental channels from the most recent read."""
    return dict(self._parameters)

  def _cached_channel_is_controllable(self, channel: str) -> Optional[bool]:
    if not self._parameters:
      return None
    parameter = self._parameters.get(channel)
    return parameter is not None and parameter.setpoint is not None

  async def _request_parameter(self, channel: str) -> EnvironmentParameter:
    parameters = await self.refresh()
    try:
      return parameters[channel]
    except KeyError as exc:
      raise HighResSampleStorageError(
        "environmentstatus", [f"no {channel} channel reported"]
      ) from exc

  async def _request_setpoint(self, channel: str) -> float:
    parameter = await self._request_parameter(channel)
    if parameter.setpoint is None:
      raise HighResSampleStorageError(
        "environmentstatus", [f"{channel} does not report a setpoint"]
      )
    return parameter.setpoint

  async def _require_controllable_channel(self, channel: str) -> EnvironmentParameter:
    parameter = (await self.refresh()).get(channel)
    if parameter is None or parameter.setpoint is None:
      raise NotImplementedError(f"The installed device does not control {channel}.")
    return parameter

  async def _set_percentage(self, channel: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
      raise ValueError(f"{channel} must be between 0 and 1.")
    await self._require_controllable_channel(channel)
    logger.info("Setting %s %s target to %g", self._driver.name, channel, value)
    await self._driver.send_command(f"environmentset {channel} {value * 100:g}")

  async def _set_control_enabled(self, channel: str, enabled: bool) -> None:
    await self._require_controllable_channel(channel)
    action = "enable" if enabled else "disable"
    logger.info("Setting %s %s control to %s", self._driver.name, channel, action)
    await self._driver.send_command(f"environment {action} {channel.lower()}")

  @property
  def supports_active_cooling(self) -> bool:
    return self._driver.model_info.supports_active_cooling

  @property
  def supports_heating(self) -> bool:
    return self._driver.model_info.supports_heating

  @property
  def temperature_range(self) -> Optional[Tuple[float, float]]:
    return self._driver.model_info.temperature_range

  @property
  def humidity_range(self) -> Optional[Tuple[float, float]]:
    return self._driver.model_info.humidity_range

  async def request_current_temperature(self) -> float:
    return (await self._request_parameter("TEMP")).current

  async def request_target_temperature(self) -> float:
    return await self._request_setpoint("TEMP")

  @evented_operation("temperature_controller.set_temperature", _set_temperature_event_context)
  async def set_temperature(self, temperature: float) -> None:
    temperature_range = self.temperature_range
    if temperature_range is not None:
      minimum, maximum = temperature_range
      if not minimum <= temperature <= maximum:
        raise ValueError(
          f"temperature must be between {minimum:g} and {maximum:g} C for {self._driver.model}."
        )
    installed = await self._require_controllable_channel("TEMP")
    if installed.limit is not None and temperature > installed.limit:
      raise ValueError(
        f"temperature must not exceed the installed limit of {installed.limit:g} C "
        f"for {self._driver.model}."
      )
    logger.info("Setting %s temperature target to %g C", self._driver.name, temperature)
    await self._driver.send_command(f"environmentset TEMP {temperature}")

  @evented_operation("temperature_controller.activate", _control_event_context)
  async def start_temperature_control(self) -> None:
    await self._set_control_enabled("TEMP", True)

  @evented_operation("temperature_controller.deactivate", _control_event_context)
  async def stop_temperature_control(self) -> None:
    await self._set_control_enabled("TEMP", False)

  @property
  def supports_humidity_control(self) -> bool:
    installed = self._cached_channel_is_controllable("RH")
    if installed is not None:
      return installed
    return self._driver.model_info.supports_humidity_control

  async def request_current_humidity(self) -> float:
    return (await self._request_parameter("RH")).current / 100.0

  async def request_target_humidity(self) -> float:
    return await self._request_setpoint("RH") / 100.0

  @evented_operation("humidity_controller.set_humidity", _set_humidity_event_context)
  async def set_humidity(self, humidity: float) -> None:
    humidity_range = self.humidity_range
    if humidity_range is not None and not humidity_range[0] <= humidity <= humidity_range[1]:
      raise ValueError(
        f"humidity must be between {humidity_range[0]:g} and {humidity_range[1]:g} "
        f"for {self._driver.model}."
      )
    await self._set_percentage("RH", humidity)

  @evented_operation("humidity_controller.activate", _control_event_context)
  async def start_humidity_control(self) -> None:
    await self._set_control_enabled("RH", True)

  @evented_operation("humidity_controller.deactivate", _control_event_context)
  async def stop_humidity_control(self) -> None:
    await self._set_control_enabled("RH", False)

  @property
  def supports_co2_control(self) -> bool:
    installed = self._cached_channel_is_controllable("CO2")
    if installed is not None:
      return installed
    return self._driver.model_info.supports_co2_control

  async def request_current_co2(self) -> float:
    return (await self._request_parameter("CO2")).current / 100.0

  async def request_target_co2(self) -> float:
    return await self._request_setpoint("CO2") / 100.0

  @evented_operation("co2_controller.set_co2", _set_co2_event_context)
  async def set_co2(self, co2: float) -> None:
    await self._set_percentage("CO2", co2)

  @evented_operation("co2_controller.activate", _control_event_context)
  async def start_co2_control(self) -> None:
    await self._set_control_enabled("CO2", True)

  @evented_operation("co2_controller.deactivate", _control_event_context)
  async def stop_co2_control(self) -> None:
    await self._set_control_enabled("CO2", False)

  @property
  def supports_o2_control(self) -> bool:
    installed = self._cached_channel_is_controllable("O2")
    if installed is not None:
      return installed
    return self._driver.model_info.supports_o2_control

  async def request_current_o2(self) -> float:
    return (await self._request_parameter("O2")).current / 100.0

  async def request_target_o2(self) -> float:
    return await self._request_setpoint("O2") / 100.0

  @evented_operation("o2_controller.set_o2", _set_o2_event_context)
  async def set_o2(self, o2: float) -> None:
    await self._set_percentage("O2", o2)

  @evented_operation("o2_controller.activate", _control_event_context)
  async def start_o2_control(self) -> None:
    await self._set_control_enabled("O2", True)

  @evented_operation("o2_controller.deactivate", _control_event_context)
  async def stop_o2_control(self) -> None:
    await self._set_control_enabled("O2", False)

  async def request_tank_pressures(self) -> Dict[str, float]:
    """Return ``TANK*`` sensor readings in the device-reported pressure unit."""
    parameters = await self.refresh()
    return {
      name: parameter.current for name, parameter in parameters.items() if name.startswith("TANK")
    }
