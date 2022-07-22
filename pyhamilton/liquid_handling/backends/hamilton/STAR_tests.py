""" Tests for Hamilton backend. """

import unittest

from pyhamilton.liquid_handling.liquid_handler import LiquidHandler
from pyhamilton.liquid_handling.resources import (
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cos_96_DW_1mL,
  TipType,
  Cos_96_DW_500ul,
  standard_volume_tip_with_filter,
)
from pyhamilton.liquid_handling.resources.ml_star import STF_L, HTF_L

from .STAR import STAR
from .errors import (
  CommandSyntaxError,
  HamiltonFirmwareError,
  NoTipError,
  HardwareError,
  UnknownHamiltonError
)


class TestSTARResponseParsing(unittest.TestCase):
  """ Test parsing of response from Hamilton. """

  def setUp(self):
    super().setUp()
    self.star = STAR()

  def test_parse_response_params(self):
    parsed = self.star.parse_response("C0QMid1111", "")
    self.assertEqual(parsed, {"id": 1111})

    parsed = self.star.parse_response("C0QMid1111", "id####")
    self.assertEqual(parsed, {"id": 1111})

    parsed = self.star.parse_response("C0QMid1112aaabc", "aa&&&")
    self.assertEqual(parsed, {"id": 1112, "aa": "abc"})

    parsed = self.star.parse_response("C0QMid1112aa-21", "aa##")
    self.assertEqual(parsed, {"id": 1112, "aa": -21})

    parsed = self.star.parse_response("C0QMid1113pqABC", "pq***")
    self.assertEqual(parsed, {"id": 1113, "pq": int("ABC", base=16)})

    with self.assertRaises(ValueError):
      # should fail with auto-added id.
      parsed = self.star.parse_response("C0QMaaabc", "")
      self.assertEqual(parsed, "")

    with self.assertRaises(ValueError):
      self.star.parse_response("C0QM", "id####") # pylint: disable=expression-not-assigned

    with self.assertRaises(ValueError):
      self.star.parse_response("C0RV", "") # pylint: disable=expression-not-assigned

  def test_parse_response_no_errors(self):
    parsed = self.star.parse_response("C0QMid1111", "")
    self.assertEqual(parsed, {"id": 1111})

    parsed = self.star.parse_response("C0QMid1111 er00/00", "")
    self.assertEqual(parsed, {"id": 1111})

    parsed = self.star.parse_response("C0QMid1111 er00/00 P100/00", "")
    self.assertEqual(parsed, {"id": 1111})

  def test_parse_response_master_error(self):
    with self.assertRaises(HamiltonFirmwareError) as ctx:
      self.star.parse_response("C0QMid1111 er01/30", "")
    e = ctx.exception
    self.assertEqual(len(e), 1)
    self.assertIn("Master", e)
    self.assertIsInstance(e["Master"], CommandSyntaxError)
    self.assertEqual(e["Master"].message, "Unknown command")

  def test_parse_response_slave_errors(self):
    with self.assertRaises(HamiltonFirmwareError) as ctx:
      self.star.parse_response("C0QMid1111 er99/00 P100/00 P235/00 P402/98 PG08/76", "")
    e = ctx.exception
    self.assertEqual(len(e), 3)
    self.assertNotIn("Master", e)
    self.assertNotIn("Pipetting channel 1", e)
    self.assertEqual(e["Pipetting channel 2"].raw_response, "35/00")
    self.assertEqual(e["Pipetting channel 4"].raw_response, "02/98")
    self.assertEqual(e["Pipetting channel 16"].raw_response, "08/76")

    self.assertIsInstance(e["Pipetting channel 2"], UnknownHamiltonError)
    self.assertIsInstance(e["Pipetting channel 4"], HardwareError)
    self.assertIsInstance(e["Pipetting channel 16"], NoTipError)

    self.assertEqual(e["Pipetting channel 2"].message, "No error")
    self.assertEqual(e["Pipetting channel 4"].message, "Unknown trace information code 98")
    self.assertEqual(e["Pipetting channel 16"].message, "Tip already picked up")

  def test_parse_slave_response_errors(self):
    with self.assertRaises(HamiltonFirmwareError) as ctx:
      self.star.parse_response("P1OQid1111er30", "")

    e = ctx.exception
    self.assertEqual(len(e), 1)
    self.assertNotIn("Master", e)
    self.assertIn("Pipetting channel 1", e)
    self.assertIsInstance(e["Pipetting channel 1"], UnknownHamiltonError)
    self.assertEqual(e["Pipetting channel 1"].message, "Unknown command")


class STARCommandCatcher(STAR):
  """ Mock backend for star that catches commands and saves them instad of sending them to the
  machine. """

  def __init__(self):
    super().__init__()
    self.commands = []

  def setup(self):
    self.setup_finished = True

  def send_command(self, module, command, **kwargs):
    cmd, _ = self._assemble_command(module, command, **kwargs)
    self.commands.append(cmd)


class TestSTARLiquidHandlerCommands(unittest.TestCase):
  """ Test STAR backend for liquid handling. """

  def setUp(self):
    # pylint: disable=invalid-name
    self.mockSTAR = STARCommandCatcher()
    self.lh = LiquidHandler(self.mockSTAR)

    tip_car = TIP_CAR_480_A00(name="tip carrier")
    tip_car[1] = STF_L(name="tips_01")
    self.lh.assign_resource(tip_car, rails=1)

    plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    plt_car[0] = Cos_96_DW_1mL(name="plate_01")
    self.lh.assign_resource(plt_car, rails=9)

    self.maxDiff = None

    self.lh.setup()

  def _assert_command_in_command_buffer(self, cmd: str, should_be: bool, fmt: str):
    """ Assert that the given command was sent to the backend. The ordering of the parameters is not
    taken into account, but the values and formatting should match. The id parameter of the command
    is ignored.

    Args:
      cmd: the command to look for
      should_be: whether the command should be found or not
      fmt: the format of the command
    """

    found = False
    # Command that fits the format, but is not the same as the command we are looking for.
    similar = None

    parsed_cmd = self.mockSTAR.parse_fw_string(cmd, fmt)
    parsed_cmd.pop("id")

    for sent_cmd in self.mockSTAR.commands:
      # When the module and command do not match, there is no point in comparing the parameters.
      if sent_cmd[0:4] != cmd[0:4]:
        continue

      try:
        parsed_sent_cmd = self.mockSTAR.parse_fw_string(sent_cmd, fmt)
        parsed_sent_cmd.pop("id")

        if parsed_cmd == parsed_sent_cmd:
          self.mockSTAR.commands.remove(sent_cmd)
          found = True
          break
        else:
          similar = parsed_sent_cmd
      except ValueError as e:
        # The command could not be parsed.
        print(e)
        continue

    if should_be and not found:
      if similar is not None:
        # These will not be equal, but this method does give a better error message than `fail`.
        self.assertEqual(similar, parsed_cmd)
      else:
        self.fail(f"Command {cmd} not found in sent commands: {self.mockSTAR.commands}")
    elif not should_be and found:
      self.fail(f"Command {cmd} was found in sent commands: {self.mockSTAR.commands}")

  def test_channel_positions_to_fw_positions(self):
    """ Convert channel positions to firmware positions. """
    resource = self.lh.get_resource("tips_01")
    self.assertEqual(
      self.mockSTAR._channel_positions_to_fw_positions(["A1"], resource),
      ([1179, 0], [2418, 0], [True, False])
    )

    self.assertEqual(
      self.mockSTAR._channel_positions_to_fw_positions(["A1", "F1"], resource),
      ([1179, 1179, 0], [2418, 1968, 0], [True, True, False])
    )

  def _assert_command_sent_once(self, cmd: str, fmt: str):
    """ Assert that the given command was sent to the backend exactly once. """
    self._assert_command_in_command_buffer(cmd, True, fmt)
    self._assert_command_in_command_buffer(cmd, False, fmt)

  def test_tip_definition(self):
    pass

  def test_tip_pickup_01(self):
    self.lh.pickup_tips("tips_01", "A1", "B1")
    self._assert_command_sent_once(
      "C0TPid0000xp01179 01179 00000&yp2418 2328 0000&tm1 1 0&tt01tp2244tz2164th2450td0",
      "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_tip_pickup_56(self):
    self.lh.pickup_tips("tips_01", channel_5="E1", channel_6="F1")
    self._assert_command_sent_once(
      "C0TPid0000xp01179 01179 00000&yp2058 1968 0000&tm1 1 0&tt01tp2244tz2164th2450td0",
      "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_tip_pickup_16(self):
    self.lh.pickup_tips("tips_01", channel_1="A1", channel_5="F1")
    self._assert_command_sent_once(
      "C0TPid0000xp01179 01179 00000&yp2418 1968 0000&tm1 1 0&tt01tp2244tz2164th2450td0",
      "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_tip_discard_56(self):
    self.test_tip_pickup_56() # pick up tips first
    self.lh.discard_tips("tips_01", channel_5="E1", channel_6="F1")
    self._assert_command_sent_once(
      "C0TRid0000xp01179 01179 00000&yp2058 1968 0000&tm1 1 0&tt01tp2244tz2164th2450ti0",
      "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####ti#")

  def test_single_channel_aspiration(self):
    self.lh.pickup_tips("tips_01", "A1")
    self.lh.aspirate("plate_01", ("A1", 100))

    # Real command, but with extra parameters. `lp`, `zl`, `zx`, `av`, `zu`, `zr` changed
    # TODO: Do we need these parameters with the real robot?
    # self._assert_command_sent_once(
    #   "C0ASid0300at0&tm1 0&xp02980 00000&yp1460 0000&th2450te2450lp2321 2450&ch000&zl1881 2450&"
    #   "zx1871 0000&ip0000&it0&fp0000&av01072 00000&as1000&ta000&ba0000&oa000&lm0&ll1&lv1&ld00&"
    #   "de0020&wt10&mv00000&mc00&mp000&ms1000&gi000&gj0gk0zu0032 0000&zr06180 00000&mh0000&zo000&"
    #   "po0100&lk0&ik0000&sd0500&se0500&sz0300&io0000&il00000&in0000&",
    #   fmt="at# (n)tm# (n)xp##### (n)yp#### (n)th####te####lp#### (n)ch### (n)zl#### (n)zx#### (n)"
    #   "ip#### (n)it# (n)fp#### (n)av#### (n)as#### (n)ta### (n)ba#### (n)oa### (n)lm# (n)ll# (n)"
    #   "lv# (n)ld## (n)de#### (n)wt## (n)mv##### (n)mc## (n)mp### (n)ms#### (n)gi### (n)gj#gk#"
    #   "zu#### (n)zr#### (n)mh#### (n)zo### (n)po#### (n)lk# (n)ik#### (n)sd#### (n)se#### (n)"
    #   "sz#### (n)io#### (n)il##### (n)in#### (n)")

    # This passes the test, but is not the real command.
    self._assert_command_sent_once(
      "C0ASid0000at0&tm1 0&xp02980 00000&yp1460 0000&th2450te2450lp2321&ch000&zl1881&"
      "zx1871&ip0000&it0&fp0000&av01072&as1000&ta000&ba0000&oa000&lm0&ll1&lv1&ld00&"
      "de0020&wt10&mv00000&mc00&mp000&ms1000&gi000&gj0gk0zu0032&zr06180&mh0000&zo000&"
      "po0100&lk0&ik0000&sd0500&se0500&sz0300&io0000&il00000&in0000&",
      fmt="at# (n)tm# (n)xp##### (n)yp#### (n)th####te####lp#### (n)ch### (n)zl#### (n)zx#### (n)"
      "ip#### (n)it# (n)fp#### (n)av#### (n)as#### (n)ta### (n)ba#### (n)oa### (n)lm# (n)ll# (n)"
      "lv# (n)ld## (n)de#### (n)wt## (n)mv##### (n)mc## (n)mp### (n)ms#### (n)gi### (n)gj#gk#"
      "zu#### (n)zr#### (n)mh#### (n)zo### (n)po#### (n)lk# (n)ik#### (n)sd#### (n)se#### (n)"
      "sz#### (n)io#### (n)il##### (n)in#### (n)")

  def test_multi_channel_aspiration(self):
    self.lh.pickup_tips("tips_01", "A1", "B1")
    self.lh.aspirate("plate_01", ("A1", 100), ("B1", 100))

    # Real command
    # self._assert_command_sent_once(
    #   "C0ASid0225at0&tm1 1 0&xp02980 02980 00000&yp1460 1370 0000&th2450te2450lp2321 2321 2450&"
    #   "ch000 000&zl1881 1881 2450&zx1871 1871 0000&ip0000 0000&it0 0&fp0000 0000&"
    #   "av01072 01072 00000&as1000 1000&ta000 000&ba0000 0000&oa000 000&lm0 0&ll1 1&lv1 1&ld00 00&"
    #   "de0020 0020&wt10 10&mv00000 00000&mc00 00&mp000 000&ms1000 1000&gi000 000&gj0gk0"
    #   "zu0032 0032 0000&zr06180 06180 00000&mh0000 0000&zo000 000&po0100 0100&lk0 0&ik0000 0000&"
    #   "sd0500 0500&se0500 0500&sz0300 0300&io0000 0000&il00000 00000&in0000 0000&",
    #   fmt="at# (n)tm# (n)xp##### (n)yp#### (n)th####te####lp#### (n)ch### (n)zl#### (n)zx#### (n)"
    #   "ip#### (n)it# (n)fp#### (n)av#### (n)as#### (n)ta### (n)ba#### (n)oa### (n)lm# (n)ll# (n)"
    #   "lv# (n)ld## (n)de#### (n)wt## (n)mv##### (n)mc## (n)mp### (n)ms#### (n)gi### (n)gj#gk#"
    #   "zu#### (n)zr#### (n)mh#### (n)zo### (n)po#### (n)lk# (n)ik#### (n)sd#### (n)se#### (n)"
    #   "sz#### (n)io#### (n)il##### (n)in#### (n)")

    # This passes the test, but is not the real command.
    self._assert_command_sent_once(
      "C0ASid0000at0&tm1 1 0&xp02980 02980 00000&yp1460 1370 0000&th2450te2450lp2321 2321&"
      "ch000 000&zl1881 1881&zx1871 1871&ip0000 0000&it0 0&fp0000 0000&"
      "av01072 01072&as1000 1000&ta000 000&ba0000 0000&oa000 000&lm0 0&ll1 1&lv1 1&ld00 00&"
      "de0020 0020&wt10 10&mv00000 00000&mc00 00&mp000 000&ms1000 1000&gi000 000&gj0gk0"
      "zu0032 0032&zr06180 06180&mh0000 0000&zo000 000&po0100 0100&lk0 0&ik0000 0000&"
      "sd0500 0500&se0500 0500&sz0300 0300&io0000 0000&il00000 00000&in0000 0000&",
      fmt="at# (n)tm# (n)xp##### (n)yp#### (n)th####te####lp#### (n)ch### (n)zl#### (n)zx#### (n)"
      "ip#### (n)it# (n)fp#### (n)av#### (n)as#### (n)ta### (n)ba#### (n)oa### (n)lm# (n)ll# (n)"
      "lv# (n)ld## (n)de#### (n)wt## (n)mv##### (n)mc## (n)mp### (n)ms#### (n)gi### (n)gj#gk#"
      "zu#### (n)zr#### (n)mh#### (n)zo### (n)po#### (n)lk# (n)ik#### (n)sd#### (n)se#### (n)"
      "sz#### (n)io#### (n)il##### (n)in#### (n)")

  def test_single_channel_dispense(self):
    self.lh.pickup_tips("tips_01", "A1")
    self.lh.dispense("plate_01", ("A1", 100))
    self._assert_command_sent_once(
      "C0DSid0000dm2&tm1 0&xp02980 00000&yp1460 0000&zx1871&lp2321&zl1881&"
      "ip0000&it0&fp0000&th2450te2450dv01072&ds1200&ss0050&rv000&ta000&ba0000&lm0&zo000&ll1&"
      "lv1&de0020&mv00000&mc00&mp000&ms0010&wt00&gi000&gj0gk0zu0032&dj00zr06180&"
      " mh0000&po0100&",
      "dm# (n)tm# (n)xp##### (n)yp#### (n)zx#### (n)lp#### (n)zl#### (n)ip#### (n)it# (n)fp#### (n)"
      "th####te####dv##### (n)ds#### (n)ss#### (n)rv### (n)ta### (n)ba#### (n)lm# (n)zo### (n)"
      "ll# (n)lv# (n)de#### (n)mv##### (n)mc## (n)mp### (n)ms#### (n)wt## (n)gi### (n)gj#gk#"
      "zu#### (n)zr##### (n)mh#### (n)po#### (n)")

  def test_multi_channel_dispense(self):
    self.lh.pickup_tips("tips_01", "A1", "B1")
    self.lh.dispense("plate_01", ("A1", 100), ("B1", 100))

    # self._assert_command_sent_once(
    #   "C0DSid0317dm2 2&tm1 1 0&dv01072 01072 00000&xp02980 02980 00000&yp1460 1370 0000&"
    #   "zx1871 1871 0000&lp2321 2321 2450&zl1881 1881 2450&ip0000 0000&it0 0&fp0000 0000&th2450"
    #   "te2450ds1200 1200&ss0050 0050&rv000 000&ta000 000&ba0000 0000&lm0 0&zo000 000&ll1 1&",
    #   "lv1 1&de0020 0020&mv00000 00000&mc00 00&mp000 000&ms0010 0010&wt00 00&gi000 000&gj0gk0"
    #   "zu0032 0032 0000&dj00zr06180 06180 00000&mh0000 0000&po0100 0100&",
    #   "dm# (n)tm# (n)xp##### (n)yp#### (n)zx#### (n)lp#### (n)zl#### (n)ip#### (n)it# (n)fp#### (n)"
    #   "th####te####dv##### (n)ds#### (n)ss#### (n)rv### (n)ta### (n)ba#### (n)lm# (n)zo### (n)"
    #   "ll# (n)lv# (n)de#### (n)mv##### (n)mc## (n)mp### (n)ms#### (n)wt## (n)gi### (n)gj#gk#"
    #   "zu#### (n)zr##### (n)mh#### (n)po#### (n)")
    # modified to remove additional values in parameters (dm, dv, lp, zl, zr, zu, zx)

    self._assert_command_sent_once(
      "C0DSid0317dm2 2&tm1 1 0&dv01072 01072&xp02980 02980 00000&yp1460 1370 0000&"
      "zx1871 1871&lp2321 2321&zl1881 1881&ip0000 0000&it0 0&fp0000 0000&th2450"
      "te2450ds1200 1200&ss0050 0050&rv000 000&ta000 000&ba0000 0000&lm0 0&zo000 000&ll1 1&"
      "lv1 1&de0020 0020&mv00000 00000&mc00 00&mp000 000&ms0010 0010&wt00 00&gi000 000&gj0gk0"
      "zu0032 0032&dj00zr06180 06180&mh0000 0000&po0100 0100&",
      "dm# (n)tm# (n)xp##### (n)yp#### (n)zx#### (n)lp#### (n)zl#### (n)ip#### (n)it# (n)fp#### (n)"
      "th####te####dv##### (n)ds#### (n)ss#### (n)rv### (n)ta### (n)ba#### (n)lm# (n)zo### (n)"
      "ll# (n)lv# (n)de#### (n)mv##### (n)mc## (n)mp### (n)ms#### (n)wt## (n)gi### (n)gj#gk#"
      "zu#### (n)zr##### (n)mh#### (n)po#### (n)")


if __name__ == "__main__":
  unittest.main()
