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

from pylabrobot.capabilities.pumping.backend import PumpBackend
from pylabrobot.capabilities.pumping.calibration import PumpCalibration
from pylabrobot.capabilities.pumping.pumping import PumpingCapability
from pylabrobot.device import Device

logger = logging.getLogger("pylabrobot")


class AgrowDriver:
  """Shared Modbus connection for an Agrow pump array.

  Not a DeviceBackend — just the hardware communication layer.
  """

  def __init__(self, port: str, address: Union[int, str]):
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
          await self.modbus.read_holding_registers(0, 1, unit=self.address)
          i = 0

    def manage_async_keep_alive():
      try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(keep_alive())
        loop.close()
      except Exception as e:
        logger.error("Error in keep alive thread: %s", e)

    self._keep_alive_thread_active = True
    self._keep_alive_thread = threading.Thread(target=manage_async_keep_alive, daemon=True)
    self._keep_alive_thread.start()

  async def setup(self):
    await self._setup_modbus()
    register_return = await self.modbus.read_holding_registers(19, 2, unit=self.address)
    self._num_channels = int(
      "".join(chr(r // 256) + chr(r % 256) for r in register_return.registers)[2]
    )
    self._start_keep_alive_thread()
    self._pump_index_to_address = {pump: pump + 100 for pump in range(0, self.num_channels)}

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
      retry_on_empty=True,
    )
    await self.modbus.connect()
    if not self.modbus.connected:
      raise ConnectionError("Modbus connection failed during pump setup")

  async def stop(self):
    # halt all channels before closing
    for pump in self.pump_index_to_address:
      address = self.pump_index_to_address[pump]
      await self.modbus.write_register(address, 0, unit=self.address)
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
      unit=self.address,
    )


class AgrowChannelBackend(PumpBackend):
  """Per-channel PumpBackend adapter that delegates to a shared AgrowDriver."""

  def __init__(self, connection: AgrowDriver, channel: int):
    self._driver = connection
    self._channel = channel

  async def setup(self):
    pass  # lifecycle managed by the device via AgrowDriver

  async def stop(self):
    pass  # lifecycle managed by the device via AgrowDriver

  async def run_revolutions(self, num_revolutions: float):
    raise NotImplementedError(
      "Revolution based pumping commands are not available for Agrow pumps."
    )

  async def run_continuously(self, speed: float):
    await self._driver.write_speed(self._channel, int(speed))

  async def halt(self):
    await self._driver.write_speed(self._channel, 0)

  def serialize(self):
    return {
      **super().serialize(),
      "port": self._driver.port,
      "address": self._driver.address,
      "channel": self._channel,
    }


class AgrowDosePumpArray(Device):
  """Agrow dose pump array device.

  Exposes each channel as an individual PumpingCapability via `self.pumps`.
  """

  def __init__(
    self,
    port: str,
    address: Union[int, str],
    calibrations: Optional[List[Optional[PumpCalibration]]] = None,
  ):
    self._driver = AgrowDriver(port=port, address=address)
    # We need a DeviceBackend for Device.__init__; use channel 0's adapter as the "primary".
    # The real lifecycle is managed via _connection.
    self._channel_backends: List[AgrowChannelBackend] = []
    self.pumps: List[PumpingCapability] = []
    self._calibrations = calibrations
    # Defer full init until setup() when we know num_channels.
    # Pass a placeholder backend — we'll override setup/stop.
    super().__init__(backend=AgrowChannelBackend(self._driver, 0))

  async def setup(self):
    await self._driver.setup()
    num_channels = self._driver.num_channels

    self._channel_backends = [AgrowChannelBackend(self._driver, ch) for ch in range(num_channels)]
    self.pumps = []
    for i, backend in enumerate(self._channel_backends):
      cal = None
      if self._calibrations is not None and i < len(self._calibrations):
        cal = self._calibrations[i]
      cap = PumpingCapability(backend=backend, calibration=cal)
      self.pumps.append(cap)

    self._capabilities = list(self.pumps)
    for cap in self._capabilities:
      await cap._on_setup()
    self._setup_finished = True

  async def stop(self):
    for cap in reversed(self._capabilities):
      await cap._on_stop()
    await self._driver.stop()
    self._setup_finished = False

  def serialize(self):
    return {
      "port": self._driver.port,
      "address": self._driver.address,
    }
