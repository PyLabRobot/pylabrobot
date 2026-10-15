from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.centrifuge.backend import CentrifugeBackend, LoaderBackend


class CentrifugeChatterboxBackend(CentrifugeBackend):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    print("Setting up")
    stack.push_shielded_async_callback(self._print_stop_message)

  async def _print_stop_message(self) -> None:
    """Print this chatterbox's native lifespan cleanup message."""
    print("Stopping")

  async def open_door(self):
    print("Opening door")

  async def close_door(self):
    print("Closing door")

  async def lock_door(self):
    print("Locking door")

  async def unlock_door(self):
    print("Unlocking door")

  async def go_to_bucket1(self):
    print("Going to bucket 1")

  async def go_to_bucket2(self):
    print("Going to bucket 2")

  async def rotate_distance(self, distance):
    print(f"Rotating distance: {distance}")

  async def lock_bucket(self):
    print("Locking bucket")

  async def unlock_bucket(self):
    print("Unlocking bucket")

  async def spin(self, g: float, duration: float, acceleration: float):
    print(f"Starting spin cycle with g: {g}, duration: {duration}, acceleration: {acceleration}")


class LoaderChatterboxBackend(LoaderBackend):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    print("Setting up")
    stack.push_shielded_async_callback(self._print_stop_message)

  async def _print_stop_message(self) -> None:
    """Print this chatterbox's native lifespan cleanup message."""
    print("Stopping")

  async def load(self):
    print("Loading")

  async def unload(self):
    print("Unloading")
