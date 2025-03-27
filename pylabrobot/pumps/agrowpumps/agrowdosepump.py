import asyncio
import logging
import threading
import time
from typing import Dict, List, Optional, Union

from pymodbus.client import AsyncModbusSerialClient  # type: ignore

from pylabrobot.pumps.backend import PumpArrayBackend

logger = logging.getLogger("pylabrobot")


class AgrowPumpArray(PumpArrayBackend):
  """
  AgrowPumpArray allows users to control AgrowPumps via Modbus communication.

  https://www.agrowtek.com/doc/im/IM_MODBUS.pdf
  https://agrowtek.com/doc/im/IM_LX1.pdf

  Attributes:
    port: The port that the AgrowPumpArray is connected to.
    address: The address of the AgrowPumpArray client registers.

  Properties:
    num_channels: The number of channels that the AgrowPumpArray has.
    pump_index_to_address: A dictionary that maps pump indices to their Modbus addresses.
  """

  def __init__(self, port: str, address: Union[int, str]):
    if not isinstance(port, str):
      raise ValueError("Port must be a string")
    self.port = port
    if address not in range(0, 256):
      raise ValueError("Pump address out of range")
    self.address = int(address)
    self._keep_alive_thread: Optional[threading.Thread] = None
    self._pump_index_to_address: Optional[Dict[int, int]] = None
    self._modbus: Optional[AsyncModbusSerialClient] = None
    self._num_channels: Optional[int] = None
    self._keep_alive_thread_active = False

  @property
  def modbus(self) -> AsyncModbusSerialClient:
    """Returns the Modbus connection to the AgrowPumpArray.

    Returns:
      AsyncModbusSerialClient: The Modbus connection to the AgrowPumpArray.
    """

    if self._modbus is None:
      raise RuntimeError("Modbus connection not established")
    return self._modbus

  @property
  def pump_index_to_address(self) -> Dict[int, int]:
    """Returns a dictionary that maps pump indices to their Modbus addresses.

    Returns:
      Dict[int, int]: A dictionary that maps pump indices to their Modbus addresses.
    """

    if self._pump_index_to_address is None:
      raise RuntimeError("Pump mappings not established")
    return self._pump_index_to_address

  @property
  def num_channels(self) -> int:
    """The number of channels that the AgrowPumpArray has.

    Returns:
      int: The number of channels that the AgrowPumpArray has.
    """
    if self._num_channels is None:
      raise RuntimeError("Number of channels not established")
    return self._num_channels

  def start_keep_alive_thread(self):
    """Creates a daemon thread that sends a Modbus request
    every 25 seconds to keep the connection alive.
    """

    async def keep_alive():
      """Sends a Modbus request every 25 seconds to keep the connection alive.
      Sleep for 0.1 seconds so we can respond to `stop` events fast.
      """
      i = 0
      while self._keep_alive_thread_active:
        time.sleep(0.1)
        i += 1
        if i == 250:
          await self.modbus.read_holding_registers(0, 1, unit=self.address)
          i = 0

    def manage_async_keep_alive():
      """Manages the keep alive thread."""
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
    """Sets up the Modbus connection to the AgrowPumpArray and creates the
    pump mappings needed to issue commands.
    """
    await self._setup_modbus()
    register_return = await self.modbus.read_holding_registers(19, 2, unit=self.address)
    self._num_channels = int(
      "".join(chr(r // 256) + chr(r % 256) for r in register_return.registers)[2]
    )
    self.start_keep_alive_thread()
    self._pump_index_to_address = {pump: pump + 100 for pump in range(0, self.num_channels)}

  async def _setup_modbus(self):
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

  def serialize(self):
    return {
      **super().serialize(),
      "port": self.port,
      "address": self.address,
    }

  async def run_revolutions(self, num_revolutions: List[float], use_channels: List[int]):
    """Run the specified channels at the speed selected. If speed is 0, the pump will be halted.

    Args:
      num_revolutions: number of revolutions to run pumps.
      use_channels: pump array channels to run

    Raises:
      NotImplementedError: Revolution based pumping commands are not available for this array.
    """

    raise NotImplementedError(
      "Revolution based pumping commands are not available for this pump array."
    )

  async def run_continuously(self, speed: List[float], use_channels: List[int]):
    """Run pumps at the specified speeds.

    Args:
      speed: rate at which to run pump.
      use_channels: pump array channels to run

    Raises:
      ValueError: Pump address out of range
      ValueError: Pump speed out of range
    """

    for pump_index, pump_speed in zip(use_channels, speed):
      pump_speed = int(pump_speed)
      if pump_speed not in range(101):
        raise ValueError("Pump speed out of range. Value should be between 0 and 100.")
      await self.modbus.write_register(
        self.pump_index_to_address[pump_index],
        pump_speed,
        unit=self.address,
      )

  async def halt(self):
    """Halt the entire pump array."""
    assert self.modbus is not None, "Modbus connection not established"
    assert self.pump_index_to_address is not None, "Pump address mapping not established"
    logger.info("Halting pump array")
    for pump in self.pump_index_to_address:
      address = self.pump_index_to_address[pump]
      await self.modbus.write_register(address, 0, unit=self.address)

  async def stop(self):
    """Close the connection to the pump array."""
    await self.halt()
    assert self.modbus is not None, "Modbus connection not established"
    if self._keep_alive_thread is not None:
      self._keep_alive_thread_active = False
      self._keep_alive_thread.join()
    self.modbus.close()
    assert not self.modbus.connected, "Modbus failing to disconnect"
