from pylabrobot.capabilities.capability import Capability, need_capability_ready

from .backend import TilterBackend


class Tilter(Capability):
  """Tilting capability.

  See :doc:`/user_guide/capabilities/tilting` for a walkthrough.
  """

  def __init__(self, backend: TilterBackend):
    super().__init__(backend=backend)
    self.backend: TilterBackend = backend
    self._absolute_angle: float = 0

  @property
  def absolute_angle(self) -> float:
    return self._absolute_angle

  @need_capability_ready
  async def set_angle(self, absolute_angle: float):
    """Set the tilt angle.

    Args:
      absolute_angle: The absolute angle in degrees. 0 is horizontal.
    """
    await self.backend.set_angle(angle=absolute_angle)
    self._absolute_angle = absolute_angle

  @need_capability_ready
  async def tilt(self, relative_angle: float):
    """Tilt by a relative angle from the current position.

    Args:
      relative_angle: The angle to tilt by, in degrees.
    """
    await self.set_angle(self._absolute_angle + relative_angle)

  async def _on_stop(self):
    await super()._on_stop()
