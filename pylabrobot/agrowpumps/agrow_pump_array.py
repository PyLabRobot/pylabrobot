import logging
from typing import Dict, List, Optional, Union

import anyio
import sniffio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan

try:
  from pymodbus.client import AsyncModbusSerialClient  # type: ignore

  _MODBUS_IMPORT_ERROR = None
except ImportError as e:
  AsyncModbusSerialClient = None  # type: ignore
  _MODBUS_IMPORT_ERROR = e

logger = logging.getLogger(__name__)


class AgrowPumpArray(HasLifespan):
  """Agrow dose pump array.

  Each channel is exposed as an :class:`AgrowChannel` on ``self.channels`` once
  :meth:`setup` has queried the number of channels from the hardware.
  """

  def __init__(self, port: str, address: Union[int, str]):
    super().__init__()
    if not isinstance(port, str):
      raise ValueError("Port must be a string")
    self.port = port
    if address not in range(0, 256):
      raise ValueError("Pump address out of range")
    self.address = int(address)
    self._pump_index_to_address: Optional[Dict[int, int]] = None
    self._modbus: Optional["AsyncModbusSerialClient"] = None
    self._num_channels: Optional[int] = None
    self._task_group: Optional[anyio.abc.TaskGroup] = None
    self._keep_alive_interval: float = 25
    self.channels: List["AgrowPump"] = []

  @property
  def modbus(self) -> "AsyncModbusSerialClient":
    if self._modbus is None:
      raise RuntimeError("Modbus connection not established")
    return self._modbus

  @property
  def pump_index_to_address(self) -> Dict[int, int]:
    if self._pump_index_to_address is None:
      raise RuntimeError("Pump mappings not established")
    return self._pump_index_to_address

  @property
  def num_channels(self) -> int:
    if self._num_channels is None:
      raise RuntimeError("Number of channels not established")
    return self._num_channels

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Connect, discover pumps, and own the keep-alive worker for one lifespan."""
    await super()._enter_lifespan(stack)
    backend = sniffio.current_async_library()
    if backend != "asyncio":
      raise RuntimeError(
        "AgrowPumpArray requires the asyncio backend because pymodbus' asynchronous serial "
        f"client is asyncio-only (running under {backend!r})"
      )
    if AsyncModbusSerialClient is None:
      raise RuntimeError(
        "pymodbus is not installed. Install with: pip install pylabrobot[modbus]."
        f" Import error: {_MODBUS_IMPORT_ERROR}"
      )

    stack.callback(self._clear_run_state)
    self._modbus = AsyncModbusSerialClient(
      port=self.port,
      baudrate=115200,
      timeout=1,
      stopbits=1,
      bytesize=8,
      parity="E",
      retry_on_empty=True,  # type: ignore[call-arg]
    )
    stack.callback(self._close_modbus)
    await self.modbus.connect()
    if not self.modbus.connected:
      logger.error("[Agrow %s] modbus connection failed", self.port)
      raise ConnectionError("Modbus connection failed during pump setup")

    register_return = await self.modbus.read_holding_registers(19, 2, unit=self.address)  # type: ignore[call-arg, misc]
    self._num_channels = int(
      "".join(chr(r // 256) + chr(r % 256) for r in register_return.registers)[2]
    )
    self._pump_index_to_address = {pump: pump + 100 for pump in range(0, self.num_channels)}
    self.channels = [AgrowPump(self, ch) for ch in range(self.num_channels)]
    stack.push_shielded_async_callback(self._zero_all_pumps)

    task_group = await stack.enter_task_group()
    self._task_group = task_group
    stack.callback(self._stop_keep_alive)
    task_group.start_soon(self._keep_alive)
    logger.info(
      "[Agrow %s addr=%s] connected: channels=%d", self.port, self.address, self._num_channels
    )

  async def _keep_alive(self) -> None:
    """Keep the Modbus connection active until the owning task group stops."""
    while True:
      await anyio.sleep(self._keep_alive_interval)
      await self.modbus.read_holding_registers(0, 1, unit=self.address)  # type: ignore[call-arg, misc]

  def _stop_keep_alive(self) -> None:
    """Cancel the keep-alive worker before its task-group context is joined."""
    if self._task_group is not None:
      self._task_group.cancel_scope.cancel()

  async def _zero_all_pumps(self) -> None:
    """Set every discovered pump speed to zero before closing Modbus."""
    logger.info("[Agrow %s addr=%s] stopping", self.port, self.address)
    for pump in self.pump_index_to_address:
      await self.write_speed(pump, 0)

  def _close_modbus(self) -> None:
    """Close the Modbus client acquired for the current lifespan."""
    self.modbus.close()
    assert not self.modbus.connected, "Modbus failing to disconnect"

  def _clear_run_state(self) -> None:
    """Clear client and discovered-channel state after one lifespan."""
    self._task_group = None
    self._modbus = None
    self._pump_index_to_address = None
    self._num_channels = None
    self.channels = []

  async def write_speed(self, channel: int, speed: int):
    if speed not in range(101):
      raise ValueError("Pump speed out of range. Value should be between 0 and 100.")
    await self.modbus.write_register(
      self.pump_index_to_address[channel],
      speed,
      unit=self.address,  # type: ignore[call-arg]
    )


class AgrowPump:
  """A single pump channel on an :class:`AgrowPumpArray`."""

  def __init__(self, array: "AgrowPumpArray", channel: int):
    self.driver = array
    self._channel = channel

  async def run_revolutions(self, num_revolutions: float):
    raise NotImplementedError(
      "Revolution based pumping commands are not available for Agrow pumps."
    )

  async def run_continuously(self, speed: float):
    logger.info(
      "[Agrow %s addr=%s] channel %d: run_continuously at speed %d",
      self.driver.port,
      self.driver.address,
      self._channel,
      int(speed),
    )
    await self.driver.write_speed(self._channel, int(speed))

  async def halt(self):
    logger.info(
      "[Agrow %s addr=%s] channel %d: halt", self.driver.port, self.driver.address, self._channel
    )
    await self.driver.write_speed(self._channel, 0)

  def serialize(self):
    return {
      "port": self.driver.port,
      "address": self.driver.address,
      "channel": self._channel,
    }
