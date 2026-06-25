from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.storage.stacker_backend import StackerBackend


class StackerChatterboxBackend(StackerBackend):
  """A no-op :class:`StackerBackend` that prints each operation; for tests and demos."""

  async def setup(self):
    print("Setting up stacker backend")

  async def stop(self):
    print("Stopping stacker backend")

  async def downstack(self, stack: ResourceStack, **backend_kwargs):
    print(f"Downstacking accessible plate from stack '{stack.name}'")

  async def upstack(self, stack: ResourceStack, plate: Plate, **backend_kwargs):
    print(f"Upstacking plate '{plate.name}' onto stack '{stack.name}'")
