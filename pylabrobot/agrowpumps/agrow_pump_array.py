import asyncio
import logging
import threading
import time
from typing import Dict, List, Optional, Union

try:
  from pymodbus.client import AsyncModbusSerialClient  # type: ignore

  _MODBUS_IMPORT_ERROR = None
except ImportError as e:
  AsyncModbusSerialClient = None  # type: ignore
  _MODBUS_IMPORT_ERROR = e

logger = logging.getLogger(__name__)


class AgrowPumpArray:
  """Agrow dose pump array.

  Each channel is exposed as an :class:`AgrowChannel` on ``self.channels`` once
  :meth:`setup` has queried the number of channels from the hardware.
  """

  def __init__(self, port: str, address: Union[int, str]):
    super().__init__()
    if _MODBUS_IMPORT_ERROR is not None:
      raise RuntimeError(
        "pymodbus is not installed. Install with: pip install pylabrobot[modbus]. "
        f"Import error: {_MODBUS_IMPORT_ERROR}"
      )
    if not isinstance(port, str):
      raise ValueError("Port must be a string")
    self.port = port
    if address not in range(0, 256):
      raise ValueError("Pump address out of range")
    self.address = int(address)
    self._keep_alive_thread: Optional[threading.Thread] = None
    self._pump_index_to_address: Optional[Dict[int, int]] = None
    self._modbus: Optional["AsyncModbusSerialClient"] = None
    self._num_channels: Optional[int] = None
    self._keep_alive_thread_active = False
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

  def _start_keep_alive_thread(self):
    async def keep_alive():
      i = 0
      while self._keep_alive_thread_active:
        time.sleep(0.1)
        i += 1
        if i == 250:
          await self.modbus.read_holding_registers(0, 1, unit=self.address)  # type: ignore[call-arg, misc]
          i = 0

    def manage_async_keep_alive():
      try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(keep_alive())
        loop.close()
      except Exception as e:
        logger.error("[Agrow %s addr=%s] keep-alive thread error: %s", self.port, self.address, e)

    self._keep_alive_thread_active = True
    self._keep_alive_thread = threading.Thread(target=manage_async_keep_alive, daemon=True)
    self._keep_alive_thread.start()

  async def setup(self):
    await self._setup_modbus()
    register_return = await self.modbus.read_holding_registers(19, 2, unit=self.address)  # type: ignore[call-arg, misc]
    self._num_channels = int(
      "".join(chr(r // 256) + chr(r % 256) for r in register_return.registers)[2]
    )
    self._start_keep_alive_thread()
    self._pump_index_to_address = {pump: pump + 100 for pump in range(0, self.num_channels)}
    self.channels = [AgrowPump(self, ch) for ch in range(self.num_channels)]
    logger.info(
      "[Agrow %s addr=%s] connected: channels=%d", self.port, self.address, self._num_channels
    )

  async def _setup_modbus(self):
    if AsyncModbusSerialClient is None:
      raise RuntimeError(
        "pymodbus is not installed. Install with: pip install pylabrobot[modbus]."
        f" Import error: {_MODBUS_IMPORT_ERROR}"
      )
    self._modbus = AsyncModbusSerialClient(
      port=self.port,
      baudrate=115200,
      timeout=1,
      stopbits=1,
      bytesize=8,
      parity="E",
      retry_on_empty=True,  # type: ignore[call-arg]
    )
    await self.modbus.connect()
    if not self.modbus.connected:
      logger.error("[Agrow %s] modbus connection failed", self.port)
      raise ConnectionError("Modbus connection failed during pump setup")

  async def stop(self):
    logger.info("[Agrow %s addr=%s] stopping", self.port, self.address)
    for pump in self.pump_index_to_address:
      await self.write_speed(pump, 0)
    if self._keep_alive_thread is not None:
      self._keep_alive_thread_active = False
      self._keep_alive_thread.join()
    self.modbus.close()
    assert not self.modbus.connected, "Modbus failing to disconnect"

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
