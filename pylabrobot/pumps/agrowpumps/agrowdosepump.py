import time
import threading
from typing import Optional, List, Dict, Union
import logging

from pymodbus.client import AsyncModbusSerialClient  # type: ignore

from pylabrobot.pumps.backend import PumpArrayBackend

logger = logging.getLogger("pylabrobot")


class AgrowPumpArray(PumpArrayBackend):
  """
  AgrowPumpArray allows users to control AgrowPumps via Modbus communication.

  Attributes:
    port: The port that the AgrowPumpArray is connected to.
    address: The address of the AgrowPumpArray client registers.

  Properties:
    num_channels: The number of channels that the AgrowPumpArray has.
    pump_index_to_address: A dictionary that maps pump indices to their Modbus addresses.
  """

  def __init__(self, port: str, address: int):
    self.port = port
    self.address = address
    self._keep_alive_thread: Optional[threading.Thread] = None
    self._pump_index_to_address = None
    self._modbus: Optional[Union[AsyncModbusSerialClient]] = None
    self._num_channels: Optional[int] = None
    self._keep_alive_thread_active = False

  @property
  def modbus(self) -> Union[AsyncModbusSerialClient]:
    """ Returns the Modbus connection to the AgrowPumpArray.

    Returns:
      Union[AsyncModbusSerialClient]: The Modbus connection to the
      AgrowPumpArray.
    """

    if self._modbus is None:
      raise RuntimeError("Modbus connection not established")
    return self._modbus

  @property
  def pump_index_to_address(self) -> Dict[int, int]:
    """ Returns a dictionary that maps pump indices to their Modbus addresses.

    Returns:
      Dict[int, int]: A dictionary that maps pump indices to their Modbus addresses.
    """

    if self._pump_index_to_address is None:
      raise RuntimeError("Pump mappings not established")
    return self._pump_index_to_address

  @property
  def num_channels(self) -> int:
    """ The number of channels that the AgrowPumpArray has.

    Returns:
      int: The number of channels that the AgrowPumpArray has.
    """
    if self._num_channels is None:
      raise RuntimeError("Number of channels not established")
    return self._num_channels

  def start_keep_alive_thread(self):
    """ Creates a daemon thread that sends a Modbus request
    every 25 seconds to keep the connection alive.
    """

    def keep_alive():
      """ Sends a Modbus request every 25 seconds to keep the
      connection alive.
      """
      while self._keep_alive_thread_active:
        time.sleep(25)
        self.modbus.read_holding_registers(0, 1, unit=self.address)

    self._keep_alive_thread_active = True
    self._keep_alive_thread = threading.Thread(target=keep_alive(), daemon=True)
    self._keep_alive_thread.start()

  async def setup(self):
    """ Sets up the Modbus connection to the AgrowPumpArray and creates the
    pump mappings needed to issue commands.

    Awaitable:
      self.modbus.connect(): This method connects to the AgrowPumpArray via Modbus.
    """
    self._modbus = AsyncModbusSerialClient(port=self.port,
                                  baudrate=115200, timeout=1, stopbits=1, bytesize=8, parity="E",
                                  retry_on_empty=True)
    response = self.modbus.connect()
    if not response or not self.modbus.connected:
      raise ConnectionError("Modbus connection failed during pump setup")
    register_return = self.modbus.read_holding_registers(19, 2, unit=self.unit)
    self._num_channels = \
      int("".join(chr(r // 256) + chr(r % 256) for r in register_return.registers)[2])
    self.start_keep_alive_thread()
    self._pump_index_to_address = {pump: pump + 100 for pump in range(0, self.num_channels)}

  async def run_revolutions(self, num_revolutions: List[float],
                            use_channels: List[int]):
    """ Run the specified channels at the speed selected. If speed is 0, the pump will be halted.

    Args:
      num_revolutions: number of revolutions to run pumps.
      use_channels: pump array channels to run

    Raises:
      NotImplementedError: Revolution based pumping commands are not available for this array.
    """

    raise NotImplementedError(
      "Revolution based pumping commands are not available for this pump array.")

  async def run_continuously(self, speed: List[float],
                             use_channels: List[int]):
    """ Run pumps at the specified speeds.

    Args:
      speed: rate at which to run pump.
      use_channels: pump array channels to run

    Raises:
      ValueError: Pump address out of range
      ValueError: Pump speed out of range
    """

    if any(channel not in range(0, self.num_channels) for channel in use_channels):
      raise ValueError(f"Pump address out of range for this pump array. \
        Value should be between 0 and {self.num_channels}")
    for pump_index, pump_speed in zip(use_channels, speed):
      pump_speed = int(pump_speed)
      if pump_speed not in range(101):
        raise ValueError("Pump speed out of range. Value should be between 0 and 100.")
      self.modbus.write_register(self.pump_index_to_address[pump_index], pump_speed, unit=self.unit)

  async def halt(self):
    """ Halt the entire pump array. """
    assert self.modbus is not None, "Modbus connection not established"
    assert self.pump_index_to_address is not None, "Pump address mapping not established"
    logger.info("Halting pump array")
    for pump in self.pump_index_to_address:
      address = self.pump_index_to_address[pump]
      self.modbus.write_register(address, 0, unit=self.unit)

  async def stop(self):
    """ Close the connection to the pump array. """
    await self.halt()
    assert self.modbus is not None, "Modbus connection not established"
    if self._keep_alive_thread is not None:
      self._keep_alive_thread_active = False
      self._keep_alive_thread.join()
    self.modbus.close()
    assert not self.modbus.is_socket_open(), "Modbus failing to disconnect"
