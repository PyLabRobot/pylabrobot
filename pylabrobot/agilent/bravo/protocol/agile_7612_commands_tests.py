import struct
import unittest

from pylabrobot.agilent.bravo.protocol.agile_7612_commands import Agile7612MoveInfo
from pylabrobot.agilent.bravo.protocol.commands import AgileMoveInfo


class Agile7612MoveInfoTests(unittest.TestCase):
  def test_pack_length_is_seventeen_bytes(self):
    info = Agile7612MoveInfo(axis="x", position=100.0, velocity=1.0, acceleration=0.5)
    self.assertEqual(len(info.pack()), 17)

  def test_standard_move_info_is_nineteen_bytes(self):
    # The Agile 7612 struct differs from the legacy Agile struct only in
    # home_complete_register's width (u16 vs u32) -- 17 bytes vs 19.
    info = AgileMoveInfo(axis="x", position=100.0, velocity=1.0, acceleration=0.5)
    self.assertEqual(len(info.pack()), 19)

  def test_z_home_matches_capture(self):
    # Captured Agile 7612 Z-axis homing PREPARE_MOVE payload; every byte is
    # pinned against the real wire capture.
    info = Agile7612MoveInfo(
      axis="z",
      position=0.0,
      velocity=200.0,
      acceleration=0.6,
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=0x0160,
    )
    expected = bytes.fromhex("0200000000000048439a99193f01016001")
    self.assertEqual(info.pack(), expected)

  def test_x_relative_jog_field_layout(self):
    info = Agile7612MoveInfo(
      axis="x",
      position=1574.8,
      velocity=74.96,
      acceleration=0.4,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=0x015E,
    )
    packed = info.pack()
    self.assertEqual(len(packed), 17)
    self.assertEqual(packed[0], 0)  # axis x = 0
    self.assertEqual(packed[13], 0)  # absolute_move = False
    self.assertEqual(packed[14], 0)  # check_for_homed = False
    self.assertEqual(struct.unpack_from("<H", packed, 15)[0], 0x015E)

  def test_home_register_is_u16(self):
    info = Agile7612MoveInfo(
      axis="z", position=0.0, velocity=0.0, acceleration=0.0, home_complete_register=0x0160
    )
    packed = info.pack()
    home_reg = struct.unpack_from("<H", packed, 15)[0]
    self.assertEqual(home_reg, 0x0160)

  def test_unpack_roundtrip(self):
    info = Agile7612MoveInfo(
      axis="y",
      position=3149.6,
      velocity=15.748,
      acceleration=0.031496,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=0x015F,
    )
    unpacked = Agile7612MoveInfo.unpack(info.pack())
    self.assertEqual(unpacked.axis, "y")
    self.assertAlmostEqual(unpacked.position, 3149.6, delta=0.1)
    self.assertFalse(unpacked.absolute_move)
    self.assertEqual(unpacked.home_complete_register, 0x015F)


if __name__ == "__main__":
  unittest.main()
