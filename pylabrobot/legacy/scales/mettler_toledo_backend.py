"""Legacy. Use pylabrobot.mettler_toledo.MettlerToledoWXS205SDUBackend instead."""

import warnings
from typing import List, Literal, Optional, Union

from pylabrobot.legacy.scales.scale_backend import ScaleBackend
from pylabrobot.mettler_toledo import mettler_toledo as mt

MettlerToledoError = mt.MettlerToledoError
MettlerToledoResponse = List[str]


class MettlerToledoWXS205SDUBackend(ScaleBackend):
  """Legacy. Use pylabrobot.mettler_toledo.MettlerToledoWXS205SDUBackend instead."""

  def __init__(self, port: Optional[str] = None, vid: int = 0x0403, pid: int = 0x6001):
    self._new = mt.MettlerToledoWXS205SDUBackend(port=port, vid=vid, pid=pid)

  async def setup(self) -> None:
    await self._new.setup()

  async def stop(self) -> None:
    await self._new.stop()

  def serialize(self) -> dict:
    return self._new.serialize()

  async def zero(self, timeout: Union[Literal["stable"], float, int] = "stable"):
    return await self._new.zero(timeout=timeout)

  async def tare(self, timeout: Union[Literal["stable"], float, int] = "stable"):
    return await self._new.tare(timeout=timeout)

  async def read_weight(self, timeout: Union[Literal["stable"], float, int] = "stable") -> float:
    return await self._new.read_weight(timeout=timeout)

  async def get_weight(self, timeout: Union[Literal["stable"], float, int] = "stable") -> float:
    warnings.warn(
      "get_weight() is deprecated. Use read_weight() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self._new.read_weight(timeout=timeout)

  async def send_command(self, command: str, timeout: int = 60):
    return await self._new.send_command(command=command, timeout=timeout)

  async def request_serial_number(self) -> str:
    return await self._new.request_serial_number()

  async def request_tare_weight(self) -> float:
    return await self._new.request_tare_weight()

  async def read_stable_weight(self) -> float:
    return await self._new.read_stable_weight()

  async def read_dynamic_weight(self, timeout: float) -> float:
    return await self._new.read_dynamic_weight(timeout=timeout)

  async def read_weight_value_immediately(self) -> float:
    return await self._new.read_weight_value_immediately()

  async def set_display_text(self, text: str):
    return await self._new.set_display_text(text=text)

  async def set_weight_display(self):
    return await self._new.set_weight_display()

  # Deprecated aliases

  async def get_serial_number(self) -> str:
    warnings.warn(
      "get_serial_number() is deprecated. Use request_serial_number() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self._new.request_serial_number()

  async def get_tare_weight(self) -> float:
    warnings.warn(
      "get_tare_weight() is deprecated. Use request_tare_weight() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self._new.request_tare_weight()

  async def get_stable_weight(self) -> float:
    warnings.warn(
      "get_stable_weight() is deprecated. Use read_stable_weight() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self._new.read_stable_weight()

  async def get_dynamic_weight(self, timeout: float) -> float:
    warnings.warn(
      "get_dynamic_weight() is deprecated. Use read_dynamic_weight() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self._new.read_dynamic_weight(timeout=timeout)

  async def get_weight_value_immediately(self) -> float:
    warnings.warn(
      "get_weight_value_immediately() is deprecated. Use read_weight_value_immediately() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self._new.read_weight_value_immediately()


class MettlerToledoWXS205SDU:
  def __init__(self, *args, **kwargs):
    raise RuntimeError(
      "`MettlerToledoWXS205SDU` is deprecated. Please use `MettlerToledoWXS205SDUBackend` instead."
    )
