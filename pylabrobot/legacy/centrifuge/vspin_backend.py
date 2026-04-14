"""Legacy. Use pylabrobot.agilent.vspin instead."""

import logging
from typing import Optional

from pylabrobot.agilent.vspin import vspin as _new
from pylabrobot.legacy.centrifuge.backend import CentrifugeBackend, LoaderBackend
from pylabrobot.legacy.centrifuge.standard import LoaderNoPlateError

logger = logging.getLogger(__name__)


class Access2Backend(LoaderBackend):
  """Legacy. Use pylabrobot.agilent.vspin.Access2Driver instead."""

  def __init__(self, device_id: str, timeout: int = 60):
    self.driver = _new.Access2Driver(device_id=device_id, timeout=timeout)

  @property
  def io(self):
    return self.driver.io

  @io.setter
  def io(self, value):
    self.driver.io = value

  @property
  def timeout(self):
    return self.driver.timeout

  @timeout.setter
  def timeout(self, value):
    self.driver.timeout = value

  async def setup(self):
    await self.driver.setup()

  async def stop(self):
    await self.driver.stop()

  def serialize(self):
    return {"io": self.io.serialize(), "timeout": self.timeout}

  async def send_command(self, command: bytes) -> bytes:
    return await self.driver.send_command(command)

  async def get_status(self) -> bytes:
    return await self.driver.request_status()

  async def park(self):
    await self.driver.park()

  async def close(self):
    await self.driver.close()

  async def open(self):
    await self.driver.open()

  async def load(self):
    try:
      await self.driver.load()
    except RuntimeError as e:
      if "no plate found on stage" in str(e):
        raise LoaderNoPlateError("no plate found on stage") from e
      raise

  async def unload(self):
    try:
      await self.driver.unload()
    except RuntimeError as e:
      if "no plate found in centrifuge" in str(e):
        raise LoaderNoPlateError("no plate found in centrifuge") from e
      raise


class VSpinBackend(CentrifugeBackend):
  """Legacy. Use pylabrobot.agilent.vspin.VSpinDriver instead."""

  def __init__(self, device_id: Optional[str] = None):
    self.driver = _new.VSpinDriver(device_id=device_id)
    self._centrifuge = _new.VSpinCentrifugeBackend(self.driver)

  @property
  def io(self):
    return self.driver.io

  @io.setter
  def io(self, value):
    self.driver.io = value

  @property
  def _bucket_1_remainder(self):
    return self._centrifuge._bucket_1_remainder

  @_bucket_1_remainder.setter
  def _bucket_1_remainder(self, value):
    self._centrifuge._bucket_1_remainder = value

  @property
  def bucket_1_remainder(self) -> int:
    return self._centrifuge.bucket_1_remainder

  async def setup(self):
    await self.driver.setup()
    await self._centrifuge._on_setup()

  async def stop(self):
    await self._centrifuge._on_stop()
    await self.driver.stop()

  async def set_bucket_1_position_to_current(self) -> None:
    await self._centrifuge.set_bucket_1_position_to_current()

  async def get_bucket_1_position(self) -> int:
    return await self._centrifuge.request_bucket_1_position()

  async def get_position(self) -> int:
    return await self.driver.request_position()

  async def get_tachometer(self) -> int:
    return await self.driver.request_tachometer()

  async def get_home_position(self) -> int:
    return await self.driver.request_home_position()

  async def get_bucket_locked(self) -> bool:
    return await self.driver.request_bucket_locked()

  async def get_door_open(self) -> bool:
    return await self.driver.request_door_open()

  async def get_door_locked(self) -> bool:
    return await self.driver.request_door_locked()

  async def open_door(self):
    await self._centrifuge.open_door()

  async def close_door(self):
    await self._centrifuge.close_door()

  async def lock_door(self):
    await self._centrifuge.lock_door()

  async def unlock_door(self):
    await self._centrifuge.unlock_door()

  async def lock_bucket(self):
    await self._centrifuge.lock_bucket()

  async def unlock_bucket(self):
    await self._centrifuge.unlock_bucket()

  async def go_to_bucket1(self):
    await self._centrifuge.go_to_bucket1()

  async def go_to_bucket2(self):
    await self._centrifuge.go_to_bucket2()

  async def go_to_position(self, position: int):
    await self._centrifuge.go_to_position(position)

  @staticmethod
  def g_to_rpm(g: float) -> int:
    return _new.VSpinCentrifugeBackend.g_to_rpm(g)

  async def spin(
    self,
    g: float = 500,
    duration: float = 60,
    acceleration: float = 0.8,
    deceleration: float = 0.8,
  ) -> None:
    await self._centrifuge.spin(
      g=g,
      duration=duration,
      backend_params=_new.VSpinCentrifugeBackend.SpinParams(
        acceleration=acceleration, deceleration=deceleration
      ),
    )

  async def configure_and_initialize(self):
    await self.driver.configure_and_initialize()


# Deprecated alias
class VSpin:
  def __init__(self, *args, **kwargs):
    raise RuntimeError("`VSpin` is deprecated. Please use `VSpinBackend` instead. ")
