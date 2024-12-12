import asyncio
import atexit
import logging
import sys
from typing import Optional, Union, cast

import serial

from pylabrobot.incubators.backend import IncubatorBackend
from pylabrobot.incubators.cytomat.config import CYTOMAT_CONFIG
from pylabrobot.incubators.cytomat.constants import (
  HEX,
  ActionRegister,
  ActionType,
  CommandType,
  CytomatActionResponse,
  CytomatComplexCommand,
  CytomatIncubationQuery,
  CytomatIncupationResponse,
  CytomatLowLevelCommand,
  CytomatRegisterType,
  CytomatType,
  ErrorRegister,
  LoadStatusAtProcessor,
  LoadStatusFrontOfGate,
  OverviewRegister,
  SensorRegister,
  SwapStationPosition,
  WarningRegister,
)
from pylabrobot.incubators.cytomat.errors import CytomatTelegramStructureError, error_map
from pylabrobot.incubators.cytomat.schemas import (
  ActionRegisterState,
  OverviewRegisterState,
  SensorStates,
  SwapStationState,
)
from pylabrobot.incubators.cytomat.utils import (
  hex_to_base_twelve,
  hex_to_binary,
  site_number,
)
from pylabrobot.incubators.rack import Rack
from pylabrobot.resources.carrier import PlateHolder

logger = logging.getLogger(__name__)


class Cytomat(IncubatorBackend):
  default_baud = 9600
  serial_message_encoding = "utf-8"

  def __init__(self, model: CytomatType):
    supported_models = [
      CytomatType.C6000,
      CytomatType.C6002,
      CytomatType.C2C_425,
      CytomatType.C2C_450_SHAKE,
      CytomatType.C5C,
    ]
    if model not in supported_models:
      raise NotImplementedError("Only the following Cytomats are supported:", supported_models)
    self.model = model

  async def setup(self):
    self.open_serial_connection()
    await self.initialize()

  async def stop(self):
    self.close_connection()

  def open_serial_connection(self):
    try:
      self.ser = serial.Serial(
        port=self.port,
        baudrate=self.default_baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        write_timeout=1,
        timeout=1,
      )

    except serial.SerialException as e:
      logger.error("Could not connect to cytomat, is it in use by a different notebook?")
      raise e

    # atexit.register(self.close_connection)

  def close_connection(self):
    if self.ser.is_open:
      self.ser.close()

  def handle_error(resp):
    error_code = int(resp[3:5])
    if error_code in error_map:
      raise error_map[error_code]

    raise Exception(f"Unknown cytomat error code in response: {resp}")

  def _get_carriage_return(self):
    if self.model == CytomatType.C2C_425:
      return "\r"
    return "\r\n"

  def _assemble_command(self, command_type, prefix, params):
    command = f"{command_type.value}:{prefix.value} {params}".strip() + self._get_carriage_return()
    return command

  async def _send_cmd(
    self,
    command_type: CommandType,
    prefix: Union[CytomatRegisterType, CytomatComplexCommand],
    params: str,
    retries: int = 3,
  ) -> HEX:
    command = self._assemble_command(command_type=command_type, prefix=prefix, params=params)
    logging.debug(command.encode(self.serial_message_encoding))
    self.ser.write(command.encode(self.serial_message_encoding))
    resp = self.ser.read(128).decode(self.serial_message_encoding)
    if len(resp) == 0:
      raise RuntimeError("Cytomat did not respond to command, is it turned on?")
    key, *values = resp.split()
    value = " ".join(values)

    if key == CytomatActionResponse.OK.value or key == prefix.value:
      # actions return an OK response, while checks return the prefix at the start of the response
      return value
    if key == CytomatActionResponse.ERROR.value:
      logger.error(f"Retrying: '{command}'. Failed with: '{resp}'")
      if retries > 0:
        if retries > 1:
          await asyncio.sleep(5)
        else:
          # on the last retry, attemp a re-initialization
          await self.initialize()
          await self.wait_for_task_completion()
          if (await self.get_overview_register()).error_register_set:
            await self.reset_error_register()

        return await self._send_cmd(
          command_type=command_type,
          prefix=prefix,
          params=params,
          retries=retries - 1,
        )

      if value == "03":
        error_register = await self.get_error_register()
        raise CytomatTelegramStructureError(f"Telegram structure error: {error_register}")
      if value in error_map:
        raise error_map[value]
      raise Exception(f"Unknown cytomat error code in response: {resp}")

    raise Exception(f"Unknown response from cytomat: {resp}")

  def _site_to_firmware_string(self, site: PlateHolder) -> str:
    rack = cast(Rack, site.parent)
    rack_idx = rack.index
    site_idx = next(idx for idx, s in rack.sites.items() if s == site)

    if self.model in [CytomatType.C2C_425]:
      return f"{str(self.rack).zfill(2)} {str(site_idx).zfill(2)}"

    if self.model in [
      CytomatType.C6000,
      CytomatType.C6002,
      CytomatType.C2C_450_SHAKE,
      CytomatType.C5C,
    ]:
      slots_to_skip = sum(r.capacity for r in self.racks[:rack_idx])
      if self.model == CytomatType.C2C_450_SHAKE:
        # TODO: is this generally true?
        # This is the "rack shaker" we ripped out ever other level so multiply by two.
        # The initial rack shaker is unused, so add fifteen.
        absolute_slot = 15 + 2 * (slots_to_skip + site_idx)
      else:
        absolute_slot = slots_to_skip + site_idx

      return f"{absolute_slot:03}"

    raise ValueError(f"Unsupported Cytomat model: {self.model}")

  async def get_overview_register(self) -> OverviewRegisterState:
    hex_value = await self._send_cmd(CommandType.CHECK_REGISTER, CytomatRegisterType.OVERVIEW, "")
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def get_warning_register(self) -> WarningRegister:
    hex_value = await self._send_cmd(CommandType.CHECK_REGISTER, CytomatRegisterType.WARNING, "")
    for member in WarningRegister:
      if hex_value == member.value:
        return member

    raise Exception(f"Unknown warning register value: {hex_value}")

  async def get_error_register(self) -> ErrorRegister:
    hex_value = await self._send_cmd(CommandType.CHECK_REGISTER, CytomatRegisterType.ERROR, "")
    for member in ErrorRegister:
      if hex_value == member.value:
        return member

    raise Exception(f"Unknown error register value: {hex_value}")

  async def reset_error_register(self) -> None:
    await self._send_cmd(CommandType.RESET_REGISTER, CytomatRegisterType.ERROR, "")

  async def initialize(self) -> None:
    """
    move the cytomat arm to the home position
    """
    await self._send_cmd(CommandType.LOW_LEVEL_COMMAND, CytomatLowLevelCommand.INITIALIZE, "")

  async def action_open_device_door(self):
    hex_value = await self._send_cmd(
      CommandType.LOW_LEVEL_COMMAND, CytomatLowLevelCommand.AUTOMATIC_GATE, "002"
    )
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def action_close_device_door(self):
    hex_value = await self._send_cmd(
      CommandType.LOW_LEVEL_COMMAND, CytomatLowLevelCommand.AUTOMATIC_GATE, "001"
    )

    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def get_action_register(self) -> ActionRegisterState:
    hex_value = await self._send_cmd(CommandType.CHECK_REGISTER, CytomatRegisterType.ACTION, "")
    binary_repr = hex_to_binary(hex_value)
    target, action = binary_repr[:3], binary_repr[3:]

    target_enum = None
    for member in ActionType:
      if int(target, 2) == int(member.value, 16):
        target_enum = member
        break
    assert target_enum is not None, f"Unknown target value: {target}"

    action_enum = None
    for member in ActionRegister:
      if int(action, 2) == int(member.value, 16):
        action_enum = member
        break
    assert action_enum is not None, f"Unknown HIGH_LEVEL_COMMANDment value: {action}"

    return ActionRegisterState(target=target_enum, action=action_enum)

  async def get_swap_register(self) -> SwapStationState:
    value = await self._send_cmd(CommandType.CHECK_REGISTER, CytomatRegisterType.SWAP, "")

    return SwapStationState(
      position=SwapStationPosition(int(value[0])),
      load_status_front_of_gate=LoadStatusFrontOfGate(int(value[1])),
      load_status_at_processor=LoadStatusAtProcessor(int(value[2])),
    )

  async def get_sensor_register(self) -> SensorStates:
    hex_value = await self._send_cmd(CommandType.CHECK_REGISTER, CytomatRegisterType.SENSOR, "")
    binary_values = hex_to_base_twelve(hex_value)
    return SensorStates(
      **{member.name: bool(int(binary_values[member.value])) for member in SensorRegister}
    )

  async def action_transfer_to_storage(  # used by insert_plate
    self, site: PlateHolder
  ) -> OverviewRegisterState:
    hex_value = await self._send_cmd(
      CommandType.HIGH_LEVEL_COMMAND,
      CytomatComplexCommand.TRANSFER_TO_STORAGE,
      self._site_to_firmware_string(site),
    )
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def action_storage_to_transfer(  # used by retrieve_plate
    self, site: PlateHolder
  ) -> OverviewRegisterState:
    hex_value = await self._send_cmd(
      CommandType.HIGH_LEVEL_COMMAND,
      CytomatComplexCommand.STORAGE_TO_TRANSFER,
      self._site_to_firmware_string(site),
    )
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def action_storage_to_wait(self, site: PlateHolder) -> OverviewRegisterState:
    hex_value = await self._send_cmd(
      CommandType.HIGH_LEVEL_COMMAND,
      CytomatComplexCommand.STORAGE_TO_WAIT,
      self._site_to_firmware_string(site),
    )
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def action_wait_to_storage(self, site: PlateHolder) -> OverviewRegisterState:
    hex_value = await self._send_cmd(
      CommandType.HIGH_LEVEL_COMMAND,
      CytomatComplexCommand.WAIT_TO_STORAGE,
      self._site_to_firmware_string(site),
    )
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def action_wait_to_transfer(self) -> OverviewRegisterState:
    hex_value = await self._send_cmd(
      CommandType.HIGH_LEVEL_COMMAND, CytomatComplexCommand.WAIT_TO_TRANSFER, ""
    )
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def action_transfer_to_wait(self) -> OverviewRegisterState:
    hex_value = await self._send_cmd(
      CommandType.HIGH_LEVEL_COMMAND, CytomatComplexCommand.TRANSFER_TO_WAIT, ""
    )
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def action_wait_to_exposed(self) -> OverviewRegisterState:
    hex_value = await self._send_cmd(
      CommandType.HIGH_LEVEL_COMMAND, CytomatComplexCommand.WAIT_TO_EXPOSED, ""
    )
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def action_exposed_to_wait(self) -> OverviewRegisterState:
    hex_value = await self._send_cmd(
      CommandType.HIGH_LEVEL_COMMAND, CytomatComplexCommand.EXPOSED_TO_WAIT, ""
    )
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def action_exposed_to_storage(self, site: PlateHolder) -> OverviewRegisterState:
    hex_value = await self._send_cmd(
      CommandType.HIGH_LEVEL_COMMAND,
      CytomatComplexCommand.EXPOSED_TO_STORAGE,
      self._site_to_firmware_string(site),
    )
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def action_storage_to_exposed(self, site: PlateHolder) -> OverviewRegisterState:
    hex_value = await self._send_cmd(
      CommandType.HIGH_LEVEL_COMMAND,
      CytomatComplexCommand.STORAGE_TO_EXPOSED,
      self._site_to_firmware_string(site),
    )
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def action_read_barcode(
    self,
    site_number_a: str,
    site_number_b: str,
  ) -> OverviewRegisterState:
    site_number(site_number_a)
    site_number(site_number_b)
    hex_value = await self._send_cmd(
      CommandType.HIGH_LEVEL_COMMAND,
      CytomatComplexCommand.READ_BARCODE,
      f"{site_number_a} {site_number_b}",
    )
    binary_value = hex_to_binary(hex_value)

    return OverviewRegisterState(
      **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
    )

  async def wait_for_transfer_station_to_be_unoccupied(self):
    dots = 0
    message = "Transfer station is occupied, waiting for it to open"
    max_dots = 3
    while (await self.get_overview_register()).transfer_station_occupied:
      sys.stdout.write(f"\r{message}{'.' * dots}" + " " * max_dots)
      sys.stdout.flush()
      dots = (dots + 1) % (max_dots + 1)
      await asyncio.sleep(1)
    sys.stdout.write("\r" + " " * (len(message) + max_dots) + "\r")
    sys.stdout.flush()

  async def wait_for_transfer_station_to_be_occupied(self):
    dots = 0
    message = "Transfer station is open, waiting for it to be occupied"
    max_dots = 3
    while not (await self.get_overview_register()).transfer_station_occupied:
      sys.stdout.write(f"\r{message}{'.' * dots}" + " " * max_dots)
      sys.stdout.flush()
      dots = (dots + 1) % (max_dots + 1)
      await asyncio.sleep(1)

    sys.stdout.write("\r" + " " * (len(message) + max_dots) + "\r")
    sys.stdout.flush()

  async def wait_for_task_completion(self):
    # TODO #108 - turn this into a context that insulates both sides of an action
    dots = 0
    message = "Cytomat is busy, waiting for it to finish"
    max_dots = 3
    while True:
      overview_register = await self.get_overview_register()
      if not overview_register.busy_bit_set:
        break

      sys.stdout.write(f"\r{message}{'.' * dots}" + " " * max_dots)
      sys.stdout.flush()
      dots = (dots + 1) % (max_dots + 1)

      await asyncio.sleep(1)

    sys.stdout.write("\r" + " " * (len(message) + max_dots) + "\r")
    sys.stdout.flush()

  async def insert_plate(self, cytomate_plate: CytomatPlate, site: PlateHolder):
    await self.wait_for_task_completion()
    await self.action_transfer_to_storage(cytomat_location)

  async def retrieve_plate(self, site: PlateHolder) -> CytomatPlate:
    await self.wait_for_task_completion()
    await self.action_storage_to_transfer(location)

  async def init_shakers(self):
    return hex_to_binary(
      await self._send_cmd(
        CommandType.LOW_LEVEL_COMMAND,
        CytomatComplexCommand.INITIALIZE_SHAKERS,
        "",
      )
    )

  async def start_shaking(self):
    await self.wait_for_task_completion()
    if self.model == CytomatType.C5C:
      raise NotImplementedError("Shaking is not supported on this model")

    return hex_to_binary(
      await self._send_cmd(CommandType.LOW_LEVEL_COMMAND, CytomatComplexCommand.START_SHAKING, "")
    )

  async def stop_shaking(self):
    await self.wait_for_task_completion()
    if self.model == CytomatType.C5C:
      raise NotImplementedError("Shaking is not supported on this model")

    return hex_to_binary(
      await self._send_cmd(CommandType.LOW_LEVEL_COMMAND, CytomatComplexCommand.STOP_SHAKING, "")
    )

  async def set_shaking_frequency(self, frequency: int, shaker: Optional[int] = 0):
    frequency = f"{frequency:04}"
    if shaker == 1 or shaker == 0:
      hex1 = await self._send_cmd(
        CommandType.SET_PARAMETER,
        CytomatComplexCommand.SET_FREQUENCY_TOS_1,
        frequency,
      )
      if shaker == 1:
        return hex1
    if shaker == 2 or shaker == 0:
      hex2 = await self._send_cmd(
        CommandType.SET_PARAMETER,
        CytomatComplexCommand.SET_FREQUENCY_TOS_2,
        frequency,
      )
      if shaker == 2:
        return hex_to_binary(hex2)
    if shaker == 0:
      return hex_to_binary(hex1), hex_to_binary(hex2)
    raise ValueError("Shaker number must be 1, 2 or 0 for both")

  async def get_incubation_query(self, query: CytomatIncubationQuery) -> CytomatIncupationResponse:
    resp = await self._send_cmd(CommandType.CHECK_REGISTER, query, "")
    nominal, actual = resp.split()
    return CytomatIncupationResponse(
      nominal_value=float(nominal.lstrip("+")), actual_value=float(actual.lstrip("+"))
    )

  async def open_door(self, *args, **kwargs):
    pass

  async def close_door(self, *args, **kwargs):
    pass

  async def fetch_plate(self, *args, **kwargs):
    pass

  async def get_temperature(self, *args, **kwargs):
    pass

  async def set_temperature(self, *args, **kwargs):
    pass

  async def take_in_plate(self, *args, **kwargs):
    pass


class CytomatChatterbox(Cytomat):
  def open_serial_connection(self):
    print("Opening serial connection")

  async def _send_cmd(self, command_type, prefix, params, retries=3):
    print(self._assemble_command(command_type=command_type, prefix=prefix, params=params))
    if command_type == CommandType.CHECK_REGISTER:
      return "0"
    return "0" * 8
