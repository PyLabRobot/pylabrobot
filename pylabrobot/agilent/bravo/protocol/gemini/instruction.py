"""Gemini 4-word instruction codec.

An instruction is the encoding a Darwin axis controller executes for motion,
a timed delay, or a tip-related action. It is loaded onto a controller-tree
node via a multipacket batch: one ``INSTR_NEW_INSTR`` write followed by four
``INSTR_TBL_VAL`` writes (one per word), plus ``START_EVT``/``SEND_EVT``
writes to bind the instruction to the trigger events that start and
report it.

Word layout::

    Word 0:
      bits 0-7    instr_type        (InstructionTypes)
      bits 8-23   velocity_scaled   uint16, velocity_pct/100.0 * 65535
                                     (if is_low_velocity, value = velocity_pct*1000/100 * 65535)
      bits 24-31  acceleration_scaled  uint8, accel_pct/100.0 * 255 (min 1 if accel>0)

    Word 1:
      bits 0-7    jerk_scaled       uint8, jerk_pct/100.0 * 255
      bits 8-15   force_scaled      uint8, force_pct/100.0 * 255
      bit  16     direction         1=Positive, 0=Negative
      bit  17     reset_pos_on_start
      bit  18     reset_pos_after_stop
      bit  19     error_on_dest_reach
      bit  20     lld
      bit  21     stop_on_touch
      bit  22     check_for_clots
      bit  24     is_low_velocity   (velocity_pct < 0.1 encoding flag)

    Word 2 (to_value): raw uint32 -- interpretation depends on instr_type.
        MOVE_TO/MOVE_BY:  IEEE 754 float (normalized target position or volume)
        CMOVE_TO:         low u16 = pt_data_id, high u16 = pt_data_count
        DELAY:            delay in milliseconds

    Word 3 (trig_at_value): raw uint32 -- typically a trigger-point float, or
        for plunger instructions: low u16 = plunger_speed, bits 16-23 =
        plunger_accel, bits 24-31 = plunger_jerk.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Union

from .enums import AxisDirection, InstructionTypes

_FLOAT32 = struct.Struct("<f")
_UINT32 = struct.Struct("<I")

# Word1 flag bits
_BIT_DIRECTION = 1 << 16
_BIT_RESET_POS_ON_START = 1 << 17
_BIT_RESET_POS_AFTER_STOP = 1 << 18
_BIT_ERROR_ON_DEST_REACH = 1 << 19
_BIT_LLD = 1 << 20
_BIT_STOP_ON_TOUCH = 1 << 21
_BIT_CHECK_FOR_CLOTS = 1 << 22
_BIT_LOW_VELOCITY = 1 << 24


def pack_float32(value: float) -> int:
  """Pack an IEEE 754 single-precision float into a uint32 for wire encoding.

  Args:
    value: The float to encode.

  Returns:
    The float's bit pattern as an unsigned 32-bit integer.
  """
  (word,) = _UINT32.unpack(_FLOAT32.pack(value))
  return int(word)


def unpack_float32(word: int) -> float:
  """Unpack a wire uint32 into an IEEE 754 single-precision float.

  Args:
    word: The 32-bit value as sent or received on the wire.

  Returns:
    The decoded float.
  """
  (value,) = _FLOAT32.unpack(_UINT32.pack(word & 0xFFFFFFFF))
  return float(value)


@dataclass
class Instruction:
  """A four-word motion/logic instruction, independent of wire framing.

  Motion percentages are 0-100 (percent of the axis's configured maximum).
  Velocities below 0.1% engage the "low velocity" encoding: the percentage is
  stored pre-multiplied by 1000, with a flag bit set so the controller knows
  to divide it back out.

  Attributes:
    instr_type: What kind of instruction this is. Decoding preserves a wire
      value the firmware sent that does not match a known
      :class:`~.enums.InstructionTypes` member as a plain ``int`` rather than
      raising, so an unrecognized instruction can still round-trip.
    velocity_percent: Move velocity, 0-100% of axis max.
    acceleration_percent: Move acceleration, 0-100% of axis max.
    jerk_percent: Move jerk, 0-100% of axis max. The firmware rejects an
      instruction whose encoded jerk byte is 0, so values <=0 or >100 are
      clamped to 100 rather than encoded as 0.
    force_percent: Force limit, 0-100%; 0 is a valid value meaning no force
      control, unlike jerk.
    direction: Move direction.
    reset_pos_on_start: Whether to zero the position counter when the move starts.
    reset_pos_after_stop: Whether to zero the position counter when the move stops.
    error_on_dest_reach: Whether reaching the destination should raise a fault.
    lld: Whether liquid-level detection is active during this move.
    stop_on_touch: Whether to stop the move on a touch/force event.
    check_for_clots: Whether to monitor for a clot during this move.
    to_value: Word 2, raw; see the module docstring for its per-``instr_type``
      interpretation, or use :attr:`volume`/:attr:`delay_ms`.
    trig_at_value: Word 3, raw; see :attr:`trig_at_float`/:attr:`plunger_speed`.
  """

  instr_type: Union[InstructionTypes, int] = InstructionTypes.MOVE_TO
  velocity_percent: float = 100.0
  acceleration_percent: float = 100.0
  jerk_percent: float = 100.0
  force_percent: float = 0.0
  direction: AxisDirection = AxisDirection.POSITIVE
  reset_pos_on_start: bool = False
  reset_pos_after_stop: bool = False
  error_on_dest_reach: bool = False
  lld: bool = False
  stop_on_touch: bool = False
  check_for_clots: bool = False
  to_value: int = 0
  trig_at_value: int = 0

  # Preserves the exact scaled byte values a decoded instruction was built
  # from, so encode(decode(x)) reproduces x's bytes exactly even where a
  # percentage would otherwise round to a slightly different scaled byte.
  # Empty on an instruction built directly rather than decoded.
  _scaled: dict = field(default_factory=dict, repr=False, compare=False)

  # --- Word 2 / word 3 typed accessors -----------------------------------

  @property
  def volume(self) -> float:
    """Word 2 as a float32 volume or position, for MOVE_TO/MOVE_BY."""
    return unpack_float32(self.to_value)

  @volume.setter
  def volume(self, v: float) -> None:
    """Set word 2 from a float32 volume or position.

    Args:
      v: The value to encode.
    """
    self.to_value = pack_float32(v)

  @property
  def delay_ms(self) -> int:
    """Word 2 as a delay in milliseconds, for DELAY."""
    return self.to_value & 0xFFFFFFFF

  @delay_ms.setter
  def delay_ms(self, ms: int) -> None:
    """Set word 2 from a delay in milliseconds.

    Args:
      ms: The delay to encode.
    """
    self.to_value = ms & 0xFFFFFFFF

  @property
  def cmove_pt_data_id(self) -> int:
    """Word 2 low 16 bits: the CMOVE point-table data ID."""
    return self.to_value & 0xFFFF

  @property
  def cmove_pt_data_count(self) -> int:
    """Word 2 high 16 bits: the CMOVE point-table point count."""
    return (self.to_value >> 16) & 0xFFFF

  def set_cmove_pt_data(self, data_id: int, data_count: int) -> None:
    """Set word 2 from a CMOVE point-table ID and point count.

    Args:
      data_id: The point-table data ID.
      data_count: The number of points in the table.
    """
    self.to_value = ((data_count & 0xFFFF) << 16) | (data_id & 0xFFFF)

  @property
  def trig_at_float(self) -> float:
    """Word 3 as a float32 trigger position."""
    return unpack_float32(self.trig_at_value)

  @trig_at_float.setter
  def trig_at_float(self, v: float) -> None:
    """Set word 3 from a float32 trigger position.

    Args:
      v: The value to encode.
    """
    self.trig_at_value = pack_float32(v)

  @property
  def plunger_speed(self) -> int:
    """Word 3 low 16 bits: plunger speed, for plunger instructions."""
    return self.trig_at_value & 0xFFFF

  @property
  def plunger_acceleration(self) -> int:
    """Word 3 bits 16-23: plunger acceleration, for plunger instructions."""
    return (self.trig_at_value >> 16) & 0xFF

  @property
  def plunger_jerk(self) -> int:
    """Word 3 bits 24-31: plunger jerk, for plunger instructions."""
    return (self.trig_at_value >> 24) & 0xFF

  def set_plunger(self, speed: int, accel: int, jerk: int) -> None:
    """Set word 3 from plunger speed, acceleration, and jerk.

    Args:
      speed: Plunger speed, packed into the low 16 bits.
      accel: Plunger acceleration, packed into bits 16-23.
      jerk: Plunger jerk, packed into bits 24-31.
    """
    self.trig_at_value = ((jerk & 0xFF) << 24) | ((accel & 0xFF) << 16) | (speed & 0xFFFF)

  # --- 4-word codec -------------------------------------------------------

  def to_words(self) -> tuple[int, int, int, int]:
    """Encode this instruction into its four wire words.

    Prefers the scaled byte values preserved by :meth:`from_words`, when
    present, over recomputing them from the percentage fields, so that
    decoding and re-encoding an instruction reproduces its original bytes
    exactly even where a percentage rounds imperfectly.

    Returns:
      The ``(word0, word1, word2, word3)`` tuple to load via
      ``INSTR_TBL_VAL``.
    """
    if self._scaled:
      vel_scaled = self._scaled["velocity_scaled"]
      accel_scaled = self._scaled["accel_scaled"]
      jerk_scaled = self._scaled["jerk_scaled"]
      force_scaled = self._scaled["force_scaled"]
      low_vel = self._scaled["low_velocity"]
    else:
      vel_scaled, low_vel = _scale_velocity(self.velocity_percent)
      accel_scaled = _scale_accel(self.acceleration_percent)
      jerk_scaled = _scale_jerk_percent(self.jerk_percent)
      force_scaled = _scale_force_percent(self.force_percent)

    word0 = (
      (int(self.instr_type) & 0xFF) | ((vel_scaled & 0xFFFF) << 8) | ((accel_scaled & 0xFF) << 24)
    )
    word1 = (jerk_scaled & 0xFF) | ((force_scaled & 0xFF) << 8)
    if self.direction == AxisDirection.POSITIVE:
      word1 |= _BIT_DIRECTION
    if self.reset_pos_on_start:
      word1 |= _BIT_RESET_POS_ON_START
    if self.reset_pos_after_stop:
      word1 |= _BIT_RESET_POS_AFTER_STOP
    if self.error_on_dest_reach:
      word1 |= _BIT_ERROR_ON_DEST_REACH
    if self.lld:
      word1 |= _BIT_LLD
    if self.stop_on_touch:
      word1 |= _BIT_STOP_ON_TOUCH
    if self.check_for_clots:
      word1 |= _BIT_CHECK_FOR_CLOTS
    if low_vel:
      word1 |= _BIT_LOW_VELOCITY
    return (
      word0 & 0xFFFFFFFF,
      word1 & 0xFFFFFFFF,
      self.to_value & 0xFFFFFFFF,
      self.trig_at_value & 0xFFFFFFFF,
    )

  @classmethod
  def from_words(cls, w0: int, w1: int, w2: int, w3: int) -> Instruction:
    """Decode an instruction from its four wire words.

    Args:
      w0: Word 0, as read via ``INSTR_TBL_VAL``.
      w1: Word 1.
      w2: Word 2.
      w3: Word 3.

    Returns:
      The decoded instruction. Its exact scaled byte values are preserved
      internally so that :meth:`to_words` reproduces ``w0``/``w1`` exactly.
    """
    instr_type_value = w0 & 0xFF
    vel_scaled = (w0 >> 8) & 0xFFFF
    accel_scaled = (w0 >> 24) & 0xFF
    jerk_scaled = w1 & 0xFF
    force_scaled = (w1 >> 8) & 0xFF
    is_low_vel = bool(w1 & _BIT_LOW_VELOCITY)

    vel_pct = vel_scaled * 100.0 / 65535.0
    if is_low_vel:
      vel_pct *= 0.001
    accel_pct = accel_scaled * 100.0 / 255.0
    jerk_pct = jerk_scaled * 100.0 / 255.0
    force_pct = force_scaled * 100.0 / 255.0

    instr_type: Union[InstructionTypes, int]
    if instr_type_value in InstructionTypes._value2member_map_:
      instr_type = InstructionTypes(instr_type_value)
    else:
      instr_type = instr_type_value

    inst = cls(
      instr_type=instr_type,
      velocity_percent=vel_pct,
      acceleration_percent=accel_pct,
      jerk_percent=jerk_pct,
      force_percent=force_pct,
      direction=AxisDirection.POSITIVE if w1 & _BIT_DIRECTION else AxisDirection.NEGATIVE,
      reset_pos_on_start=bool(w1 & _BIT_RESET_POS_ON_START),
      reset_pos_after_stop=bool(w1 & _BIT_RESET_POS_AFTER_STOP),
      error_on_dest_reach=bool(w1 & _BIT_ERROR_ON_DEST_REACH),
      lld=bool(w1 & _BIT_LLD),
      stop_on_touch=bool(w1 & _BIT_STOP_ON_TOUCH),
      check_for_clots=bool(w1 & _BIT_CHECK_FOR_CLOTS),
      to_value=w2 & 0xFFFFFFFF,
      trig_at_value=w3 & 0xFFFFFFFF,
    )
    inst._scaled = {
      "velocity_scaled": vel_scaled,
      "accel_scaled": accel_scaled,
      "jerk_scaled": jerk_scaled,
      "force_scaled": force_scaled,
      "low_velocity": is_low_vel,
    }
    return inst


def _scale_velocity(velocity_percent: float) -> tuple[int, bool]:
  """Scale a velocity percentage into word0's uint16 field.

  Args:
    velocity_percent: Velocity, 0-100% of axis max. Values outside
      ``(0, 100]`` are treated as 100%.

  Returns:
    The scaled uint16 value and whether the low-velocity flag must be set.
  """
  v = velocity_percent
  if v <= 0.0 or v > 100.0:
    v = 100.0
  if v < 0.1:
    scaled_base = v * 1000.0
    low_vel = True
  else:
    scaled_base = v
    low_vel = False
  scaled = int(scaled_base / 100.0 * 65535.0) & 0xFFFF
  return scaled, low_vel


def _scale_accel(accel_percent: float) -> int:
  """Scale an acceleration percentage into word0's uint8 field.

  Args:
    accel_percent: Acceleration, 0-100% of axis max. Values outside
      ``(0, 100]`` are treated as 100%.

  Returns:
    The scaled uint8 value, floored at 1 whenever the input is positive.
  """
  a = accel_percent
  if a <= 0.0 or a > 100.0:
    a = 100.0
  scaled = int(a / 100.0 * 255.0)
  if scaled == 0 and a > 0.0:
    scaled = 1
  return scaled & 0xFF


def _scale_jerk_percent(percent: float) -> int:
  """Scale a jerk percentage into word1's low uint8 field.

  Values <=0 or >100 are treated as 100%, unlike :func:`_scale_force_percent`
  where 0 is a valid, meaningful value. The firmware rejects an instruction
  whose encoded jerk byte is 0 as out of range, so this clamp is load-bearing.

  Args:
    percent: Jerk, 0-100% of axis max.

  Returns:
    The scaled uint8 value.
  """
  p = percent
  if p <= 0.0 or p > 100.0:
    p = 100.0
  return int(p / 100.0 * 255.0) & 0xFF


def _scale_force_percent(percent: float) -> int:
  """Scale a force percentage into word1's second uint8 field.

  Unlike jerk, 0 is valid here and means no force control.

  Args:
    percent: Force limit, clamped to 0-100%.

  Returns:
    The scaled uint8 value.
  """
  p = percent
  if p < 0.0:
    p = 0.0
  elif p > 100.0:
    p = 100.0
  return int(p / 100.0 * 255.0) & 0xFF
