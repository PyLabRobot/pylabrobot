import logging
from typing import Dict, List, Optional, Union

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding

try:
  from pymodbus.client import AsyncModbusSerialClient  # type: ignore

  _MODBUS_IMPORT_ERROR = None
except ImportError as e:
  AsyncModbusSerialClient = None  # type: ignore
  _MODBUS_IMPORT_ERROR = e

from pylabrobot.pumps.backend import PumpArrayBackend

logger = logging.getLogger("pylabrobot")


class AgrowPumpArrayBackend(PumpArrayBackend):
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
    self._pump_index_to_address: Optional[Dict[int, int]] = None
    self._modbus: Optional["AsyncModbusSerialClient"] = None
    self._num_channels: Optional[int] = None

  @property
  def modbus(self) -> "AsyncModbusSerialClient":
    """Returns the Modbus connection to the AgrowPumpArray."""
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
      The number of channels that the AgrowPumpArray has.
    """
    if self._num_channels is None:
      raise RuntimeError("Number of channels not established")
    return self._num_channels

  async def _keep_alive_task(self):
    """Sends a Modbus request every 25 seconds to keep the connection alive."""
    while True:
      await anyio.sleep(25)
      # do a keep-alive
      assert self._modbus is not None
      await self._modbus.read_holding_registers(0, 1, unit=self.address)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    """Sets up the Modbus connection to the AgrowPumpArray and creates the
    pump mappings needed to issue commands.
    """
    if self._modbus is None:
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
    stack.callback(self._modbus.close)

    register_return = await self._modbus.read_holding_registers(19, 2, unit=self.address)
    self._num_channels = int(
      "".join(chr(r // 256) + chr(r % 256) for r in register_return.registers)[2]
    )

    tg = await stack.enter_async_context(anyio.create_task_group())
    stack.callback(tg.cancel_scope.cancel)

    tg.start_soon(self._keep_alive_task)

    stack.push_shielded_async_callback(self.halt)

    self._pump_index_to_address = {pump: pump + 100 for pump in range(0, self.num_channels)}

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


# Deprecated alias with warning # TODO: remove mid May 2025 (giving people 1 month to update)
# https://github.com/PyLabRobot/pylabrobot/issues/466


class AgrowPumpArray:
  def __init__(self, *args, **kwargs):
    raise RuntimeError(
      "`AgrowPumpArray` is deprecated. Please use `AgrowPumpArrayBackend` instead."
    )
