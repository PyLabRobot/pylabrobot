from typing import TYPE_CHECKING, Dict, List, Union

from pylabrobot.agrowpumps import AgrowPumpArray as NativeAgrowPumpArray
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.pumps.backend import PumpArrayBackend

if TYPE_CHECKING:
  from pymodbus.client import AsyncModbusSerialClient  # type: ignore


class AgrowPumpArrayBackend(PumpArrayBackend):
  """Compatibility backend delegating Agrow ownership to the native driver."""

  def __init__(self, port: str, address: Union[int, str]):
    super().__init__()
    if not isinstance(port, str):
      raise ValueError("Port must be a string")
    self.port = port
    if address not in range(0, 256):
      raise ValueError("Pump address out of range")
    self.address = int(address)
    self._driver = self._create_driver(port=port, address=self.address)

  def _create_driver(self, port: str, address: int) -> NativeAgrowPumpArray:
    """Create the sole native lifecycle owner for this compatibility backend."""
    return NativeAgrowPumpArray(port=port, address=address)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Enter the native owner's lifespan for this compatibility lifespan."""
    await super()._enter_lifespan(stack)
    await stack.enter_async_context(self._driver.lifespan())

  @property
  def modbus(self) -> "AsyncModbusSerialClient":
    """Return the native owner's Modbus connection."""
    return self._driver.modbus

  @property
  def pump_index_to_address(self) -> Dict[int, int]:
    """Return the native owner's pump-index register mapping."""
    return self._driver.pump_index_to_address

  @property
  def num_channels(self) -> int:
    """Return the number of channels discovered by the native owner."""
    return self._driver.num_channels

  def serialize(self):
    return {
      **super().serialize(),
      "port": self.port,
      "address": self.address,
    }

  async def run_revolutions(self, num_revolutions: List[float], use_channels: List[int]):
    """Reject revolution-based commands, which this pump array does not support."""
    raise NotImplementedError(
      "Revolution based pumping commands are not available for this pump array."
    )

  async def run_continuously(self, speed: List[float], use_channels: List[int]):
    """Run selected channels at integer-truncated speeds in request order."""
    for pump_index, pump_speed in zip(use_channels, speed):
      await self._driver.write_speed(pump_index, int(pump_speed))

  async def halt(self):
    """Halt every pump in mapping insertion order."""
    for pump_index in self.pump_index_to_address:
      await self._driver.write_speed(pump_index, 0)


# Deprecated alias with warning # TODO: remove mid May 2025 (giving people 1 month to update)
# https://github.com/PyLabRobot/pylabrobot/issues/466


class AgrowPumpArray:
  def __init__(self, *args, **kwargs):
    raise RuntimeError(
      "`AgrowPumpArray` is deprecated. Please use `AgrowPumpArrayBackend` instead."
    )
