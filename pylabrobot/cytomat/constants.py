from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import NewType


BINARY_REPRESENTATION = NewType("BINARY_REPRESENTATION", str)
HEX = NewType("HEX", str)


class CommandType(Enum):
    CHECK_REGISTER = "ch"
    RESET_REGISTER = "rs"
    HIGH_LEVEL_COMMAND = "mv"
    LOW_LEVEL_COMMAND = "ll"
    SET_PARAMETER = "se"


class OverviewRegister(Enum):
    """
    This is returned by:
      the check_register command f"{CommandType.CHECK_REGISTER.value}{OverviewRegister.value}"
      any of the CommandType.HIGH_LEVEL_COMMAND commands
    """

    TRANSFER_STATION_OCCUPIED = 0
    DEVICE_DOOR_OPEN = 1
    AUTOMATIC_GATE_OPEN = 2
    HANDLER_OCCUPIED = 3
    ERROR_REGISTER_SET = 4
    WARNING_REGISTER_SET = 5
    READY_BIT_SET = 6
    BUSY_BIT_SET = 7


class WarningRegister(Enum):
    NO_WARNING = "00"
    COMMUNICATION_WITH_MOTOR_CONTROLLERS_INTERRUPTED = "01"
    MTP_NOT_LOADED_ON_HANDLER_SHOVEL = "02"
    MTP_NOT_UNLOADED_FROM_HANDLER_SHOVEL = "03"
    SHOVEL_NOT_EXTENDED_HANDLER_MOVEMENT_ERROR = "04"
    PROCESS_TIMEOUT = "05"
    AUTOMATIC_LIFT_DOOR_NOT_OPEN = "06"
    AUTOMATIC_LIFT_DOOR_NOT_CLOSED = "07"
    SHOVEL_NOT_RETRACTED = "08"
    INITIALIZATION_DUE_TO_OPEN_DEVICE_DOOR = "09"
    TRANSFER_STATION_NOT_ROTATED = "0C"
    OTHER_MOTOR_FAULT_INIT_PHYTRON = "0D"
    REINITIALIZATION_CAROUSEL = "0E"


class ErrorRegister(Enum):
    NO_ERROR = "00"
    COMMUNICATION_WITH_MOTOR_CONTROLLERS_INTERRUPTED = "01"
    NO_MTP_LOADED_ON_HANDLER_SHOVEL = "02"
    NO_MTP_UNLOADED_FROM_HANDLER_SHOVEL = "03"
    SHOVEL_NOT_EXTENDED_AUTOMATIC_UNIT_POSITION_ERROR = "04"
    PROCESS_TIMEOUT = "05"
    AUTOMATIC_LIFT_DOOR_NOT_OPEN = "06"
    AUTOMATIC_LIFT_DOOR_NOT_CLOSED = "07"
    SHOVEL_NOT_RETRACTED = "08"
    STEPPER_MOTOR_CONTROLLER_TEMPERATURE_TOO_HIGH = "0A"
    OTHER_STEPPER_MOTOR_CONTROLLER_ERROR = "0B"
    TRANSFER_STATION_NOT_ROTATED = "0C"
    COMMUNICATION_WITH_HEATING_CONTROLLERS_AND_GAS_SUPPLY_DISTURBED = "0D"
    FATAL_ERROR_OCCURRED_DURING_ERROR_ROUTINE = "FF"


class ActionRegister(Enum):
    MOVEMENT_HEIGHT_MOTOR_TO_STORAGE_MINUS_OFFSET = "01"
    QUERY_HEIGHT_POSITION_REACHED_MINUS_OFFSET = "02"
    MOVEMENT_HEIGHT_MOTOR_TO_STORAGE_PLUS_OFFSET = "03"
    QUERY_HEIGHT_POSITION_REACHED_PLUS_OFFSET = "04"
    MOVEMENT_ROTATION_MOTOR_TO_STORAGE_LOCATION = "05"
    QUERY_ROTATION_POSITION_REACHED = "06"
    MOVEMENT_EXTEND_SHOVEL = "07"
    QUERY_SHOVEL_EXTENDED = "08"
    QUERY_SHOVEL_EXTENSION_LIMIT_SWITCH = "09"
    MOVEMENT_RETRACT_SHOVEL = "0A"
    QUERY_SHOVEL_RETRACTED = "0B"
    CLOSE_AUTOMATIC_LIFT_DOOR = "0C"
    QUERY_AUTOMATIC_LIFT_DOOR_CLOSED = "0D"
    OPEN_AUTOMATIC_LIFT_DOOR = "0E"
    QUERY_AUTOMATIC_LIFT_DOOR_OPEN = "0F"
    TRANSFER_STATION_IN_POSITION_1 = "10"
    QUERY_TRANSFER_STATION_IN_POSITION_1 = "11"
    TRANSFER_STATION_IN_POSITION_2 = "12"
    QUERY_TRANSFER_STATION_IN_POSITION_2 = "13"
    CHECK_MTP_ON_SHOVEL = "14"
    CHECK_MTP_ON_TRANSFER_STATION = "15"
    MOVEMENT_TURNTABLE = "19"
    TURNTABLE_POSITION_QUERY = "1A"
    TURNTABLE_INIT_MOVEMENT = "1B"
    TURNTABLE_INIT_POSITION_QUERY = "1C"
    PARAMETER_SET_FOR_MOTOR_CONTROLLER_CHANGED = "1D"


class ActionType(Enum):
    INIT_POSITION = "01"
    WAIT_POSITION = "02"
    STACKER = "03"
    TRANSFER_STATION = "04"


class SwapStationPosition(IntEnum):
    PLATE_1_IN_FRONT_OF_AUTOMATIC_GATE = 1
    PLATE_2_IN_FRONT_OF_AUTOMATIC_GATE = 2


class LoadStatusFrontOfGate(IntEnum):
    EMPTY = 0
    OCCUPIED = 1  # Occupied (microtiter plate loaded)


class LoadStatusAtProcessor(IntEnum):
    EMPTY = 0
    OCCUPIED = 1  # Occupied (microtiter plate loaded)


class SensorRegister(IntEnum):
    INIT_SENSOR_HEIGHT_MOTOR = 0
    INIT_SENSOR_CAROUSEL = 1
    SHOVEL_RETRACTED = 2
    SHOVEL_EXTENDED = 3
    SHOVEL_OCCUPIED = 4
    GATE_OPENED = 5
    GATE_CLOSED = 6
    TRANSFER_STATION_OCCUPIED = 7
    TRANSFER_STATION_POSITION_1 = 8
    TRANSFER_STATION_POSITION_2 = 9
    INNER_DOOR_OPENED = 10
    CAROUSEL_POSITION = 11
    HANDLER_POSITIONED_TOWARDS_STACKER = 12
    HANDLER_POSITIONED_TOWARDS_GATE = 13
    TRANSFER_STATION_SECOND_PLATE_OCCUPIED = 14


class CytomatRegisterType(Enum):
    OVERVIEW = "bs"
    WARNING = "bw"
    ERROR = "be"
    ACTION = "ba"
    SWAP = "sw"
    SENSOR = "ts"


class CytomatComplexCommand(Enum):
    TRANSFER_TO_STORAGE = "ts"  # Open lift door, retrieve from transfer, close door, place at storage
    STORAGE_TO_TRANSFER = "st"  # Retrieve from storage, open door, move to transfer, close door
    STORAGE_TO_WAIT = "sw"  # Retrieve from storage, move to wait position
    WAIT_TO_STORAGE = "ws"  # Move from wait to storage, unload, return to wait
    WAIT_TO_TRANSFER = "wt"  # Open door, place on transfer, return to wait, close door
    TRANSFER_TO_WAIT = "tw"  # Open door, retrieve from transfer, return to wait, close door
    WAIT_TO_EXPOSED = "wh"  # Move from wait to exposed position outside device
    EXPOSED_TO_WAIT = "hw"  # Return to wait from exposed, close door
    EXPOSED_TO_STORAGE = "hs"  # Return with MTP from exposed to storage, move to wait, close door
    STORAGE_TO_EXPOSED = "sh"  # Move from wait to storage, load MTP, transport to exposed
    READ_BARCODE = "sn"  # Read barcode of storage locations
    INITIALIZE_SHAKERS = "vi"
    START_SHAKING = "va"
    STOP_SHAKING = "vd"
    # SHAKER_OPEN_CLAMPS = "xa"
    # SHAKER_CLOSE_CLAMPS = "xd"
    SET_FREQUENCY_TOS_1 = "pb 20"
    SET_FREQUENCY_TOS_2 = "pb 21"


class CytomatLowLevelCommand(Enum):
    INITIALIZE = "in"
    AUTOMATIC_GATE = "gp"


class CytomatIncubationQuery(Enum):
    CO2 = "ic"
    HUMIDITY = "ih"
    O2 = "io"
    TEMP = "it"


@dataclass(frozen=True)
class CytomatIncupationResponse:
    nominal_value: float
    actual_value: float


class CytomatActionResponse(Enum):
    OK = "ok"
    ERROR = "er"


@dataclass(frozen=True)
class CytomatRack:
    num_slots: int  # number of plate locations in rack
    pitch: int  # distance between 2 plate locations


class CytomatType(Enum):
    C6000 = "C6000"
    C6002 = "C6002"
    C2C_50 = "C2C_50"
    C2C_425 = "C2C_425"
    C2C_450_SHAKE = "C2C_450_SHAKE"
    SWIRLER = "SWIRLER" # Cytomat 5C on top of an arduino controlled swirler



@dataclass(frozen=True)
class CytomatCapability:  # to enhance protocol modularity across cytomats, here we define instrument capabilities
    incubate: bool
    cool: bool
    shake: bool


CytomatCapabilities = {
    CytomatType.C6000: CytomatCapability(True, False, False),
    CytomatType.C6002: CytomatCapability(False, True, False),
    CytomatType.C2C_50: CytomatCapability(
        True, False, False
    ),  # refers to cytomat with temp range 25-50 (incubator-only)
    CytomatType.C2C_425: CytomatCapability(False, True, False),  # refers to cytomat with temp range 4-25 (fridge-only)
    CytomatType.C2C_450_SHAKE: CytomatCapability(
        True, True, True
    ),  # refers to cytomat with temp range 4-50 & shaker plugs
    CytomatType.SWIRLER: CytomatCapability(
        True, True, True
    ),
}