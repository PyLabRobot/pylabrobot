"""Legacy. Use pylabrobot.cole_parmer instead."""

from pylabrobot.cole_parmer import masterflex_backend as _new
from pylabrobot.legacy.pumps.backend import PumpBackend


class MasterflexBackend(PumpBackend):
  """Legacy. Use pylabrobot.cole_parmer.MasterflexBackend instead."""

  def __init__(self, com_port: str):
    self._new = _new.MasterflexBackend(com_port=com_port)

  @property
  def io(self):
    return self._new.io

  @io.setter
  def io(self, value):
    self._new.io = value

  async def setup(self):
    await self._new.setup()

  async def stop(self):
    await self._new.stop()

  def serialize(self):
    return self._new.serialize()

  async def send_command(self, command: str):
    return await self._new.send_command(command)

  async def run_revolutions(self, num_revolutions: float):
    await self._new.run_revolutions(num_revolutions)

  async def run_continuously(self, speed: float):
    await self._new.run_continuously(speed)

  async def halt(self):
    await self._new.halt()


# Deprecated alias
class Masterflex:
  def __init__(self, *args, **kwargs):
    raise RuntimeError("`Masterflex` is deprecated. Please use `MasterflexBackend` instead.")
