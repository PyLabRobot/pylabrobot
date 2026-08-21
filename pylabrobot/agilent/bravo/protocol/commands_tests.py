import unittest
from typing import List, Tuple

from pylabrobot.agilent.bravo.protocol.commands import (
  DEFAULT_COMMAND_TIMEOUT,
  AgileJogInfo,
  AgileMoveInfo,
  GripperParams,
  LightCommandData,
  SmartHeadEEPROMData,
)
from pylabrobot.agilent.bravo.types import Axis, LightColor, LightCommand


class AgileMoveInfoTests(unittest.TestCase):
  def test_pack_length(self):
    info = AgileMoveInfo(axis="x", position=100.0, velocity=1.0, acceleration=0.5)
    self.assertEqual(len(info.pack()), 19)

  def test_roundtrip(self):
    info = AgileMoveInfo(
      axis="z",
      position=1000.0,
      velocity=50.0,
      acceleration=100.0,
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=0x0160,
    )
    restored = AgileMoveInfo.unpack(info.pack())
    self.assertEqual(restored.axis, "z")
    self.assertAlmostEqual(restored.position, 1000.0, places=2)
    self.assertTrue(restored.absolute_move)
    self.assertEqual(restored.home_complete_register, 0x0160)

  def test_axis_byte_matches_wire_code_table(self):
    # x=0, y=1, z=2, w=3, g=4, zg=5 -- see types._AXIS_CODES.
    cases: List[Tuple[Axis, int]] = [
      ("x", 0),
      ("y", 1),
      ("z", 2),
      ("w", 3),
      ("g", 4),
      ("zg", 5),
    ]
    for axis, code in cases:
      info = AgileMoveInfo(axis=axis, position=0.0, velocity=0.0, acceleration=0.0)
      self.assertEqual(info.pack()[0], code)


class AgileJogInfoTests(unittest.TestCase):
  def test_roundtrip(self):
    info = AgileJogInfo(
      axis="g",
      velocity=10.0,
      acceleration=5.0,
      max_position=200.0,
      tolerance=1.5,
      peak_current=0.2,
    )
    restored = AgileJogInfo.unpack(info.pack())
    self.assertEqual(restored.axis, "g")
    self.assertAlmostEqual(restored.velocity, 10.0, places=3)
    self.assertAlmostEqual(restored.peak_current, 0.2, places=3)


class LightCommandDataTests(unittest.TestCase):
  def test_pack_unpack_roundtrip(self):
    cmd = LightCommandData(
      light=LightColor.RED | LightColor.GREEN,
      period_ms=500,
      duty_cycle=0.5,
    )
    restored = LightCommandData.unpack(cmd.pack())
    self.assertTrue(restored.light & LightColor.RED)
    self.assertTrue(restored.light & LightColor.GREEN)
    self.assertEqual(restored.period_ms, 500)
    self.assertAlmostEqual(restored.duty_cycle, 0.5, places=3)

  def test_from_light_command_converts_seconds_to_milliseconds(self):
    # A 0.5s period must survive as period_ms=500, not int(0.5)=0, which the
    # firmware would read as "solid" instead of blinking.
    command = LightCommand(color=LightColor.BLUE, period=0.5, duty_cycle=1.0)
    data = LightCommandData.from_light_command(command)
    self.assertEqual(data.period_ms, 500)

  def test_period_half_second_survives_full_roundtrip(self):
    command = LightCommand(color=LightColor.BLUE, period=0.5, duty_cycle=0.75)
    packed = LightCommandData.from_light_command(command).pack()
    unpacked = LightCommandData.unpack(packed)
    recovered = unpacked.to_light_command()
    self.assertEqual(recovered.period, 0.5)
    self.assertNotEqual(recovered.period, 0.0)
    self.assertAlmostEqual(recovered.duty_cycle, 0.75, places=3)

  def test_zero_period_means_solid(self):
    command = LightCommand(color=LightColor.RED, period=0.0)
    data = LightCommandData.from_light_command(command)
    self.assertEqual(data.period_ms, 0)


class GripperParamsTests(unittest.TestCase):
  def test_roundtrip(self):
    params = GripperParams(
      grip_current=0.3,
      grip_velocity=10.0,
      grip_acceleration=5.0,
      target_position=2.0,
      position_tolerance=0.1,
      max_gripper_current=0.5,
      original_max_pos_error=0.2,
      original_velocity=20.0,
      original_acceleration=15.0,
      ticks_per_eng_unit=944.88,
    )
    restored = GripperParams.unpack(params.pack())
    self.assertAlmostEqual(restored.grip_current, 0.3, places=3)
    self.assertAlmostEqual(restored.ticks_per_eng_unit, 944.88, places=1)


class SmartHeadEEPROMDataTests(unittest.TestCase):
  def test_roundtrip(self):
    eeprom = SmartHeadEEPROMData(address=0x01, length=1, data=b"\x03")
    restored = SmartHeadEEPROMData.unpack(eeprom.pack())
    self.assertEqual(restored.address, 0x01)
    self.assertEqual(restored.length, 1)
    self.assertEqual(restored.data, b"\x03")

  def test_data_padded_to_five_bytes_on_pack(self):
    eeprom = SmartHeadEEPROMData(address=0x1C, length=3, data=b"abc")
    self.assertEqual(len(eeprom.pack()), 7)


class DefaultCommandTimeoutTests(unittest.TestCase):
  def test_is_in_seconds(self):
    # DEFAULT_COMMAND_TIMEOUT is expressed in seconds, not milliseconds.
    self.assertEqual(DEFAULT_COMMAND_TIMEOUT, 2.0)


if __name__ == "__main__":
  unittest.main()
