import asyncio
import atexit
import json
import logging
import os
import sys
from typing import Optional, Union

import serial
import yaml
from prettytable import PrettyTable
from pylabrobot.resources.plate import Plate

from .config import CYTOMAT_CONFIG
from .constants import (
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
from .schema import (
    CytomatRackState,
    CytomatRelativeLocation,
)
from .utils import (
    DATA_PATH,
    hex_to_base_twelve,
    hex_to_binary,
    validate_storage_location_number,
)
from .errors import CytomatTelegramStructureError, error_map
from .schemas import (
    ActionRegisterState,
    CytomatPlate,
    OverviewRegisterState,
    PlatePair,
    SensorStates,
    SwapStationState,
)

logger = logging.getLogger(__name__)


class Cytomat:
    default_baud = 9600
    serial_message_encoding = "utf-8"

    def __init__(self, model: CytomatType):
        supported_models = [
            CytomatType.C6000,
            CytomatType.C6002,
            CytomatType.C2C_425,
            CytomatType.C2C_450_SHAKE,
            CytomatType.SWIRLER,
        ]
        if model not in supported_models:
            raise NotImplementedError("Only the following Cytomats are supported:", supported_models)

        self.model = model

        self.port = CYTOMAT_CONFIG[model.value]["port"]
        self.rack_cfg = CYTOMAT_CONFIG[model.value]["racks"]

        if self.model == CytomatType.SWIRLER:
            self.swirler = Swirler(CYTOMAT_CONFIG[model.value]["swirler_port"])

        self.open_serial_connection()

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
            if self.model == CytomatType.SWIRLER:
                self.swirler.open_serial_connection()

        except serial.SerialException as e:
            logger.error("Could not connect to cytomat, is it in use by a different notebook?")
            raise e

        atexit.register(self.close_connection)

    def close_connection(self):
        if self.ser.is_open:
            self.ser.close()
        if self.model == CytomatType.SWIRLER:
            self.swirler.close_connection()

    @property
    def data_path(self):
        os.makedirs(DATA_PATH, exist_ok=True)
        return os.path.join(DATA_PATH, f"cytomat_{self.model.value}.yaml")

    @property
    def rack_state(self) -> CytomatRackState:
        if not os.path.exists(self.data_path):
            rack_state = CytomatRackState.from_cytomat_type(self.model)
            self.save_state(rack_state)

        with open(self.data_path, "r") as file:
            return CytomatRackState(**yaml.load(file, Loader=yaml.FullLoader))

    def save_state(self, rack_state: CytomatRackState):
        rack_state = CytomatRackState(**rack_state.dict())

        current_state = json.loads(rack_state.json())
        with open(self.data_path, "w") as file:
            yaml.dump(current_state, file, Dumper=QuotedKeyDumper)

    def _add_plate_to_rack_state(self, location: CytomatRelativeLocation, cytomat_plate: CytomatPlate):
        rack_state = self.rack_state

        rack_index = next(
            (index for index, rack in enumerate(rack_state.racks) if rack.rack_index == location.rack),
            None,
        )
        if rack_index is None:
            raise ValueError(f"Rack {location.rack} not found")

        if rack_state.racks[rack_index].idx.get(location.slot) is not None:
            raise ValueError(f"Slot {location.slot} already contains a plate")

        rack_state.racks[rack_index].idx[location.slot] = cytomat_plate
        self.save_state(rack_state)

    def _remove_plate_from_rack_state(self, location: CytomatRelativeLocation) -> CytomatPlate:
        rack_state = self.rack_state

        list_index = next(
            (index for index, rack in enumerate(rack_state.racks) if location.rack == rack.rack_index),
            None,
        )
        if list_index is None:
            raise ValueError(f"Rack {location.rack} not found")

        retrieved_plate = rack_state.racks[list_index].idx.get(location.slot)
        if retrieved_plate is None:
            raise ValueError(f"Plate not found in slot {location}")
        rack_state.racks[list_index].idx[location.slot] = None

        self.save_state(rack_state)
        return retrieved_plate

    def find_slot_for_plate(self, plr_plate: Plate) -> CytomatRelativeLocation:
        """
        find the smallest available slot for a plate
        """

        def _plate_height(p: Plate):
            if p.has_lid():
                return p.get_size_z() + 3

            return p.get_size_z()

        filtered_sorted_racks = sorted(
            (rack for rack in self.rack_state.racks if _plate_height(plr_plate) < rack.type.pitch),
            key=lambda rack: rack.type.pitch,
        )
        if len(filtered_sorted_racks) == 0:
            raise ValueError(f"No available slot for plate with pitch {plr_plate.get_size_z()}")

        for rack in filtered_sorted_racks:
            for slot, plate in rack.idx.items():
                if plate is None:
                    return CytomatRelativeLocation(rack.rack_index, int(slot), self.model)

        raise ValueError(f"No available slot for plate with pitch {plr_plate.get_size_z()}")

    def find_plate_location(self, plate_uid: str) -> CytomatRelativeLocation:
        for rack in self.rack_state.racks:
            for slot, plate in rack.idx.items():
                if plate is not None and plate.uid == plate_uid:
                    return CytomatRelativeLocation(
                        rack=rack.rack_index,
                        slot=slot,
                        model=self.model,
                    )
        raise ValueError(f"Plate {plate_uid} not found in racks")

    def list_plates_by_prefix(self, prefix: str) -> list[str]:
        plates = []
        for rack in self.rack_state.racks:
            for _, plate in rack.idx.items():
                if plate is not None and plate.uid.startswith(prefix):
                    plates.append(plate.uid)
        return plates

    def handle_error(resp):
        error_code = int(resp[3:5])
        if error_code in error_map:
            raise error_map[error_code]

        raise Exception(f"Unknown cytomat error code in response: {resp}")

    def _get_carriage_return(self):
        if self.model == CytomatType.C2C_425:
            return "\r"
        return "\r\n"

    async def _send_cmd(
        self,
        command_type: CommandType,
        prefix: Union[CytomatRegisterType, CytomatComplexCommand],
        params: str,
        retries: int = 3,
    ) -> HEX:
        command = f"{command_type.value}:{prefix.value} {params}".strip() + self._get_carriage_return()
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
                    await self.initialize_cytomat()
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

    async def initialize_cytomat(self) -> None:
        """
        move the cytomat arm to the home position
        """
        await self._send_cmd(CommandType.LOW_LEVEL_COMMAND, CytomatLowLevelCommand.INITIALIZE, "")

    async def action_open_device_door(self):
        hex_value = await self._send_cmd(CommandType.LOW_LEVEL_COMMAND, CytomatLowLevelCommand.AUTOMATIC_GATE, "002")
        binary_value = hex_to_binary(hex_value)

        return OverviewRegisterState(
            **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
        )

    async def action_close_device_door(self):
        hex_value = await self._send_cmd(CommandType.LOW_LEVEL_COMMAND, CytomatLowLevelCommand.AUTOMATIC_GATE, "001")

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
        return SensorStates(**{member.name: bool(int(binary_values[member.value])) for member in SensorRegister})

    async def action_transfer_to_storage(self, storage_location: CytomatRelativeLocation) -> OverviewRegisterState:
        hex_value = await self._send_cmd(
            CommandType.HIGH_LEVEL_COMMAND,
            CytomatComplexCommand.TRANSFER_TO_STORAGE,
            storage_location.to_firmware_string(),
        )
        binary_value = hex_to_binary(hex_value)

        return OverviewRegisterState(
            **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
        )

    async def action_storage_to_transfer(self, storage_location: CytomatRelativeLocation) -> OverviewRegisterState:
        hex_value = await self._send_cmd(
            CommandType.HIGH_LEVEL_COMMAND,
            CytomatComplexCommand.STORAGE_TO_TRANSFER,
            storage_location.to_firmware_string(),
        )
        binary_value = hex_to_binary(hex_value)

        return OverviewRegisterState(
            **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
        )

    async def action_storage_to_wait(self, location: CytomatRelativeLocation) -> OverviewRegisterState:
        hex_value = await self._send_cmd(
            CommandType.HIGH_LEVEL_COMMAND,
            CytomatComplexCommand.STORAGE_TO_WAIT,
            location.to_firmware_string(),
        )
        binary_value = hex_to_binary(hex_value)

        return OverviewRegisterState(
            **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
        )

    async def action_wait_to_storage(self, location: CytomatRelativeLocation) -> OverviewRegisterState:
        hex_value = await self._send_cmd(
            CommandType.HIGH_LEVEL_COMMAND,
            CytomatComplexCommand.WAIT_TO_STORAGE,
            location.to_firmware_string(),
        )
        binary_value = hex_to_binary(hex_value)

        return OverviewRegisterState(
            **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
        )

    async def action_wait_to_transfer(self) -> OverviewRegisterState:
        hex_value = await self._send_cmd(CommandType.HIGH_LEVEL_COMMAND, CytomatComplexCommand.WAIT_TO_TRANSFER, "")
        binary_value = hex_to_binary(hex_value)

        return OverviewRegisterState(
            **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
        )

    async def action_transfer_to_wait(self) -> OverviewRegisterState:
        hex_value = await self._send_cmd(CommandType.HIGH_LEVEL_COMMAND, CytomatComplexCommand.TRANSFER_TO_WAIT, "")
        binary_value = hex_to_binary(hex_value)

        return OverviewRegisterState(
            **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
        )

    async def action_wait_to_exposed(self) -> OverviewRegisterState:
        hex_value = await self._send_cmd(CommandType.HIGH_LEVEL_COMMAND, CytomatComplexCommand.WAIT_TO_EXPOSED, "")
        binary_value = hex_to_binary(hex_value)

        return OverviewRegisterState(
            **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
        )

    async def action_exposed_to_wait(self) -> OverviewRegisterState:
        hex_value = await self._send_cmd(CommandType.HIGH_LEVEL_COMMAND, CytomatComplexCommand.EXPOSED_TO_WAIT, "")
        binary_value = hex_to_binary(hex_value)

        return OverviewRegisterState(
            **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
        )

    async def action_exposed_to_storage(self, location: CytomatRelativeLocation) -> OverviewRegisterState:
        hex_value = await self._send_cmd(
            CommandType.HIGH_LEVEL_COMMAND,
            CytomatComplexCommand.EXPOSED_TO_STORAGE,
            location.to_firmware_string(),
        )
        binary_value = hex_to_binary(hex_value)

        return OverviewRegisterState(
            **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
        )

    async def action_storage_to_exposed(self, location: CytomatRelativeLocation) -> OverviewRegisterState:
        hex_value = await self._send_cmd(
            CommandType.HIGH_LEVEL_COMMAND,
            CytomatComplexCommand.STORAGE_TO_EXPOSED,
            location.to_firmware_string(),
        )
        binary_value = hex_to_binary(hex_value)

        return OverviewRegisterState(
            **{member.name.lower(): binary_value[member.value] == "1" for member in OverviewRegister}
        )

    async def action_read_barcode(
        self,
        storage_location_number_a: str,
        storage_location_number_b: str,
    ) -> OverviewRegisterState:
        validate_storage_location_number(storage_location_number_a)
        validate_storage_location_number(storage_location_number_b)
        hex_value = await self._send_cmd(
            CommandType.HIGH_LEVEL_COMMAND,
            CytomatComplexCommand.READ_BARCODE,
            f"{storage_location_number_a} {storage_location_number_b}",
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

    async def wait_for_transfer_station_to_be_occupied (self):
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

    async def retrieve_plate_by_uid(self, plate_uid: str) -> CytomatPlate:
        location = self.find_plate_location(plate_uid)
        return await self.retrieve_plate(location)

    async def insert_plate_pair(self, plate_pair: PlatePair) -> CytomatRelativeLocation:
        location = self.find_slot_for_plate(plate_pair.pylabrobot)
        await self.insert_plate(plate_pair.cytomat, location)
        plate_pair.pylabrobot.unassign()
        return location

    async def insert_plate(self, cytomate_plate: CytomatPlate, cytomat_location: CytomatRelativeLocation):
        await self.wait_for_task_completion()
        await self.action_transfer_to_storage(cytomat_location)
        self._add_plate_to_rack_state(cytomat_location, cytomate_plate)

    async def retrieve_plate(self, location: CytomatRelativeLocation) -> CytomatPlate:
        await self.wait_for_task_completion()
        await self.action_storage_to_transfer(location)
        return self._remove_plate_from_rack_state(location)

    async def init_shakers(self):
        if self.model == CytomatType.SWIRLER:
            return None

        return hex_to_binary(
            await self._send_cmd(
                CommandType.LOW_LEVEL_COMMAND,
                CytomatComplexCommand.INITIALIZE_SHAKERS,
                "",
            )
        )

    async def start_shaking(self):
        await self.wait_for_task_completion()

        if self.model == CytomatType.SWIRLER:
            return await self.swirler.start_shaking()
        return hex_to_binary(
            await self._send_cmd(CommandType.LOW_LEVEL_COMMAND, CytomatComplexCommand.START_SHAKING, "")
        )

    async def stop_shaking(self):
        await self.wait_for_task_completion()

        if self.model == CytomatType.SWIRLER:
            return await self.swirler.stop_shaking()
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

    def __str__(self):
        table = PrettyTable()
        headers = [f"rack {i} p{k.type.pitch}" for i, k in enumerate(self.rack_state.racks)]
        table.field_names = headers
        table.align = "l"

        num_rows = max(rack.num_slots for rack in self.rack_cfg)

        # Fill in the table rows
        for row_num in range(num_rows):
            row = []
            for rack in self.rack_state.racks:
                slots = rack.idx
                if row_num < len(slots):
                    slot_id = list(slots.keys())[row_num]
                    plate_info = slots[slot_id] if slots[slot_id] else "-"
                    row.append(f"{slot_id}: {plate_info.uid}")
                else:
                    row.append("")
            table.add_row(row)

        return f"cytomat {self.model.value} state:\n{table}"


class QuotedKeyDumper(yaml.Dumper):
    # by default, yaml interprests strings with prepended zeros as octal numbers, so we override the default representer
    def represent_data(self, data):
        # If the data is a dictionary key, force it to be represented with quotes
        if isinstance(data, str):
            return self.represent_scalar("tag:yaml.org,2002:str", data, style='"')
        return super().represent_data(data)