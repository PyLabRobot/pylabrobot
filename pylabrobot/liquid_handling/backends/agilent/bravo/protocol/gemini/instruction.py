"""Gemini 4-word instruction encoder/decoder.

Ported from ``GeminiAPI.Communication.Core.Instruction``. An instruction is
loaded onto a device via multipacket: one ``INSTR_NEW_INSTR`` + four
``INSTR_TBL_VAL`` writes (one per uint32 word), plus ``START_EVT`` /
``SEND_EVT`` subcommands to bind it to trigger events.

Word layout::

    Word 0:
      bits 0-7    instr_type        (InstructionTypes)
      bits 8-23   velocity_scaled   uint16, velocity_pct/100.0 * 65535
                                    (if IsLowVelocity, value = velocity_pct*1000/100 * 65535)
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

    Word 2 (to_value): raw uint32 — interpretation depends on instr_type.
        MOVE_TO/MOVE_BY:  IEEE 754 float (normalized target position or volume)
        CMOVE_TO:         low u16 = pt_data_id, high u16 = pt_data_count
        DELAY:            delay in milliseconds

    Word 3 (trig_at_value): raw uint32 — typically a trigger-point float,
        or for plunger instructions: low u16 = plunger_speed,
        bits 16-23 = plunger_accel, bits 24-31 = plunger_jerk.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import (
  AxisDirection,
  InstructionTypes,
)

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
  """Pack an IEEE 754 single-precision float into a uint32 for wire encoding."""
  return _UINT32.unpack(_FLOAT32.pack(value))[0]


def unpack_float32(word: int) -> float:
  """Unpack a uint32 into an IEEE 754 single-precision float."""
  return _FLOAT32.unpack(_UINT32.pack(word & 0xFFFFFFFF))[0]


@dataclass(slots=True)
class Instruction:
  """Four-word motion/logic instruction, independent of wire framing.

  Motion percentages are 0-100 (percent of axis max). Velocities smaller than
  0.1% engage the "low velocity" encoding (stored pre-multiplied by 1000 with
  a flag bit set).
  """

  instr_type: InstructionTypes = InstructionTypes.MOVE_TO
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
  to_value: int = 0  # word2 — raw; use helpers below for typed access
  trig_at_value: int = 0  # word3 — raw; use helpers below

  # Round-trip fidelity: when decoding words, we remember the exact scaled
  # byte values so encode→decode→encode yields identical bytes even when the
  # percentage quantizes imperfectly. Empty on fresh construction.
  _scaled: dict = field(default_factory=dict, repr=False, compare=False)

  # --- Word 2 / word 3 typed accessors -----------------------------------

  @property
  def volume(self) -> float:
    """Word2 interpreted as a float32 volume/position (MOVE_TO/MOVE_BY)."""
    return unpack_float32(self.to_value)

  @volume.setter
  def volume(self, v: float) -> None:
    self.to_value = pack_float32(v)

  @property
  def delay_ms(self) -> int:
    """Word2 interpreted as milliseconds (DELAY)."""
    return self.to_value & 0xFFFFFFFF

  @delay_ms.setter
  def delay_ms(self, ms: int) -> None:
    self.to_value = ms & 0xFFFFFFFF

  @property
  def cmove_pt_data_id(self) -> int:
    return self.to_value & 0xFFFF

  @property
  def cmove_pt_data_count(self) -> int:
    return (self.to_value >> 16) & 0xFFFF

  def set_cmove_pt_data(self, data_id: int, data_count: int) -> None:
    self.to_value = ((data_count & 0xFFFF) << 16) | (data_id & 0xFFFF)

  @property
  def trig_at_float(self) -> float:
    return unpack_float32(self.trig_at_value)

  @trig_at_float.setter
  def trig_at_float(self, v: float) -> None:
    self.trig_at_value = pack_float32(v)

  @property
  def plunger_speed(self) -> int:
    return self.trig_at_value & 0xFFFF

  @property
  def plunger_acceleration(self) -> int:
    return (self.trig_at_value >> 16) & 0xFF

  @property
  def plunger_jerk(self) -> int:
    return (self.trig_at_value >> 24) & 0xFF

  def set_plunger(self, speed: int, accel: int, jerk: int) -> None:
    self.trig_at_value = ((jerk & 0xFF) << 24) | ((accel & 0xFF) << 16) | (speed & 0xFFFF)

  # --- 4-word codec -------------------------------------------------------

  def to_words(self) -> tuple[int, int, int, int]:
    # Prefer preserved scaled bytes when available (for round-trip fidelity).
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
  def from_words(cls, w0: int, w1: int, w2: int, w3: int) -> "Instruction":
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

    inst = cls(
      instr_type=InstructionTypes(instr_type_value)
      if instr_type_value in InstructionTypes._value2member_map_
      else instr_type_value,
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
    # Preserve exact scaled bytes for byte-identical re-encoding.
    inst._scaled = {
      "velocity_scaled": vel_scaled,
      "accel_scaled": accel_scaled,
      "jerk_scaled": jerk_scaled,
      "force_scaled": force_scaled,
      "low_velocity": is_low_vel,
    }
    return inst


def _scale_velocity(velocity_percent: float) -> tuple[int, bool]:
  """Return (scaled_uint16, low_velocity_flag) for word0 bits 8-23."""
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
  """Return scaled uint8 for word0 bits 24-31. Clamped; min 1 if >0."""
  a = accel_percent
  if a <= 0.0 or a > 100.0:
    a = 100.0
  scaled = int(a / 100.0 * 255.0)
  if scaled == 0 and a > 0.0:
    scaled = 1
  return scaled & 0xFF


def _scale_jerk_percent(percent: float) -> int:
  """Return scaled uint8 for jerk (word1 bits 0-7).

  Per the C# ``Instruction.Jerk`` setter: if the supplied value is <=0 or
  >100 the field is treated as 100%. This is NOT the same rule as the force
  field — there, 0 is a valid value meaning "no force control".

  The firmware rejects instructions with jerk=0 (word1 low byte = 0) as
  OUT_OF_RANGE, so preserving this clamp is essential.
  """
  p = percent
  if p <= 0.0 or p > 100.0:
    p = 100.0
  return int(p / 100.0 * 255.0) & 0xFF


def _scale_force_percent(percent: float) -> int:
  """Return scaled uint8 for force (word1 bits 8-15). 0 is valid."""
  p = percent
  if p < 0.0:
    p = 0.0
  elif p > 100.0:
    p = 100.0
  return int(p / 100.0 * 255.0) & 0xFF


def _scale_byte_percent(percent: float) -> int:
  """Deprecated alias — use _scale_jerk_percent or _scale_force_percent."""
  return _scale_force_percent(percent)
