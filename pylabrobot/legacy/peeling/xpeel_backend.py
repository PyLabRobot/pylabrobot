"""Legacy. Use pylabrobot.azenta.XPeelBackend instead."""

from pylabrobot.azenta import xpeel
from pylabrobot.legacy.peeling.backend import PeelerBackend


class XPeelBackend(PeelerBackend):
  """Legacy. Use pylabrobot.azenta.XPeelBackend instead."""

  def __init__(self, port: str, timeout=None):
    self._new = xpeel.XPeelBackend(port=port, timeout=timeout)

  async def setup(self):
    await self._new.setup()

  async def stop(self):
    await self._new.stop()

  def serialize(self) -> dict:
    return self._new.serialize()

  async def peel(self, **kwargs):
    params = xpeel.XPeelBackend.PeelParams(**kwargs) if kwargs else None
    return await self._new.peel(backend_params=params)

  async def restart(self):
    return await self._new.restart()

  async def reset(self):
    return await self._new.reset()

  async def get_status(self):
    return await self._new.get_status()

  async def get_version(self):
    return await self._new.get_version()

  async def seal_check(self):
    return await self._new.seal_check()

  async def get_tape_remaining(self):
    return await self._new.get_tape_remaining()

  async def enable_plate_check(self, enabled=True):
    return await self._new.enable_plate_check(enabled=enabled)

  async def get_seal_sensor_status(self):
    return await self._new.get_seal_sensor_status()

  async def set_seal_threshold_upper(self, value: int):
    return await self._new.set_seal_threshold_upper(value=value)

  async def set_seal_threshold_lower(self, value: int):
    return await self._new.set_seal_threshold_lower(value=value)

  async def move_conveyor_out(self):
    return await self._new.move_conveyor_out()

  async def move_conveyor_in(self):
    return await self._new.move_conveyor_in()

  async def move_elevator_down(self):
    return await self._new.move_elevator_down()

  async def move_elevator_up(self):
    return await self._new.move_elevator_up()

  async def advance_tape(self):
    return await self._new.advance_tape()
