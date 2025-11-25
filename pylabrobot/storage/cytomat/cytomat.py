import asyncio
import logging
import time
import warnings
from typing import List, Literal, Optional, Union, cast

import serial

from pylabrobot.io.serial import Serial
from pylabrobot.resources import Plate, PlateCarrier, PlateHolder
from pylabrobot.storage.backend import IncubatorBackend
from pylabrobot.storage.cytomat.constants import (
  ActionRegister,
  ActionType,
  CytomatActionResponse,
  CytomatIncupationResponse,
  CytomatType,
  ErrorRegister,
  LoadStatusAtProcessor,
  LoadStatusFrontOfGate,
  SensorRegister,
  SwapStationPosition,
  WarningRegister,
)
from pylabrobot.storage.cytomat.errors import (
  CytomatBusyError,
  CytomatCommandUnknownError,
  CytomatTelegramStructureError,
  error_map,
  error_register_map,
)
from pylabrobot.storage.cytomat.schemas import (
  ActionRegisterState,
  OverviewRegisterState,
  SensorStates,
  SwapStationState,
)
from pylabrobot.storage.cytomat.utils import (
  hex_to_base_twelve,
  hex_to_binary,
  validate_storage_location_number,
)

logger = logging.getLogger(__name__)


class CytomatBackend(IncubatorBackend):
  default_baud = 9600
  serial_message_encoding = "utf-8"

  def __init__(self, model: Union[CytomatType, str], port: str):
    super().__init__()

    supported_models = [
      CytomatType.C6000,
      CytomatType.C6002,
      CytomatType.C2C_425,
      CytomatType.C2C_450_SHAKE,
      CytomatType.C5C,
    ]
    if isinstance(model, str):
      try:
        model = CytomatType(model)
      except ValueError:
        raise ValueError(f"Unsupported Cytomat model: '{model}'")
    if model not in supported_models:
      raise NotImplementedError(
        f"Only the following Cytomats are supported: {supported_models}, but got '{model}'"
      )
    self.model = model
    self._racks: List[PlateCarrier] = []

    self.io = Serial(
      port=port,
      baudrate=self.default_baud,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
      write_timeout=1,
      timeout=1,
    )

  async def setup(self):
    await self.io.setup()
    await self.initialize()
    await self.wait_for_task_completion()

  async def set_racks(self, racks: List[PlateCarrier]):
    await super().set_racks(racks)
    warnings.warn("Cytomat racks need to be configured with the exe software")

  async def stop(self):
    await self.io.stop()

  def _assemble_command(self, command_type: str, command: str, params: str):
    carriage_return = "\r" if self.model == CytomatType.C2C_425 else "\r\n"
    command = f"{command_type}:{command} {params}".strip() + carriage_return
    return command

  async def send_command(self, command_type: str, command: str, params: str) -> str:
    async def _send_command(command_str) -> str:
      logger.debug(command_str.encode(self.serial_message_encoding))
      await self.io.write(command_str.encode(self.serial_message_encoding))
      resp = (await self.io.read(128)).decode(self.serial_message_encoding)
      if len(resp) == 0:
        raise RuntimeError("Cytomat did not respond to command, is it turned on?")
      key, *values = resp.split()
      value = " ".join(values)

      if key == CytomatActionResponse.OK.value or key == command:
        # actions return an OK response, while checks return the command at the start of the response
        return value
      if key == CytomatActionResponse.ERROR.value:
        logger.error("Command %s failed with: '%s'", command_str, resp)
        if value == "03":
          error_register = await self.get_error_register()
          await self.reset_error_register()
          raise CytomatTelegramStructureError(f"Telegram structure error: {error_register}")
        if int(value, base=16) in error_map:
          await self.reset_error_register()
          raise error_map[int(value, base=16)]
        await self.reset_error_register()
        raise Exception(f"Unknown cytomat error code in response: {resp}")

      logger.error("Command %s received an unknown response: '%s'", command_str, resp)
      await self.reset_error_register()
      raise Exception(f"Unknown response from cytomat: {resp}")

    # Cytomats sometimes return a busy or command not recognized error even when the overview
    # register says the machine is not busy, or if the command is known. We will retry a few times,
    # which costs 1s if there is a true error, but is necessary to avoid false negatives.
    command_str = self._assemble_command(command_type=command_type, command=command, params=params)
    n_retries = 10
    exc: Optional[BaseException] = None
    for _ in range(n_retries):
      try:
        return await _send_command(command_str)
      except (CytomatCommandUnknownError, CytomatBusyError) as e:
        exc = e
        await asyncio.sleep(0.1)
        continue
    assert exc is not None
    await self.reset_error_register()
    raise exc

  async def send_action(
    self, command_type: str, command: str, params: str, timeout: Optional[int] = 60
  ) -> OverviewRegisterState:
    """Calls send_command, but has a timeout handler and returns the overview register state.
    Args:
      timeout: The maximum time to wait for the command to complete. If None, the command will not
        wait for completion.
    """
    await self.send_command(command_type, command, params)
    if timeout is not None:
      overview_register = await self.wait_for_task_completion(timeout=timeout)
    return overview_register

  def _site_to_firmware_string(self, site: PlateHolder) -> str:
    rack = cast(PlateCarrier, site.parent)
    rack_idx = [rack.name for rack in self._racks].index(
      rack.name
    )  # autoreload resistant, should work
    site_idx = next(idx for idx, s in rack.sites.items() if s == site)

    if self.model in [CytomatType.C2C_425]:
      return f"{str(rack_idx).zfill(2)} {str(site_idx).zfill(2)}"

    # TODO: configure all cytomats to use `rack site` format
    if self.model in [
      CytomatType.C6000,
      CytomatType.C6002,
      CytomatType.C2C_450_SHAKE,
      CytomatType.C5C,
    ]:
      slots_to_skip = sum(r.capacity for r in self._racks[:rack_idx])
      absolute_slot = slots_to_skip + site_idx + 1  # 1-indexed

      return f"{absolute_slot:03}"

    raise ValueError(f"Unsupported Cytomat model: {self.model}")

  async def get_overview_register(self) -> OverviewRegisterState:
    # Sometimes this command is not recognized and it is not known why. We will retry a few times
    # We don't care if the cytomat is still busy, that is actually what we are often checking for.
    # We are just gathering state, so just try a little bit later.
    num_tries = 10
    for _ in range(num_tries):
      try:
        resp = await self.send_command("ch", "bs", "")
      except (CytomatCommandUnknownError, CytomatBusyError):
        await asyncio.sleep(0.1)
        continue
      return OverviewRegisterState.from_resp(resp)
    await self.reset_error_register()
    raise CytomatCommandUnknownError("Could not get overview register")

  async def get_warning_register(self) -> WarningRegister:
    hex_value = await self.send_command("ch", "bw", "")
    for member in WarningRegister:
      if hex_value == member.value:
        return member

    await self.reset_error_register()
    raise Exception(f"Unknown warning register value: {hex_value}")

  async def get_error_register(self) -> ErrorRegister:
    hex_value = await self.send_command("ch", "be", "")
    for member in ErrorRegister:
      if hex_value == member.value:
        return member

    await self.reset_error_register()
    raise Exception(f"Unknown error register value: {hex_value}")

  async def reset_error_register(self) -> None:
    await self.send_command("rs", "be", "")

  async def initialize(self) -> None:
    await self.send_action("ll", "in", "", timeout=300)  # this command sometimes times out

  async def open_door(self):
    return await self.send_action("ll", "gp", "002")

  async def close_door(self):
    return await self.send_action("ll", "gp", "001")

  async def shovel_in(self):
    return await self.send_action("ll", "sp", "001")

  async def shovel_out(self):
    return await self.send_action("ll", "sp", "002")

  async def get_action_register(self) -> ActionRegisterState:
    hex_value = await self.send_command("ch", "ba", "")
    binary_repr = hex_to_binary(hex_value)
    target, action = binary_repr[:3], binary_repr[3:]

    target_enum = None
    for action_type_member in ActionType:
      if int(target, 2) == int(action_type_member.value, 16):
        target_enum = action_type_member
        break
    assert target_enum is not None, f"Unknown target value: {target}"

    action_enum = None
    for action_register_member in ActionRegister:
      if int(action, base=2) == int(action_register_member.value, base=16):
        action_enum = action_register_member
        break
    assert action_enum is not None, f"Unknown value: {action}"

    return ActionRegisterState(target=target_enum, action=action_enum)

  async def get_swap_register(self) -> SwapStationState:
    value = await self.send_command("ch", "sw", "")
    return SwapStationState(
      position=SwapStationPosition(int(value[0])),
      load_status_front_of_gate=LoadStatusFrontOfGate(int(value[1])),
      load_status_at_processor=LoadStatusAtProcessor(int(value[2])),
    )

  async def get_sensor_register(self) -> SensorStates:
    hex_value = await self.send_command("ch", "ts", "")
    binary_values = hex_to_base_twelve(hex_value)
    return SensorStates(
      **{member.name: bool(int(binary_values[member.value])) for member in SensorRegister}
    )

  async def action_transfer_to_storage(  # used by insert_plate
    self, site: PlateHolder
  ) -> OverviewRegisterState:
    """Open lift door, retrieve from transfer, close door, place at storage"""
    return await self.send_action("mv", "ts", self._site_to_firmware_string(site), timeout=120)

  async def action_storage_to_transfer(  # used by retrieve_plate
    self, site: PlateHolder
  ) -> OverviewRegisterState:
    """Retrieve from storage, open door, move to transfer, close door"""
    return await self.send_action("mv", "st", self._site_to_firmware_string(site))

  async def action_storage_to_wait(self, site: PlateHolder) -> OverviewRegisterState:
    """Retrieve from storage, move to wait position"""
    return await self.send_action("mv", "sw", self._site_to_firmware_string(site))

  async def action_wait_to_storage(self, site: PlateHolder) -> OverviewRegisterState:
    """Move from wait to storage, unload, return to wait"""
    return await self.send_action("mv", "ws", self._site_to_firmware_string(site))

  async def action_wait_to_transfer(self) -> OverviewRegisterState:
    """Open door, place on transfer, return to wait, close door"""
    return await self.send_action("mv", "wt", "")

  async def action_transfer_to_wait(self) -> OverviewRegisterState:
    """Open door, retrieve from transfer, return to wait, close door"""
    return await self.send_action("mv", "tw", "")

  async def action_wait_to_exposed(self) -> OverviewRegisterState:
    """Move from wait to exposed position outside device"""
    return await self.send_action("mv", "wh", "")

  async def action_exposed_to_wait(self) -> OverviewRegisterState:
    """Return to wait from exposed, close door"""
    return await self.send_action("mv", "hw", "")

  async def action_exposed_to_storage(self, site: PlateHolder) -> OverviewRegisterState:
    """Return with MTP from exposed to storage, move to wait, close door"""
    return await self.send_action("mv", "hs", self._site_to_firmware_string(site))

  async def action_storage_to_exposed(self, site: PlateHolder) -> OverviewRegisterState:
    """Move from wait to storage, load MTP, transport to exposed"""
    return await self.send_action("mv", "sh", self._site_to_firmware_string(site))

  async def action_read_barcode(
    self,
    site_number_a: str,
    site_number_b: str,
  ) -> OverviewRegisterState:
    # Read barcode of storage locations
    validate_storage_location_number(site_number_a)
    validate_storage_location_number(site_number_b)
    resp = await self.send_command("mv", "sn", f"{site_number_a} {site_number_b}")
    return OverviewRegisterState.from_resp(resp)

  async def wait_for_transfer_station(self, occupied: bool = False):
    """Wait for the transfer station to be occupied, or unoccupied."""
    while (await self.get_overview_register()).transfer_station_occupied != occupied:
      await asyncio.sleep(1)

  async def wait_for_task_completion(self, timeout=60) -> OverviewRegisterState:
    """
    Wait for the cytomat to finish the current task. This is done by checking the overview register
    until the busy bit is not set. If the cytomat is busy for too long, a TimeoutError is raised.
    If the error bit is set in the overview register, the error register is read and the corresponding
    error is raised.
    """
    start = time.time()
    while True:
      overview_register = await self.get_overview_register()
      if not overview_register.busy_bit_set:
        # only check for errors once the cytomat is done, so that the user has the chance to
        # handle the error and proceed if desired.
        if overview_register.error_register_set:
          error_register = await self.get_error_register()
          await self.reset_error_register()
          raise error_register_map[error_register]
        return overview_register
      await asyncio.sleep(1)
      if time.time() - start > timeout:
        raise TimeoutError("Cytomat did not complete task in time")

  async def init_shakers(self):
    return hex_to_binary(await self.send_command("ll", "vi", ""))

  async def start_shaking(self, frequency: float, shakers: Optional[List[int]] = None):
    if self.model == CytomatType.C5C:
      raise NotImplementedError("Shaking is not supported on this model")
    await self.set_shaking_frequency(frequency=int(frequency), shakers=shakers)
    return hex_to_binary(await self.send_command("ll", "va", ""))

  async def stop_shaking(self):
    if self.model == CytomatType.C5C:
      raise NotImplementedError("Shaking is not supported on this model")
    return hex_to_binary(await self.send_command("ll", "vd", ""))

  async def set_shaking_frequency(
    self, frequency: int, shakers: Optional[List[int]] = None
  ) -> List[str]:
    shakers = shakers or [1, 2]
    assert all(shaker in [1, 2] for shaker in shakers), "Shaker index must be 1 or 2"
    return [await self.send_command("se", f"pb 2{idx - 1}", f"{frequency:04}") for idx in shakers]

  async def get_incubation_query(
    self, query: Literal["ic", "ih", "io", "it"]
  ) -> CytomatIncupationResponse:
    resp = await self.send_command("ch", query, "")
    nominal, actual = resp.split()
    return CytomatIncupationResponse(
      nominal_value=float(nominal.lstrip("+")), actual_value=float(actual.lstrip("+"))
    )

  async def get_co2(self) -> CytomatIncupationResponse:
    return await self.get_incubation_query("ic")

  async def get_humidity(self) -> CytomatIncupationResponse:
    return await self.get_incubation_query("ih")

  async def get_o2(self) -> CytomatIncupationResponse:
    return await self.get_incubation_query("io")

  async def get_temperature(self) -> float:
    return (await self.get_incubation_query("it")).actual_value

  async def fetch_plate_to_loading_tray(self, plate: Plate):
    site = plate.parent
    assert isinstance(site, PlateHolder)
    await self.action_storage_to_transfer(site)

  async def take_in_plate(self, plate: Plate, site: PlateHolder):
    await self.action_transfer_to_storage(site)

  async def set_temperature(self, *args, **kwargs):
    raise NotImplementedError("Temperature control is not implemented yet")

  def serialize(self) -> dict:
    return {
      **IncubatorBackend.serialize(self),
      "model": self.model.value,
      "port": self.io.port,
    }


class CytomatChatterbox(CytomatBackend):
  async def setup(self):
    await self.wait_for_task_completion()

  async def stop(self):
    print("closing connection to cytomat")

  async def send_command(self, command_type, command, params):
    print(
      "cytomat", self._assemble_command(command_type=command_type, command=command, params=params)
    )
    if command_type == "ch":
      return "0"
    return "0" * 8

  async def wait_for_transfer_station(self, occupied: bool = False):
    # send the command, but don't wait when we are in chatting mode.
    _ = await self.get_overview_register()


# Deprecated alias with warning # TODO: remove mid May 2025 (giving people 1 month to update)
# https://github.com/PyLabRobot/pylabrobot/issues/466


class Cytomat:
  def __init__(self, *args, **kwargs):
    raise RuntimeError("`Cytomat` is deprecated. Please use `CytomatBackend` instead. ")
