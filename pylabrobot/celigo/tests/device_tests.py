"""Tests for the high-level Celigo facade (against the mock board)."""

import unittest

from pylabrobot.celigo.config import (
  AxisConfig,
  CalibrationConfig,
  CeligoHardwareConfig,
  HardwareDefaultConfig,
)
from pylabrobot.celigo.demo import MockBoard
from pylabrobot.celigo.device import Celigo, Y_AXIS
from pylabrobot.celigo.navigation import CORNING_3603_96, well_to_encoder_ticks


def _celigo():
  calib = CalibrationConfig(
    microns_per_pixel_x=1.05456,
    microns_per_pixel_y=1.05444,
    image_width_pixels=2048,
    image_height_pixels=2048,
    stage_x_scale=1.0,
    stage_y_scale=1.0,
  )
  hw = HardwareDefaultConfig(
    default_plate_x_corner_stage_coordinate=2.159,
    default_plate_y_corner_stage_coordinate=3.492,
  )
  hwcfg = CeligoHardwareConfig(
    x_axis=AxisConfig(motion_name="X", mm_per_encoder_tick=0.0127, home_offset=-18.0),
    y_axis=AxisConfig(
      motion_name="Y", mm_per_encoder_tick=0.0127, home_offset=71.75, invert_axis_direction=True
    ),
  )
  return Celigo(
    transport=MockBoard(),
    calibration=calib,
    hardware_defaults=hw,
    hardware_config=hwcfg,
    plate=CORNING_3603_96,
  )


class TestFacade(unittest.TestCase):
  def test_setup_builds_coords_and_reads_motors(self):
    cel = _celigo()
    cel.setup()
    self.assertIsNotNone(cel.coords)
    self.assertEqual(len(cel.motors), 4)

  def test_brightfield_on_off(self):
    cel = _celigo()
    cel.setup()
    self.assertEqual(cel.set_brightfield(True), 3276)
    self.assertEqual(cel.set_brightfield(False), 0)

  def test_move_to_well_matches_navigation(self):
    cel = _celigo()
    cel.setup()
    xt, yt = cel.move_to_well("A1")
    expect = well_to_encoder_ticks(
      CORNING_3603_96,
      "A1",
      cel.coords,
      cel.hardware_config.x_axis,
      cel.hardware_config.y_axis,
    )
    self.assertEqual((xt, yt), expect)

  def test_move_to_well_needs_config(self):
    cel = Celigo(transport=MockBoard())  # no calibration/hw config
    cel.setup()
    with self.assertRaises(RuntimeError):
      cel.move_to_well("A1")

  def test_move_z_and_encoders(self):
    cel = _celigo()
    cel.setup()
    cel.move_z(10337)  # mock returns ready; should not raise
    enc = cel.read_encoders()
    self.assertEqual(set(enc), {"x", "y", "z", "filter"})

  def test_move_z_verifies_encoder_arrival(self):
    cel = _celigo()
    cel.setup()
    self.assertEqual(cel.move_z(10337), 10337)  # returns the arrived encoder position
    self.assertEqual(cel.read_encoders()["z"], 10337)

  def test_move_to_well_updates_encoders(self):
    cel = _celigo()
    cel.setup()
    xt, yt = cel.move_to_well("A1")
    enc = cel.read_encoders()
    self.assertEqual((enc["x"], enc["y"]), (xt, yt))

  def test_move_includes_move_current(self):
    cel = _celigo()
    cel.setup()
    cel.move_z(10337)
    issued = [ez for _name, ez in cel.transport.log if ez]
    self.assertTrue(any("m50" in c and "A10337" in c for c in issued))  # Z move current set

  def test_stall_raises(self):
    class _StallBoard(MockBoard):
      def _motor_reply(self, ez):  # never advance position -> looks like a stall
        data = f"0`{self.enc.get(int(ez[1]) if ez[1:2].isdigit() else 3, 0)}" if "?8" in ez else "0`"
        return ("\x02" + data + "\x03q").encode("latin-1")

    cel = Celigo(transport=_StallBoard(), stall_limit=2)
    cel.setup()
    with self.assertRaises(RuntimeError):
      cel.move_z(10337)

  def test_home_axis_resyncs(self):
    cel = _celigo()
    cel.setup()
    self.assertEqual(cel.home_axis(Y_AXIS, settle_polls=2), 0)

  def test_open_close_door_runs(self):
    cel = _celigo()
    cel.setup()
    cel.open_door()
    cel.close_door()
    # the captured signatures show up in issued motor commands
    motor_cmds = [ez for name, ez in cel.transport.log if ez]
    self.assertTrue(any("D25000R" in c for c in motor_cmds))
    self.assertTrue(any("A-136R" in c for c in motor_cmds))


if __name__ == "__main__":
  unittest.main()
