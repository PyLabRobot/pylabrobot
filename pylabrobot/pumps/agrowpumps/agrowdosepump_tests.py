from typing import Optional, Dict

from pylabrobot.pumps.agrowpumps import AgrowPumpArray

class AgrowPumpArrayTester(AgrowPumpArray):
  """
  AgrowPumpArrayTester allows users to test AgrowPumpArray.
  """

  def __init__(self, port: str,
               address: int,
               pump_index_to_address: Optional[Dict[int, int]] = None):
    super().__init__(port=port, address=address, pump_index_to_address=pump_index_to_address)
    self._modbus: Optional[SimulatedModbusClient] = None

  @property
  def modbus(self) -> "SimulatedModbusClient":
    """ Returns the simulated Modbus connection to the AgrowPumpArrayTester.

    Returns:
      SimulatedModbusClient: The Modbus connection to the AgrowPumpArray.
    """

    if self._modbus is None:
      raise RuntimeError("Modbus connection not established")
    return self._modbus


  async def setup(self):
    """ Sets up the Modbus connection to the AgrowPumpArray and creates the
    pump mappings needed to issue commands.

    Awaitable:
      self.modbus.connect(): This method connects to the AgrowPumpArray via Modbus.
    """
    self._modbus = SimulatedModbusClient()
    response = self.modbus.connect()
    if not response:
      raise ConnectionError("Modbus connection failed during pump setup")
    self.start_keep_alive_thread()
    if self.modbus.connected:
        register_return = \
          self.modbus.read_holding_registers(19, 2, unit=self.unit)
        self.agrow_pump_array_num_channels = \
          int("".join(chr(r // 256) + chr(r % 256) for r in register_return.registers)[2])
    else:
      raise ConnectionError("Modbus connection failed during pump setup")
    self._pump_index_to_address = {pump: pump + 100 for pump in range(0, self.num_channels)}


class SimulatedModbusClient:
  """
  SimulatedModbusClient allows users to simulate Modbus communication.

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

  def write_register(self, *args, **kwargs):
    assert self.connected, "Modbus connection not established"
    if "unit" not in kwargs:
      raise ValueError("unit must be specified")
    if args[0] not in range(100, 107):
      raise ValueError("address out of range")

  def close(self):
    assert self.connected, "Modbus connection not established"
    self.connected = False
