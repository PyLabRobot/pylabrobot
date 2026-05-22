"""Bravo command IDs and binary data structures.

Ported from HomewoodCommandSet.h. All struct layouts match the C++ #pragma pack(push, 1).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from pylabrobot.liquid_handling.backends.agilent.bravo.types import Axis, LightColor


# ---------------------------------------------------------------------------
# Command IDs (PC -> Rabbit)
# ---------------------------------------------------------------------------

class CommandID(IntEnum):
    """Command IDs sent to the Bravo via the V11DeviceComm protocol."""
    QUERY_VERSION = 0x00
    # 0x01 reserved (deprecated firmware response)
    # 0x02 reserved (meta-framework)
    # 0x04-0x07 reserved (Abort/Pause/Unpause/Ignore)
    # 0x0E-0x0F reserved (protocol version query)
    PING_DEVICE = 0xA0
    DIRECT_AGILE_COMMAND = 0xA1
    PREPARE_MOVE = 0xA2
    QUERY_ROBOT_DISABLE = 0xA3
    QUERY_MOTOR_POWER = 0xA4
    CLEAR_MOTOR_POWER_FAULT = 0xA5
    GET_POSITION = 0xA6
    QUERY_STATE = 0xA7
    CLEAR_GO_BUTTON = 0xA8
    GO_BUTTON_PRESSED = 0xA9
    PREPARE_JOG = 0xAA
    STOP = 0xAB
    QUERY_JOG_STATUS = 0xAE
    SET_LIGHT = 0xB0
    CLEAR_LIGHTS = 0xB1
    DETECT_GRIPPER = 0xB2
    READ_AD_WEIGH_PAD = 0xB3
    GRIP = 0xB4
    DETECT_SMART_HEAD = 0xB5
    GET_EEPROM_DATA = 0xB6
    WRITE_EEPROM_DATA = 0xB7
    WRITE_SERIAL_NUMBER = 0xB8
    GET_SERIAL_NUMBER = 0xB9


# ---------------------------------------------------------------------------
# Agile protocol constants
# ---------------------------------------------------------------------------

AGILE_PACKET_SIZE = 10


# ---------------------------------------------------------------------------
# Binary data structures (1-byte aligned, matching C++ pragma pack)
# ---------------------------------------------------------------------------

@dataclass
class AgileMoveInfo:
    """Move command payload for CMD_PREPARE_MOVE.

    All position/velocity/acceleration values are in encoder ticks and ticks/ms.
    """
    axis: Axis
    position: float        # ticks
    velocity: float        # ticks/ms
    acceleration: float    # ticks/ms^2
    absolute_move: bool = True
    check_for_homed: bool = True
    home_complete_register: int = 0  # AS_REGISTER enum value

    # struct layout: u8 + 3*float + 2*u8 + u32 = 1 + 12 + 2 + 4 = 19 bytes
    _PACK_FORMAT = "<Bfff BB I"

    def pack(self) -> bytes:
        return struct.pack(
            self._PACK_FORMAT,
            int(self.axis),
            self.position,
            self.velocity,
            self.acceleration,
            1 if self.absolute_move else 0,
            1 if self.check_for_homed else 0,
            self.home_complete_register,
        )

    @classmethod
    def unpack(cls, data: bytes) -> AgileMoveInfo:
        axis_val, pos, vel, accel, abs_move, check_homed, home_reg = struct.unpack(
            cls._PACK_FORMAT, data[:struct.calcsize(cls._PACK_FORMAT)]
        )
        return cls(
            axis=Axis(axis_val),
            position=pos,
            velocity=vel,
            acceleration=accel,
            absolute_move=bool(abs_move),
            check_for_homed=bool(check_homed),
            home_complete_register=home_reg,
        )


@dataclass
class AgileJogInfo:
    """Jog command payload for CMD_PREPARE_JOG."""
    axis: Axis
    velocity: float        # ticks/ms
    acceleration: float    # ticks/ms^2
    max_position: float    # ticks (limit)
    tolerance: float       # ticks
    peak_current: float    # fraction of max

    _PACK_FORMAT = "<Bfffff"

    def pack(self) -> bytes:
        return struct.pack(
            self._PACK_FORMAT,
            int(self.axis),
            self.velocity,
            self.acceleration,
            self.max_position,
            self.tolerance,
            self.peak_current,
        )

    @classmethod
    def unpack(cls, data: bytes) -> AgileJogInfo:
        vals = struct.unpack(cls._PACK_FORMAT, data[:struct.calcsize(cls._PACK_FORMAT)])
        return cls(axis=Axis(vals[0]), velocity=vals[1], acceleration=vals[2],
                   max_position=vals[3], tolerance=vals[4], peak_current=vals[5])


@dataclass
class LightCommandData:
    """Light command payload for CMD_SET_LIGHT."""
    light: LightColor
    period_ms: int = 0
    duty_cycle: float = 1.0

    _PACK_FORMAT = "<BIf"  # u8 + u32 + float = 9 bytes

    def pack(self) -> bytes:
        return struct.pack(self._PACK_FORMAT, int(self.light), self.period_ms, self.duty_cycle)

    @classmethod
    def unpack(cls, data: bytes) -> LightCommandData:
        light, period, duty = struct.unpack(
            cls._PACK_FORMAT, data[:struct.calcsize(cls._PACK_FORMAT)]
        )
        return cls(light=LightColor(light), period_ms=period, duty_cycle=duty)


@dataclass
class GripperParams:
    """Gripper command payload for CMD_GRIP."""
    grip_current: float
    grip_velocity: float
    grip_acceleration: float
    target_position: float
    position_tolerance: float
    max_gripper_current: float
    original_max_pos_error: float
    original_velocity: float
    original_acceleration: float
    ticks_per_eng_unit: float

    _PACK_FORMAT = "<ffffffffff"  # 10 floats = 40 bytes

    def pack(self) -> bytes:
        return struct.pack(
            self._PACK_FORMAT,
            self.grip_current, self.grip_velocity, self.grip_acceleration,
            self.target_position, self.position_tolerance, self.max_gripper_current,
            self.original_max_pos_error, self.original_velocity,
            self.original_acceleration, self.ticks_per_eng_unit,
        )

    @classmethod
    def unpack(cls, data: bytes) -> GripperParams:
        vals = struct.unpack(cls._PACK_FORMAT, data[:struct.calcsize(cls._PACK_FORMAT)])
        return cls(*vals)


@dataclass
class SmartHeadEEPROMData:
    """EEPROM read/write payload for CMD_GET_EEPROM_DATA / CMD_WRITE_EEPROM_DATA."""
    address: int       # EEPROM address (u8)
    length: int        # 1-5 bytes
    data: bytes = b""  # up to 5 bytes

    _PACK_FORMAT = "<BB5s"  # u8 + u8 + 5 bytes = 7

    def pack(self) -> bytes:
        padded = (self.data + b"\x00" * 5)[:5]
        return struct.pack(self._PACK_FORMAT, self.address, self.length, padded)

    @classmethod
    def unpack(cls, raw: bytes) -> SmartHeadEEPROMData:
        addr, length, data_bytes = struct.unpack(
            cls._PACK_FORMAT, raw[:struct.calcsize(cls._PACK_FORMAT)]
        )
        return cls(address=addr, length=length, data=data_bytes[:length])


# ---------------------------------------------------------------------------
# Smart Head EEPROM address map
# ---------------------------------------------------------------------------

class EEPROMAddress(IntEnum):
    FIRMWARE_VERSION = 0x00
    HEAD_TYPE = 0x01
    HOMING_OFFSET = 0x02       # 2 bytes
    W_AXIS_TRAVEL = 0x04       # 5 bytes (cumulative mm)
    W_AXIS_DIR_CHANGES = 0x09  # 4 bytes
    PM_DATE = 0x0D             # 2 bytes
    W_TRAVEL_PRIOR_PM = 0x0F   # 5 bytes
    W_DIR_CHANGES_PRIOR_PM = 0x14  # 4 bytes
    TOTAL_EEPROM_WRITES = 0x18     # 3 bytes
    SERIAL_NUMBER_LENGTH = 0x1B    # 1 byte
    SERIAL_NUMBER = 0x1C           # 1-20 bytes


# ---------------------------------------------------------------------------
# Default timeouts
# ---------------------------------------------------------------------------

DEFAULT_COMMAND_TIMEOUT_MS = 2000
MAX_COMMAND_RETRIES = 5
