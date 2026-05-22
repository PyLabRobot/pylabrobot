"""Agile 7612 Bravo command structs — AgileMoveInfo with u16 home_complete_register."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from pylabrobot.liquid_handling.backends.agilent.bravo.types import Axis


@dataclass
class Agile7612MoveInfo:
    """Move command payload for Agile 7612 Bravo CMD_PREPARE_MOVE.

    Identical to AgileMoveInfo except home_complete_register is u16 (17 bytes
    total) instead of u32 (19 bytes).
    """
    axis: Axis
    position: float
    velocity: float
    acceleration: float
    absolute_move: bool = True
    check_for_homed: bool = True
    home_complete_register: int = 0

    _PACK_FORMAT = "<Bfff BB H"  # 17 bytes

    def pack(self) -> bytes:
        return struct.pack(
            self._PACK_FORMAT,
            int(self.axis),
            self.position,
            self.velocity,
            self.acceleration,
            1 if self.absolute_move else 0,
            1 if self.check_for_homed else 0,
            self.home_complete_register & 0xFFFF,
        )

    @classmethod
    def unpack(cls, data: bytes) -> Agile7612MoveInfo:
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
