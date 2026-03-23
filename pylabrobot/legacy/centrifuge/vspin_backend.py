"""Legacy. Use pylabrobot.agilent.vspin instead."""

import logging
from typing import Optional

from pylabrobot.agilent.vspin import vspin as _new
from pylabrobot.legacy.centrifuge.backend import CentrifugeBackend, LoaderBackend
from pylabrobot.legacy.centrifuge.standard import LoaderNoPlateError

logger = logging.getLogger(__name__)


class Access2Backend(LoaderBackend):
  """Legacy. Use pylabrobot.agilent.vspin.Access2Backend instead."""

  def __init__(self, device_id: str, timeout: int = 60):
    self._new = _new.Access2Backend(device_id=device_id, timeout=timeout)

  @property
  def io(self):
    return self._new.io

  @io.setter
  def io(self, value):
    self._new.io = value

  @property
  def timeout(self):
    return self._new.timeout

  @timeout.setter
  def timeout(self, value):
    self._new.timeout = value

  async def setup(self):
    await self._new.setup()

  async def stop(self):
    await self._new.stop()

  def serialize(self):
    return {"io": self.io.serialize(), "timeout": self.timeout}

  async def send_command(self, command: bytes) -> bytes:
    return await self._new.send_command(command)

  async def get_status(self) -> bytes:
    return await self._new.get_status()

  async def park(self):
    await self._new.park()

  async def close(self):
    await self._new.close()

  async def open(self):
    await self._new.open()

  async def load(self):
    try:
      await self._new.load()
    except RuntimeError as e:
      if "no plate found on stage" in str(e):
        raise LoaderNoPlateError("no plate found on stage") from e
      raise

  async def unload(self):
    try:
      await self._new.unload()
    except RuntimeError as e:
      if "no plate found in centrifuge" in str(e):
        raise LoaderNoPlateError("no plate found in centrifuge") from e
      raise


class VSpinBackend(CentrifugeBackend):
  """Legacy. Use pylabrobot.agilent.vspin.VSpinBackend instead."""

  def __init__(self, device_id: Optional[str] = None):
    self._new = _new.VSpinBackend(device_id=device_id)

  @property
  def io(self):
    return self._new.io

  @io.setter
  def io(self, value):
    self._new.io = value

  @property
  def _bucket_1_remainder(self):
    return self._new._bucket_1_remainder

  @_bucket_1_remainder.setter
  def _bucket_1_remainder(self, value):
    self._new._bucket_1_remainder = value

  @property
  def bucket_1_remainder(self) -> int:
    return self._new.bucket_1_remainder

  async def setup(self):
    await self._new.setup()

  async def stop(self):
    await self._new.stop()

  async def set_bucket_1_position_to_current(self) -> None:
    await self._new.set_bucket_1_position_to_current()

  async def get_bucket_1_position(self) -> int:
    return await self._new.get_bucket_1_position()

  async def get_position(self) -> int:
    return await self._new.get_position()

  async def get_tachometer(self) -> int:
    return await self._new.get_tachometer()

  async def get_home_position(self) -> int:
    return await self._new.get_home_position()

  async def get_bucket_locked(self) -> bool:
    return await self._new.get_bucket_locked()

  async def get_door_open(self) -> bool:
    return await self._new.get_door_open()

  async def get_door_locked(self) -> bool:
    return await self._new.get_door_locked()

  async def open_door(self):
    await self._new.open_door()

  async def close_door(self):
    await self._new.close_door()

  async def lock_door(self):
    await self._new.lock_door()

  async def unlock_door(self):
    await self._new.unlock_door()

  async def lock_bucket(self):
    await self._new.lock_bucket()

  async def unlock_bucket(self):
    await self._new.unlock_bucket()

  async def go_to_bucket1(self):
    await self._new.go_to_bucket1()

  async def go_to_bucket2(self):
    await self._new.go_to_bucket2()

  async def go_to_position(self, position: int):
    await self._new.go_to_position(position)

  @staticmethod
  def g_to_rpm(g: float) -> int:
    return _new.VSpinBackend.g_to_rpm(g)

  async def spin(
    self,
    g: float = 500,
    duration: float = 60,
    acceleration: float = 0.8,
    deceleration: float = 0.8,
  ) -> None:
    await self._new.spin(
      g=g,
      duration=duration,
      backend_params=_new.VSpinBackend.SpinParams(
        acceleration=acceleration, deceleration=deceleration
      ),
    )

  async def configure_and_initialize(self):
    await self._new.configure_and_initialize()


# Deprecated alias
class VSpin:
  def __init__(self, *args, **kwargs):
    raise RuntimeError("`VSpin` is deprecated. Please use `VSpinBackend` instead. ")
