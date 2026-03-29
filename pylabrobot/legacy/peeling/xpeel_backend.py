"""Legacy. Use pylabrobot.azenta.XPeelDriver and XPeelPeelerBackend instead."""

from pylabrobot.azenta.xpeel import XPeelDriver, XPeelPeelerBackend
from pylabrobot.legacy.peeling.backend import PeelerBackend


class XPeelBackend(PeelerBackend):
  """Legacy. Use pylabrobot.azenta.XPeelDriver and XPeelPeelerBackend instead."""

  def __init__(self, port: str, timeout=None):
    self._driver = XPeelDriver(port=port, timeout=timeout)
    self._peeler = XPeelPeelerBackend(self._driver)

  async def setup(self):
    await self._driver.setup()
    await self._peeler._on_setup()

  async def stop(self):
    await self._peeler._on_stop()
    await self._driver.stop()

  def serialize(self) -> dict:
    return self._driver.serialize()

  async def peel(self, **kwargs):
    params = XPeelPeelerBackend.PeelParams(**kwargs) if kwargs else None
    return await self._peeler.peel(backend_params=params)

  async def restart(self):
    return await self._peeler.restart()

  async def reset(self):
    return await self._driver.reset()

  async def get_status(self):
    return await self._driver.request_status()

  async def get_version(self):
    return await self._driver.request_version()

  async def seal_check(self):
    return await self._driver.seal_check()

  async def get_tape_remaining(self):
    return await self._driver.request_tape_remaining()

  async def enable_plate_check(self, enabled=True):
    return await self._driver.enable_plate_check(enabled=enabled)

  async def get_seal_sensor_status(self):
    return await self._driver.request_seal_sensor_status()

  async def set_seal_threshold_upper(self, value: int):
    return await self._driver.set_seal_threshold_upper(value=value)

  async def set_seal_threshold_lower(self, value: int):
    return await self._driver.set_seal_threshold_lower(value=value)

  async def move_conveyor_out(self):
    return await self._driver.move_conveyor_out()

  async def move_conveyor_in(self):
    return await self._driver.move_conveyor_in()

  async def move_elevator_down(self):
    return await self._driver.move_elevator_down()

  async def move_elevator_up(self):
    return await self._driver.move_elevator_up()

  async def advance_tape(self):
    return await self._driver.advance_tape()
