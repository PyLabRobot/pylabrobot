from pylabrobot.only_fans import FanBackend


class FanChatterboxBackend(FanBackend):
  """ Chatter box backend for device-free testing. Prints out all operations. """

  async def setup(self) -> None:
    print("Setting up the fan.")

  async def turn_on(self, intensity: int) -> None:
    print(f"Turning on the fan at intensity {intensity}.")

  async def turn_off(self) -> None:
    print("Turning off the fan.")

  async def stop(self) -> None:
    print("Stopping the fan.")
