import threading
import time
from typing import Optional, List, Dict

from pylabrobot.pumps.backend import PumpArrayBackend

from pymodbus.client import AsyncModbusSerialClient as ModbusClient  # type: ignore


class AgrowPumpArray(PumpArrayBackend):
  """
  AgrowPumpArray is a class that allows users to control AgrowPumps via Modbus communication.

  Attributes:
      port: The port that the AgrowPumpArray is connected to.
      unit: The unit number of the AgrowPumpArray.
      pump_index_to_address: A dictionary that maps pump indices to their Modbus addresses.

  Properties:
      num_channels: The number of channels that the AgrowPumpArray has.
  """

  def __init__(self, port: str,
               unit: int,
               pump_index_to_address: Optional[Dict[int, int]] = None):
    self.port = port
    self.unit = unit
    self.pump_index_to_address = pump_index_to_address
    self.modbus: Optional[ModbusClient] = None

  @property
  def num_channels(self) -> int:
    """
    num_channels(self): This method returns the number of channels that the AgrowPumpArray has.

    Returns:
        int: The number of channels that the AgrowPumpArray has.

    """
    if self.pump_index_to_address is None:
      if self.modbus.connected:  # type: ignore[union-attr]
        register_return = \
          self.modbus.read_holding_registers(19, 2, unit=self.unit)  # type: ignore[union-attr]
        return int("".join(chr(r // 256) + chr(r % 256) for r in register_return.registers)[2])
      else:
        return 0
    else:
      return len(self.pump_index_to_address)

  def start_keep_alive_thread(self):

    """

    keep_alive_thread(self): This method creates a daemon thread that sends a Modbus request
    every 25 seconds to keep the connection alive.

    """

    def keep_alive():
      """
      keep_alive(self): This method sends a Modbus request every 25 seconds to keep the
      connection alive.
      """
      while True:
        time.sleep(25)

        self.modbus.read_holding_registers(0, 1, unit=self.unit)  # type: ignore[union-attr]

    thread = threading.Thread(target=keep_alive, daemon=True)
    thread.start()

  async def setup(self):
    """
    setup(self): This method sets up the Modbus connection to the AgrowPumpArray and creates the
    pump mappings needed to issue commands.

    Awaitable:
        self.modbus.connect(): This method connects to the AgrowPumpArray via Modbus.

    """
    self.modbus = ModbusClient(method="rtu", port=self.port,
                               baudrate=115200, timeout=1, stopbits=1, bytesize=8, parity="E",
                               retry_on_empty=True)
    await self.modbus.connect()
    self.start_keep_alive_thread()
    self.pump_index_to_address = {pump: pump + 100 for pump in range(self.num_channels)}

  async def run_revolutions(self, num_revolutions: List[float],
                            use_channels: List[int]):
    """Run the specified channels at the speed selected. If speed is 0, the pump will be halted.
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
    """Run pumps at the specified speeds.
        Args:
            speed: rate at which to run pump.
            use_channels: pump array channels to run

        Raises:
            ValueError: Pump address out of range
            ValueError: Pump speed out of range
    """
    if any(channel not in range(1, self.num_channels + 1) for channel in use_channels):
      raise ValueError("Pump address out of range")
    for pump_index, pump_speed in zip(use_channels, speed):
      pump_speed = int(pump_speed)
      if pump_speed not in range(101):
        raise ValueError("Pump speed out of range")
      self.modbus.write_register(  # type: ignore[union-attr]
        self.pump_index_to_address[pump_index], pump_speed, unit=self.unit)  # type: ignore[index]

  async def halt(self):
    """ Halt the entire pump array. """
    print("Engaging shutdown routine")
    for pump in self.pump_index_to_address:  # type: ignore[union-attr]
      address = self.pump_index_to_address[pump]  # type: ignore[index]
      self.modbus.write_register(address, 0, unit=self.unit)  # type: ignore[union-attr]

  async def stop(self):
    """ Close the connection to the pump array. """
    self.modbus.close()  # type: ignore[union-attr]
