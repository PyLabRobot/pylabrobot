import asyncio
import time
from typing import Any, Optional

from pylabrobot.events import evented_operation, resource_reference
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.resources import Coordinate, ResourceHolder

from .backend import TemperatureControllerBackend


def _temperature_controller_event_context(
  temperature_controller: "TemperatureController",
) -> dict[str, Any]:
  """Describe a thermal device and its directly loaded resource, when present."""
  context: dict[str, Any] = {"device": resource_reference(temperature_controller)}
  if temperature_controller.resource is not None:
    context["resources"] = [resource_reference(temperature_controller.resource)]
  if temperature_controller.target_temperature is not None:
    context["target_temperature"] = float(temperature_controller.target_temperature)
  return context


def _set_temperature_event_context(
  temperature_controller: "TemperatureController",
  temperature: float,
  passive: Optional[bool] = None,
) -> dict[str, Any]:
  context = _temperature_controller_event_context(temperature_controller)
  context["target_temperature"] = float(temperature)
  if passive is not None:
    context["passive"] = passive
  return context


def _wait_for_temperature_event_context(
  temperature_controller: "TemperatureController",
  timeout: Optional[float] = None,
  tolerance: Optional[float] = None,
) -> dict[str, Any]:
  context = _temperature_controller_event_context(temperature_controller)
  if timeout is not None:
    context["timeout"] = float(timeout)
  if tolerance is not None:
    context["tolerance"] = float(tolerance)
  return context


def _hold_temperature_event_context(
  temperature_controller: "TemperatureController",
  duration: float,
) -> dict[str, Any]:
  context = _temperature_controller_event_context(temperature_controller)
  context["duration"] = float(duration)
  return context


class TemperatureController(ResourceHolder, Machine):
  """Temperature controller, for heating or for cooling."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: TemperatureControllerBackend,
    child_location: Coordinate,
    category: str = "temperature_controller",
    model: Optional[str] = None,
  ):
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      child_location=child_location,
      category=category,
      model=model,
    )
    Machine.__init__(self, backend=backend)
    self.backend: TemperatureControllerBackend = backend  # fix type
    self.target_temperature: Optional[float] = None

  @evented_operation("temperature_controller.set_temperature", _set_temperature_event_context)
  async def set_temperature(self, temperature: float, passive: bool = False):
    """Set the temperature of the temperature controller.

    Args:
      temperature: Temperature in Celsius.
      passive: If ``True`` and cooling is required, allow the device to cool
        down naturally without calling ``set_temperature`` on the backend.
        This can be used for backends that do not support active cooling or to
        explicitly disable active cooling when it is available.
    """
    current = await self.backend.get_current_temperature()

    self.target_temperature = temperature

    if temperature < current:
      if passive:  # if passive, we do nothing and return early.
        return

      # If we have to cool but the backend does not support active cooling,
      # and we are not passive cooling, raise an error.
      if not self.backend.supports_active_cooling:
        raise ValueError(
          "Backend does not support active cooling. Use passive=True to allow "
          "passive cooling or set a higher temperature."
        )

    return await self.backend.set_temperature(temperature)

  async def get_temperature(self) -> float:
    """Get the current temperature of the temperature controller in Celsius."""
    return await self.backend.get_current_temperature()

  @evented_operation(
    "temperature_controller.wait_for_temperature", _wait_for_temperature_event_context
  )
  async def wait_for_temperature(self, timeout: float = 300.0, tolerance: float = 0.5) -> None:
    """Wait for the temperature to reach the target temperature. The target temperature must be
    set by `set_temperature()`.

    Args:
      timeout: Timeout in seconds.
      tolerance: Tolerance in Celsius.
    """
    if self.target_temperature is None:
      raise RuntimeError("Target temperature is not set.")
    start = time.time()
    while time.time() - start < timeout:
      temperature = await self.get_temperature()
      if abs(temperature - self.target_temperature) < tolerance:
        return
      await asyncio.sleep(1.0)
    raise TimeoutError(f"Temperature did not reach target temperature within {timeout} seconds.")

  @evented_operation("temperature_controller.hold_temperature", _hold_temperature_event_context)
  async def hold_temperature(self, duration: float) -> None:
    """Hold the currently configured thermal condition for a requested dwell.

    This operation intentionally does not issue a new hardware temperature command or verify
    that a loaded resource reached the configured target. It records the protocol-requested
    dwell while the controller remains in its existing state.

    Args:
      duration: Dwell duration in seconds. Zero is allowed for callers that conditionally
        elide a dwell; negative values are invalid.
    """
    if duration < 0:
      raise ValueError("Temperature hold duration must not be negative.")
    await asyncio.sleep(duration)

  @evented_operation("temperature_controller.deactivate", _temperature_controller_event_context)
  async def deactivate(self):
    """Deactivate the temperature controller. This will stop the heating or cooling, and return
    the temperature to ambient temperature. The target temperature will be reset to `None`.
    """
    self.target_temperature = None
    return await self.backend.deactivate()

  async def stop(self):
    """Stop the temperature controller and close the backend connection."""
    await self.deactivate()
    await super().stop()

  def serialize(self) -> dict:
    return {
      **Machine.serialize(self),
      **ResourceHolder.serialize(self),
    }
