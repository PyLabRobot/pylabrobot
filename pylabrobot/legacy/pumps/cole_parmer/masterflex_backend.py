"""Legacy. Use pylabrobot.cole_parmer instead."""

from pylabrobot.cole_parmer.masterflex_backend import MasterflexBackend as _NewBackend
from pylabrobot.cole_parmer.masterflex_backend import MasterflexDriver
from pylabrobot.legacy.pumps.backend import PumpBackend


class MasterflexBackend(PumpBackend):
  """Legacy. Use pylabrobot.cole_parmer.MasterflexBackend instead."""

  def __init__(self, com_port: str):
    self._driver = MasterflexDriver(com_port=com_port)
    self._backend = _NewBackend(self._driver)

  @property
  def io(self):
    return self._driver.io

  @io.setter
  def io(self, value):
    self._driver.io = value

  async def setup(self):
    await self._driver.setup()

  async def stop(self):
    await self._driver.stop()

  def serialize(self):
    return {"type": self.__class__.__name__, "com_port": self._driver.com_port}

  async def send_command(self, command: str):
    return await self._driver.send_command(command)

  async def run_revolutions(self, num_revolutions: float):
    await self._backend.run_revolutions(num_revolutions)

  async def run_continuously(self, speed: float):
    await self._backend.run_continuously(speed)

  async def halt(self):
    await self._backend.halt()


# Deprecated alias
class Masterflex:
  def __init__(self, *args, **kwargs):
    raise RuntimeError("`Masterflex` is deprecated. Please use `MasterflexBackend` instead.")
