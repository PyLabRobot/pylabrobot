import functools
import time
import threading
from typing import Optional, List, Dict, Union, Callable

from pymodbus.client import ModbusSerialClient as ModbusClient  # type: ignore

from pylabrobot.pumps.backend import PumpArrayBackend


def check_pump_mappings(func: Callable):
  """ Decorator for methods that require the modbus connection to be established.

  Checked by verifying `self.pump_index_to_address` is not `None`.

  Raises:
    RuntimeError: If the pump mappings are not established.
  """

  @functools.wraps(func)
  async def wrapper(*args, **kwargs):
    if args[0].pump_mappings is None:
      raise RuntimeError("Pump mappings not established")
    return func(*args, **kwargs)

  return wrapper


class AgrowPumpArray(PumpArrayBackend):
  """
  AgrowPumpArray is a class that allows users to control AgrowPumps via Modbus communication.

  Attributes:
    port: The port that the AgrowPumpArray is connected to.
    unit: The unit number of the AgrowPumpArray.

  Properties:
    num_channels: The number of channels that the AgrowPumpArray has.
    pump_index_to_address: A dictionary that maps pump indices to their Modbus addresses.
  """

  def __init__(self, port: str,
               unit: int,
               pump_index_to_address: Optional[Dict[int, int]] = None,
               keep_alive_thread_enabled: bool = True):
    self.port = port
    self.unit = unit
    self._pump_index_to_address = pump_index_to_address
    self._modbus: Optional[Union[ModbusClient, SimulatedModbusClient]] = None
    self.keep_alive_thread: Optional[threading.Thread] = None
    self.agrow_pump_array_num_channels = 0
    self.keep_alive_thread_enabled = keep_alive_thread_enabled

  @property
  def modbus(self) -> Union[ModbusClient, "SimulatedModbusClient"]:
    """ Returns the Modbus connection to the AgrowPumpArray.

    Returns:
      Union[ModbusClient, SimulatedModbusClient]: The Modbus connection to the
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

    return self.agrow_pump_array_num_channels

  async def start_keep_alive_thread(self):
    """ Creates a daemon thread that sends a Modbus request
    every 25 seconds to keep the connection alive.
    """

    def keep_alive():
      """ Sends a Modbus request every 25 seconds to keep the
      connection alive.
      """
      time.sleep(25)
      self.modbus.read_holding_registers(0, 1, unit=self.unit)

    self.keep_alive_thread = threading.Thread(target=keep_alive(), daemon=True)
    self.keep_alive_thread.start()

  async def setup(self):
    """ Sets up the Modbus connection to the AgrowPumpArray and creates the
    pump mappings needed to issue commands.

    Awaitable:
      self.modbus.connect(): This method connects to the AgrowPumpArray via Modbus.
    """

    if self.port == "simulated":
      self._modbus = SimulatedModbusClient()
    else:
      self._modbus = ModbusClient(port=self.port,
                                  baudrate=115200, timeout=1, stopbits=1, bytesize=8, parity="E",
                                  retry_on_empty=True)
    response = self.modbus.connect()
    if not response:
      raise ConnectionError("Modbus connection failed during pump setup")
    if self.keep_alive_thread_enabled:
      await self.start_keep_alive_thread()
    await self.set_num_channels()
    await self.set_pump_mappings()

  async def set_num_channels(self):
    if self._pump_index_to_address is None:
      if self.modbus.connected:
        register_return = \
          self.modbus.read_holding_registers(19, 2, unit=self.unit)
        self.agrow_pump_array_num_channels = \
          int("".join(chr(r // 256) + chr(r % 256) for r in register_return.registers)[2])
    else:
      return int(len(self.pump_index_to_address))

  async def set_pump_mappings(self):
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
    print("Engaging pump halt routine")
    for pump in self.pump_index_to_address:
      address = self.pump_index_to_address[pump]
      self.modbus.write_register(address, 0, unit=self.unit)

  async def stop(self):
    """ Close the connection to the pump array. """
    await self.halt()
    assert self.modbus is not None, "Modbus connection not established"
    if self.keep_alive_thread is not None:
      self.keep_alive_thread.join()
    self.modbus.close()
    assert not self.modbus.is_socket_open(), "Modbus failing to disconnect"


class SimulatedModbusClient:
  """
  SimulatedModbusClient is a class that allows users to simulate Modbus communication.

  Attributes:
    connected: A boolean that indicates whether the simulated client is connected.
  """

  def __init__(self, connected: bool = False):
    self.connected = connected

  def connect(self):
    self.connected = True

  @staticmethod
  def read_holding_registers(*args, **kwargs):
    """ Simulates reading holding registers from the AgrowPumpArray. """
    if "unit" not in kwargs:
      raise ValueError("unit must be specified")
    if args[0] == 19:
      return_register = type("return_register",
                             (object,),
                             {"registers": [16708, 13824, 0, 0, 0, 0, 0]})()
      return_register.registers = return_register.registers[:args[1]]
      return return_register
    if args[0] == 0:
      return

  def write_register(self, *args, **kwargs):
    assert self.connected, "Modbus connection not established"
    if "unit" not in kwargs:
      raise ValueError("unit must be specified")
    if args[0] not in range(100, 107):
      raise ValueError("address out of range")

  def close(self):
    assert self.connected, "Modbus connection not established"
    self.connected = False
