from pylabrobot.capabilities.capability import Capability

from .backend import HumidityControllerBackend


class HumidityControlCapability(Capability):
  """Humidity control capability."""

  def __init__(self, backend: HumidityControllerBackend):
    super().__init__(backend=backend)
    self.backend: HumidityControllerBackend = backend

  async def set_humidity(self, humidity: float):
    """Set the target humidity as a fraction 0.0-1.0.

    Raises:
      ValueError: If the backend does not support humidity control.
    """
    if not self.backend.supports_humidity_control:
      raise ValueError("Backend does not support humidity control (read-only).")
    await self.backend.set_humidity(humidity)

  async def request_humidity(self) -> float:
    """Get the current humidity as a fraction 0.0-1.0."""
    return await self.backend.request_current_humidity()

  async def _on_stop(self):
    await super()._on_stop()
