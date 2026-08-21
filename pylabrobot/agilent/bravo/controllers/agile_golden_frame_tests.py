"""Golden-frame tests: byte-for-byte wire output against a checked-in fixture.

``testdata/agile_golden_frames.json`` holds ``(command_id, payload_hex)``
sequences: the expected byte-level output for each scenario, captured from a
reference implementation of ``Agile7612Controller`` and ``AgileSrtController``
driven through a recording fake comm layer. Every test here drives the same
call against these controllers through an equivalent recording fake, with an
explicit ``axis_config`` chosen to configure both trees identically (real
per-axis speeds and home-sensor bitmasks, rather than each tree's own
no-profile fallback, so a mismatch here is a genuine packet-building bug and
not one of the three documented default-configuration differences), and
asserts the captured sequence matches the fixture exactly. The fixture is
checked in so a change in packet content, field order, or phase sequencing
fails immediately.

This is what actually exercises the per-axis homing routines, jog,
tip_force_jog, grip, and the underlying packet builders end to end -- unit
tests on individual helper methods do not catch a wrong byte inside a
19-command homing sequence the way a full recorded comparison does.
"""

from __future__ import annotations

import json
import struct
import time
import unittest
from pathlib import Path

from pylabrobot.agilent.bravo.axis_config import default_axis_config
from pylabrobot.agilent.bravo.controllers.agile_7612 import Agile7612Controller
from pylabrobot.agilent.bravo.controllers.agile_srt import AgileSrtController
from pylabrobot.agilent.bravo.controllers.base import AxisMoveInfo, JogParams
from pylabrobot.agilent.bravo.errors import BravoError
from pylabrobot.agilent.bravo.protocol.v11_comm_tests import BufferedTransport
from pylabrobot.agilent.bravo.types import ALL_AXES

_GOLDEN_PATH = Path(__file__).parent / "testdata" / "agile_golden_frames.json"
with open(_GOLDEN_PATH) as _f:
  GOLDEN: dict = json.load(_f)

# The per-axis fallback bitmask this port's controllers fall back to when an
# axis's home_flag_bitmask is left at its 0 default. Setting it explicitly
# here (rather than leaving it at 0) makes the source's own *direct* profile
# read produce the same on/off-sensor branching as this port's fallback, so
# the fixture's on-sensor and off-sensor scenarios are actually reachable
# and comparable in both trees.
_HOME_FLAG_BITMASK: dict = {"x": 1, "y": 2, "z": 4, "w": 8, "g": 1, "zg": 2}


def _matching_axis_config() -> dict:
  """Build an axis_config mapping that configures both trees identically.

  Every field but home_flag_bitmask already matches the fixture's fake
  profile through default_axis_config's own values (real per-axis speeds
  and ranges, the shared W ticks-per-uL constant); only the bitmask needs
  overriding away from its 0 default.
  """
  config = {}
  for axis in ALL_AXES:
    cfg = default_axis_config(axis)
    cfg.home_flag_bitmask = _HOME_FLAG_BITMASK[axis]
    config[axis] = cfg
  return config


class RecordingComm:
  """Fake comm layer: records every ``(command_id, payload_hex)`` sent.

  Response content is inert except for register 0x10 (home-sensor state)
  reads, whose on/off-sensor byte a test controls directly, and status
  reads, which always report settled so ``_agile_7612_wait_for_settled``
  returns on its first poll instead of looping.
  """

  def __init__(self) -> None:
    self.calls: list[tuple[int, str]] = []
    self.is_connected = True
    self.sensor_byte = 0xFF
    self.command_counts: dict = {}
    self.error_log: list = []

  @property
  def transport(self) -> "RecordingComm":
    return self

  def drain(self) -> int:
    return 0

  def send_command(self, command_id, data: bytes = b"", timeout: float = 2.0) -> bytes:
    self.calls.append((int(command_id), data.hex()))
    if len(data) > 1 and data[1] == 0x10:
      return bytes([0x00, 0x00, self.sensor_byte, 0x00, 0x00, 0x00, 0x00, 0x00])
    if len(data) > 7 and data[0] == 0x00 and data[7] == 0x90:
      return bytes([0x00, 0x00, 0xB0, 0xB0, 0xB0, 0xB0, 0x00, 0x00, 0x00, 0x00])
    if len(data) > 1 and data[0] == 0x09 and data[1] == 0x90:
      return bytes([0x00, 0x00, 0x55, 0x2A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    return bytes(10)


def _new_controller(cls, sensor_byte: int = 0xFF) -> tuple[Agile7612Controller, RecordingComm]:
  controller = cls(BufferedTransport(), axis_config=_matching_axis_config())
  comm = RecordingComm()
  comm.sensor_byte = sensor_byte
  controller._comm = comm
  for axis in ALL_AXES:
    controller._homed[axis] = True
  return controller, comm


def _run(cls, sensor_byte: int, action) -> list[tuple[int, str]]:
  controller, comm = _new_controller(cls, sensor_byte)
  try:
    action(controller)
  except (BravoError, NotImplementedError):
    pass
  return comm.calls


class GoldenFrameTestCase(unittest.TestCase):
  """Base class: silences real sleeps so polling loops (jog, tip_force_jog) run fast."""

  def setUp(self) -> None:
    self._real_sleep = time.sleep
    time.sleep = lambda *_a, **_k: None

  def tearDown(self) -> None:
    time.sleep = self._real_sleep

  def assert_matches_golden(self, scenario: str, calls: list) -> None:
    expected = [tuple(pair) for pair in GOLDEN[scenario]]
    self.assertEqual(calls, expected, f"{scenario}: captured frames diverge from golden")


class Agile7612HomingGoldenTests(GoldenFrameTestCase):
  def test_home_x_on_sensor(self):
    calls = _run(Agile7612Controller, 0xFF, lambda c: c._home_x())
    self.assert_matches_golden("agile7612_home_x_on_sensor", calls)

  def test_home_x_off_sensor(self):
    calls = _run(Agile7612Controller, 0x00, lambda c: c._home_x())
    self.assert_matches_golden("agile7612_home_x_off_sensor", calls)

  def test_home_y_on_sensor(self):
    calls = _run(Agile7612Controller, 0xFF, lambda c: c._home_y())
    self.assert_matches_golden("agile7612_home_y_on_sensor", calls)

  def test_home_y_off_sensor(self):
    calls = _run(Agile7612Controller, 0x00, lambda c: c._home_y())
    self.assert_matches_golden("agile7612_home_y_off_sensor", calls)

  def test_home_z_on_sensor(self):
    calls = _run(Agile7612Controller, 0xFF, lambda c: c._home_z())
    self.assert_matches_golden("agile7612_home_z_on_sensor", calls)

  def test_home_z_off_sensor(self):
    calls = _run(Agile7612Controller, 0x00, lambda c: c._home_z())
    self.assert_matches_golden("agile7612_home_z_off_sensor", calls)

  def test_home_w_on_sensor(self):
    calls = _run(Agile7612Controller, 0xFF, lambda c: c._home_w())
    self.assert_matches_golden("agile7612_home_w_on_sensor", calls)

  def test_home_w_off_sensor(self):
    calls = _run(Agile7612Controller, 0x00, lambda c: c._home_w())
    self.assert_matches_golden("agile7612_home_w_off_sensor", calls)

  def test_home_g(self):
    calls = _run(Agile7612Controller, 0xFF, lambda c: c._home_g())
    self.assert_matches_golden("agile7612_home_g", calls)

  def test_home_zg(self):
    calls = _run(Agile7612Controller, 0xFF, lambda c: c._home_zg())
    self.assert_matches_golden("agile7612_home_zg", calls)

  def test_home_axes_order(self):
    calls = _run(Agile7612Controller, 0xFF, lambda c: c.home_axes(list(ALL_AXES)))
    self.assert_matches_golden("agile7612_home_axes_order", calls)


class Agile7612MotionGoldenTests(GoldenFrameTestCase):
  def test_move(self):
    def action(c):
      c.move(
        [
          AxisMoveInfo(axis="x", position=100.0, velocity=50.0, acceleration=100.0, absolute=True),
          AxisMoveInfo(axis="g", position=2.0, velocity=10.0, acceleration=50.0, absolute=True),
        ],
        wait=True,
      )

    calls = _run(Agile7612Controller, 0xFF, action)
    self.assert_matches_golden("agile7612_move", calls)

  def test_jog(self):
    def action(c):
      c.jog(
        JogParams(
          axis="z",
          velocity=5.0,
          acceleration=20.0,
          max_position=50.0,
          tolerance=1.0,
          peak_current=0.2,
        )
      )

    calls = _run(Agile7612Controller, 0xFF, action)
    self.assert_matches_golden("agile7612_jog", calls)

  def test_tip_force_jog(self):
    calls = _run(Agile7612Controller, 0xFF, lambda c: c.tip_force_jog("z", 0.15, 30.0))
    self.assert_matches_golden("agile7612_tip_force_jog", calls)

  def test_grip(self):
    calls = _run(Agile7612Controller, 0xFF, lambda c: c.grip("slow", 3.0))
    self.assert_matches_golden("agile7612_grip", calls)


class MoveOriginOffsetTests(GoldenFrameTestCase):
  """Zg parks at firmware -20mm (hardcoded, independent of any axis_config), so an
  absolute move to Zg is the one case in this default configuration where
  _move_origin is nonzero -- exercising it directly, since none of the golden
  move scenarios happen to touch Zg.
  """

  def test_absolute_move_to_zg_subtracts_the_firmware_park_offset(self):
    controller, comm = _new_controller(Agile7612Controller)
    controller.move(
      [AxisMoveInfo(axis="zg", position=10.0, velocity=25.0, acceleration=250.0, absolute=True)],
      wait=True,
    )

    prepare_move_calls = [hexdata for cid, hexdata in comm.calls if cid == 0xA2]
    self.assertEqual(len(prepare_move_calls), 1)
    payload = bytes.fromhex(prepare_move_calls[0])
    position_ticks = struct.unpack_from("<f", payload, 1)[0]

    origin = controller._move_origin("zg")
    self.assertEqual(origin, 20.0)  # 0.0 homing_offset - (-20.0) firmware park
    expected_ticks = controller._to_ticks("zg", 10.0 - origin)
    self.assertAlmostEqual(position_ticks, expected_ticks, places=3)


class SrtHomingGoldenTests(GoldenFrameTestCase):
  def test_home_x_on_sensor(self):
    calls = _run(AgileSrtController, 0xFF, lambda c: c._srt_home_axis("x"))
    self.assert_matches_golden("srt_home_x_on_sensor", calls)

  def test_home_x_off_sensor(self):
    calls = _run(AgileSrtController, 0x00, lambda c: c._srt_home_axis("x"))
    self.assert_matches_golden("srt_home_x_off_sensor", calls)

  def test_home_y_on_sensor(self):
    calls = _run(AgileSrtController, 0xFF, lambda c: c._srt_home_axis("y"))
    self.assert_matches_golden("srt_home_y_on_sensor", calls)

  def test_home_y_off_sensor(self):
    calls = _run(AgileSrtController, 0x00, lambda c: c._srt_home_axis("y"))
    self.assert_matches_golden("srt_home_y_off_sensor", calls)

  def test_home_z_on_sensor(self):
    calls = _run(AgileSrtController, 0xFF, lambda c: c._srt_home_axis("z"))
    self.assert_matches_golden("srt_home_z_on_sensor", calls)

  def test_home_z_off_sensor(self):
    calls = _run(AgileSrtController, 0x00, lambda c: c._srt_home_axis("z"))
    self.assert_matches_golden("srt_home_z_off_sensor", calls)

  def test_home_w_on_sensor(self):
    calls = _run(AgileSrtController, 0xFF, lambda c: c._srt_home_axis("w"))
    self.assert_matches_golden("srt_home_w_on_sensor", calls)

  def test_home_w_off_sensor(self):
    calls = _run(AgileSrtController, 0x00, lambda c: c._srt_home_axis("w"))
    self.assert_matches_golden("srt_home_w_off_sensor", calls)

  def test_home_axes_order(self):
    calls = _run(AgileSrtController, 0xFF, lambda c: c.home_axes(["x", "y", "z", "w"]))
    self.assert_matches_golden("srt_home_axes_order", calls)


class PositionDecodeTests(unittest.TestCase):
  """Directly exercises _read_raw_position's byte decode, which the golden-frame
  scenarios cannot: RecordingComm's generic response is an all-zero, symmetric
  10 bytes, so a big-endian-vs-little-endian decode bug is invisible there.
  """

  def test_controller_1_axis_decodes_the_position_register_big_endian(self):
    controller, comm = _new_controller(Agile7612Controller)
    real_send_command = comm.send_command

    def send_command(command_id, data: bytes = b"", timeout: float = 2.0) -> bytes:
      # Register 0x07 reads (raw position) get a deliberately asymmetric
      # big-endian value; everything else keeps RecordingComm's normal
      # canned responses.
      if len(data) > 1 and data[1] == 0x07:
        comm.calls.append((int(command_id), data.hex()))
        return bytes([0x00, 0x00, 0x12, 0x34, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
      return real_send_command(command_id, data, timeout)

    comm.send_command = send_command  # type: ignore[method-assign]

    position = controller.get_position("x")

    # _read_raw_position's own documented formula for a controller-1 axis:
    # float(raw_be_u16) / (ticks_per_eng_unit * scale / 2.0), scale=16.0 for X.
    raw_be_u16 = 0x1234
    ticks_per_eng_unit = controller._ticks_per_unit["x"]
    expected = float(raw_be_u16) / (ticks_per_eng_unit * 16.0 / 2.0)
    self.assertAlmostEqual(position, expected)
    # A little-endian misreading of the same two bytes (0x3412) would give a
    # visibly different result, so this also fails if the byte order flips.
    wrong_le = float(0x3412) / (ticks_per_eng_unit * 16.0 / 2.0)
    self.assertNotAlmostEqual(position, wrong_le)

  def test_controller_2_axis_decodes_sign_and_magnitude(self):
    controller, comm = _new_controller(Agile7612Controller)
    real_send_command = comm.send_command

    def send_command(command_id, data: bytes = b"", timeout: float = 2.0) -> bytes:
      if len(data) > 1 and data[1] == 0x07:
        comm.calls.append((int(command_id), data.hex()))
        # High bit set (sign) + magnitude 0x0100 in the low 15 bits.
        return bytes([0x00, 0x00, 0x81, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
      return real_send_command(command_id, data, timeout)

    comm.send_command = send_command  # type: ignore[method-assign]

    position = controller.get_position("g")

    eff_tpu = controller._CTRL2_EFFECTIVE_TPU.get("g", 126.8)
    expected = -1.0 * float(0x0100) * 2.0 / eff_tpu
    self.assertAlmostEqual(position, expected)
    self.assertLess(position, 0.0)


if __name__ == "__main__":
  unittest.main()
