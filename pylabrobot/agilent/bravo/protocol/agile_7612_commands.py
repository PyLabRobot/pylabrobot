"""Move-command payload for the Agile 7612 Bravo generation.

Identical to :class:`~.commands.AgileMoveInfo` except its
``home_complete_register`` field is packed as a uint16 (17-byte payload total)
rather than the uint32 (19-byte payload) the legacy Agile generation uses.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..types import _AXIS_BY_CODE, Axis, axis_code


@dataclass
class Agile7612MoveInfo:
  """Move command payload for the Agile 7612 ``CMD_PREPARE_MOVE``.

  All position/velocity/acceleration values are in encoder ticks and ticks/ms.

  Attributes:
    axis: The axis this move targets.
    position: Target position (absolute) or delta (relative), in ticks.
    velocity: Move velocity, in ticks/ms.
    acceleration: Move acceleration, in ticks/ms^2.
    absolute_move: Whether ``position`` is absolute rather than relative.
    check_for_homed: Whether the controller should refuse the move if the
      axis has not been homed.
    home_complete_register: The Agile register whose value confirms this
      axis's home flag, packed as a uint16.
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
    """Pack this move command into its 17-byte wire encoding.

    Returns:
      The packed payload.
    """
    return struct.pack(
      self._PACK_FORMAT,
      axis_code(self.axis),
      self.position,
      self.velocity,
      self.acceleration,
      1 if self.absolute_move else 0,
      1 if self.check_for_homed else 0,
      self.home_complete_register & 0xFFFF,
    )

  @classmethod
  def unpack(cls, data: bytes) -> Agile7612MoveInfo:
    """Unpack a move command from its 17-byte wire encoding.

    Args:
      data: At least 17 bytes, payload first.

    Returns:
      The decoded move command.
    """
    axis_val, pos, vel, accel, abs_move, check_homed, home_reg = struct.unpack(
      cls._PACK_FORMAT, data[: struct.calcsize(cls._PACK_FORMAT)]
    )
    return cls(
      axis=_AXIS_BY_CODE[axis_val],
      position=pos,
      velocity=vel,
      acceleration=accel,
      absolute_move=bool(abs_move),
      check_for_homed=bool(check_homed),
      home_complete_register=home_reg,
    )
