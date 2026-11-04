import anyio

from pylabrobot.legacy.machines.machine import Machine

from .backend import FanBackend

# Bounds dispatching the turn-off command that closes a timed run and waiting for that
# command to complete. It is not a budget for any device operation, and it is deliberately
# independent of the requested run duration: a long run must not buy its shutdown a long
# shield.
_FAN_TURN_OFF_TIMEOUT = 30.0


class Fan(Machine):
  """
  Front end for Fans.
  """

  def __init__(self, backend: FanBackend):
    super().__init__(backend=backend)
    self.backend: FanBackend = backend  # fix type

  async def _pre_backend_stop(self) -> None:
    await self.backend.turn_off()

  async def turn_on(self, intensity: int, duration=None):
    """Run the fan

    Args:
      intensity: integer percent between 0 and 100
      duration: time to run the fan for. If None, run until `turn_off` is called.
    """

    await self.backend.turn_on(intensity=intensity)

    if duration is not None:
      try:
        await anyio.sleep(duration)
      finally:
        # A timed run owns the matching shutdown, so cancellation waits for it, bounded
        # by the fixed cleanup budget above.
        with anyio.CancelScope(shield=True):
          with anyio.move_on_after(_FAN_TURN_OFF_TIMEOUT) as turn_off_scope:
            await self.backend.turn_off()
          if turn_off_scope.cancelled_caught:
            raise TimeoutError(
              f"Fan turn-off was not confirmed within {_FAN_TURN_OFF_TIMEOUT} seconds; "
              "the fan may still be running."
            )

  async def turn_off(self):
    """Turn the fan off, but do not close the connection."""
    await self.backend.turn_off()
