from .backend import PumpBackend


class PumpChatterboxBackend(PumpBackend):
  """Chatterbox backend for device-free testing."""

  async def setup(self):
    print("Setting up the pump.")

  async def stop(self):
    print("Stopping the pump.")

  async def run_revolutions(self, num_revolutions: float):
    print(f"Running {num_revolutions} revolutions.")

  async def run_continuously(self, speed: float):
    print(f"Running continuously at speed {speed}.")

  async def halt(self):
    print("Halting the pump.")
