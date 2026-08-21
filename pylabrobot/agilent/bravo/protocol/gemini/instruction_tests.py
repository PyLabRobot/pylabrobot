import unittest

from pylabrobot.agilent.bravo.protocol.gemini.enums import AxisDirection, InstructionTypes
from pylabrobot.agilent.bravo.protocol.gemini.instruction import (
  Instruction,
  pack_float32,
  unpack_float32,
)


class Float32CodecTests(unittest.TestCase):
  def test_roundtrip(self):
    for value in (0.0, 1.0, -1.0, 3.14159, -273.15, 1e10, -1e-10):
      self.assertAlmostEqual(unpack_float32(pack_float32(value)), value, places=4)

  def test_known_value(self):
    # 1.0 as IEEE-754 single precision, little-endian.
    self.assertEqual(pack_float32(1.0), 0x3F800000)
    self.assertAlmostEqual(unpack_float32(0x3F800000), 1.0)


class InstructionWordRoundtripTests(unittest.TestCase):
  def test_basic_move_roundtrips(self):
    inst = Instruction(
      instr_type=InstructionTypes.MOVE_TO,
      velocity_percent=75.0,
      acceleration_percent=50.0,
      jerk_percent=100.0,
      force_percent=0.0,
      direction=AxisDirection.POSITIVE,
    )
    inst.volume = 42.5
    words = inst.to_words()
    self.assertEqual(len(words), 4)
    decoded = Instruction.from_words(*words)
    self.assertEqual(decoded.instr_type, InstructionTypes.MOVE_TO)
    self.assertAlmostEqual(decoded.velocity_percent, 75.0, places=2)
    self.assertAlmostEqual(decoded.acceleration_percent, 50.0, delta=1.0)
    self.assertEqual(decoded.direction, AxisDirection.POSITIVE)
    self.assertAlmostEqual(decoded.volume, 42.5, places=3)

  def test_decode_then_encode_is_byte_exact(self):
    # Round-tripping through from_words/to_words must reproduce the exact
    # input words, including where a percentage would otherwise re-quantize
    # to a different scaled byte.
    words = (0x0155AA03, 0x000180A1, 0x42280000, 0x00000000)
    decoded = Instruction.from_words(*words)
    self.assertEqual(decoded.to_words(), words)

  def test_low_velocity_flag_set_below_point_one_percent(self):
    inst = Instruction(velocity_percent=0.05)
    word0, word1, _, _ = inst.to_words()
    self.assertTrue(word1 & (1 << 24))
    decoded = Instruction.from_words(word0, word1, 0, 0)
    self.assertAlmostEqual(decoded.velocity_percent, 0.05, delta=0.01)

  def test_jerk_zero_clamps_to_full_scale(self):
    # The firmware rejects a jerk byte of 0 as out of range, so jerk<=0 must
    # clamp to 100%, not encode as 0.
    inst = Instruction(jerk_percent=0.0)
    _, word1, _, _ = inst.to_words()
    self.assertEqual(word1 & 0xFF, 255)

  def test_force_zero_stays_zero(self):
    # Unlike jerk, force=0 is meaningful (no force control) and must not clamp.
    inst = Instruction(force_percent=0.0)
    _, word1, _, _ = inst.to_words()
    self.assertEqual((word1 >> 8) & 0xFF, 0)

  def test_flag_bits_roundtrip(self):
    inst = Instruction(
      reset_pos_on_start=True,
      reset_pos_after_stop=True,
      error_on_dest_reach=True,
      lld=True,
      stop_on_touch=True,
      check_for_clots=True,
    )
    decoded = Instruction.from_words(*inst.to_words())
    self.assertTrue(decoded.reset_pos_on_start)
    self.assertTrue(decoded.reset_pos_after_stop)
    self.assertTrue(decoded.error_on_dest_reach)
    self.assertTrue(decoded.lld)
    self.assertTrue(decoded.stop_on_touch)
    self.assertTrue(decoded.check_for_clots)

  def test_unrecognized_instr_type_roundtrips_as_int(self):
    words = (0xFF, 0, 0, 0)
    decoded = Instruction.from_words(*words)
    self.assertEqual(decoded.instr_type, 0xFF)
    self.assertEqual(decoded.to_words(), words)


class InstructionWord2Word3AccessorTests(unittest.TestCase):
  def test_delay_ms(self):
    inst = Instruction()
    inst.delay_ms = 1500
    self.assertEqual(inst.delay_ms, 1500)
    self.assertEqual(inst.to_value, 1500)

  def test_cmove_pt_data(self):
    inst = Instruction()
    inst.set_cmove_pt_data(data_id=7, data_count=200)
    self.assertEqual(inst.cmove_pt_data_id, 7)
    self.assertEqual(inst.cmove_pt_data_count, 200)

  def test_plunger_fields(self):
    inst = Instruction()
    inst.set_plunger(speed=1000, accel=200, jerk=50)
    self.assertEqual(inst.plunger_speed, 1000)
    self.assertEqual(inst.plunger_acceleration, 200)
    self.assertEqual(inst.plunger_jerk, 50)

  def test_trig_at_float(self):
    inst = Instruction()
    inst.trig_at_float = -12.5
    self.assertAlmostEqual(inst.trig_at_float, -12.5, places=3)


if __name__ == "__main__":
  unittest.main()
