""" Tests for Hamilton backend. """

from typing import cast
import unittest

from pylabrobot.liquid_handling.liquid_handler import LiquidHandler
from pylabrobot.liquid_handling.resources import (
  Resource,
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cos_96_EZWash,
  Coordinate,
  PlateReader,
  ResourceStack,
  Lid
)
from pylabrobot.liquid_handling.resources.hamilton import STARLetDeck
from pylabrobot.liquid_handling.resources.ml_star import STF_L
from pylabrobot.liquid_handling.standard import Move, Pickup

from tests.usb import MockDev, MockEndpoint

from .STAR import STAR
from .errors import (
  CommandSyntaxError,
  HamiltonFirmwareError,
  NoTipError,
  HardwareError,
  UnknownHamiltonError
)


DROP_TIP_FORMAT = "xp##### (n)yp#### (n)tm# (n)tp####tz####th####ti#"
ASPIRATION_RESPONSE_FORMAT = (
  "at# (n)tm# (n)xp##### (n)yp#### (n)th####te####lp#### (n)ch### (n)zl#### (n)zx#### (n)"
  "ip#### (n)it# (n)fp#### (n)av#### (n)as#### (n)ta### (n)ba#### (n)oa### (n)lm# (n)ll# (n)"
  "lv# (n)ld## (n)de#### (n)wt## (n)mv##### (n)mc## (n)mp### (n)ms#### (n)gi### (n)gj#gk#"
  "zu#### (n)zr#### (n)mh#### (n)zo### (n)po#### (n)lk# (n)ik#### (n)sd#### (n)se#### (n)"
  "sz#### (n)io#### (n)il##### (n)in#### (n)"
)
DISPENSE_RESPONSE_FORMAT = (
  "dm# (n)tm# (n)xp##### (n)yp#### (n)zx#### (n)lp#### (n)zl#### (n)ip#### (n)it# (n)fp#### (n)"
  "th####te####dv##### (n)ds#### (n)ss#### (n)rv### (n)ta### (n)ba#### (n)lm# (n)zo### (n)"
  "ll# (n)lv# (n)de#### (n)mv##### (n)mc## (n)mp### (n)ms#### (n)wt## (n)gi### (n)gj#gk#"
  "zu#### (n)zr##### (n)mh#### (n)po#### (n)"
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
    assert "Master" in e
    self.assertIsInstance(e["Master"], CommandSyntaxError)
    self.assertEqual(e["Master"].message, "Unknown command")

  def test_parse_response_slave_errors(self):
    with self.assertRaises(HamiltonFirmwareError) as ctx:
      self.star.parse_response("C0QMid1111 er99/00 P100/00 P235/00 P402/98 PG08/76", "")
    e = ctx.exception
    self.assertEqual(len(e), 3)
    assert "Master" not in e
    assert "Pipetting channel 1" not in e
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
    assert "Master" not in e
    assert "Pipetting channel 1" in e
    self.assertIsInstance(e["Pipetting channel 1"], UnknownHamiltonError)
    self.assertEqual(e["Pipetting channel 1"].message, "Unknown command")


class STARUSBCommsMocker(STAR):
  """ Mocks PyUSB """

  def setup(self, send_response):
    self.dev = MockDev(send_response)
    self.read_endpoint = MockEndpoint()
    self.write_endpoint = MockEndpoint()


class TestSTARUSBComms(unittest.TestCase):
  """ Test that USB data is parsed correctly. """
  def test_send_command_correct_response(self):
    star = STARUSBCommsMocker()
    star.setup(send_response="C0QMid0001") # correct response
    resp = star.send_command("C0", command="QM", fmt="")
    self.assertEqual(resp, {"id": 1})

  def test_send_command_wrong_id(self):
    star = STARUSBCommsMocker(read_timeout=2, packet_read_timeout=1)
    star.setup(send_response="C0QMid0000") # wrong response
    with self.assertRaises(TimeoutError):
      star.send_command("C0", command="QM")

  def test_send_command_plaintext_response(self):
    star = STARUSBCommsMocker(read_timeout=2, packet_read_timeout=1)
    star.setup(send_response="this is plain text") # wrong response
    with self.assertRaises(TimeoutError):
      star.send_command("C0", command="QM")


class STARCommandCatcher(STAR):
  """ Mock backend for star that catches commands and saves them instad of sending them to the
  machine. """

  def __init__(self):
    super().__init__()
    self.commands = []

  def setup(self):
    self.setup_finished = True
    self._num_channels = 8
    self.iswap_installed = True
    self.core96_head_installed = True

  def send_command(self, module, command, fmt="", timeout=0, **kwargs):
    cmd, _ = self._assemble_command(module, command, **kwargs)
    self.commands.append(cmd)

  def stop(self):
    self.stop_finished = True


class TestSTARLiquidHandlerCommands(unittest.TestCase):
  """ Test STAR backend for liquid handling. """

  def setUp(self):
    # pylint: disable=invalid-name
    self.mockSTAR = STARCommandCatcher()
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.mockSTAR, deck=self.deck)

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[1] = self.tip_rack = STF_L(name="tip_rack_01")
    self.deck.assign_child_resource(self.tip_car, rails=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = Cos_96_EZWash(name="plate_01", with_lid=True)
    self.plt_car[1] = self.other_plate = Cos_96_EZWash(name="plate_02", with_lid=True)
    self.deck.assign_child_resource(self.plt_car, rails=9)

    class BlueBucket(Resource):
      def __init__(self, name: str):
        super().__init__(name, size_x=123, size_y=82, size_z=75, category="bucket")
    self.bb = BlueBucket(name="blue bucket")
    self.deck.assign_child_resource(self.bb, location=Coordinate(425, 78.5, 20))

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

  def test_ops_to_fw_positions(self):
    """ Convert channel positions to firmware positions. """
    # pylint: disable=protected-access
    tip_a1 = self.tip_rack.get_item("A1")
    tip_f1 = self.tip_rack.get_item("F1")
    tip_type = self.tip_rack.tip_type

    op = Pickup(resource=tip_a1, tip_type=tip_type)
    self.assertEqual(
      self.mockSTAR._ops_to_fw_positions((op,), use_channels=[0]),
      ([1179, 0], [2418, 0], [True, False])
    )

    ops = (Pickup(resource=tip_a1, tip_type=tip_type), Pickup(resource=tip_f1, tip_type=tip_type))
    self.assertEqual(
      self.mockSTAR._ops_to_fw_positions(ops, use_channels=[0, 1]),
      ([1179, 1179, 0], [2418, 1968, 0], [True, True, False])
    )

    ops = (Pickup(resource=tip_a1, tip_type=tip_type), Pickup(resource=tip_f1, tip_type=tip_type))
    self.assertEqual(
      self.mockSTAR._ops_to_fw_positions(ops, use_channels=[1, 2]),
      ([0, 1179, 1179, 0], [0, 2418, 1968, 0], [False, True, True, False])
    )

  def _assert_command_sent_once(self, cmd: str, fmt: str):
    """ Assert that the given command was sent to the backend exactly once. """
    self._assert_command_in_command_buffer(cmd, True, fmt)
    self._assert_command_in_command_buffer(cmd, False, fmt)

  def test_tip_definition(self):
    pass

  def test_tip_pickup_01(self):
    self.lh.pick_up_tips(self.tip_rack["A1", "B1"])
    self._assert_command_sent_once(
      "C0TPid0000xp01179 01179 00000&yp2418 2328 0000tm1 1 0&tt01tp2243tz2163th2450td0",
      "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_tip_pickup_56(self):
    self.lh.pick_up_tips(self.tip_rack["E1", "F1"], use_channels=[4, 5])
    self._assert_command_sent_once(
      "C0TPid0000xp00000 00000 00000 00000 01179 01179 00000&yp0000 0000 0000 0000 2058 1968 "
      "0000&tm0 0 0 0 1 1 0 &tt01tp2243tz2163th2450td0",
      "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_tip_pickup_15(self):
    self.lh.pick_up_tips(self.tip_rack["A1", "F1"], use_channels=[0, 4])
    self._assert_command_sent_once(
      "C0TPid0000xp01179 00000 00000 00000 01179 00000&yp2418 0000 0000 0000 1968 0000 "
      "&tm1 0 0 0 1 0&tt01tp2243tz2163th2450td0",
      "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_tip_drop_56(self):
    self.test_tip_pickup_56() # pick up tips first
    self.lh.drop_tips(self.tip_rack["E1", "F1"], use_channels=[4, 5])
    self._assert_command_sent_once(
      "C0TRid0000xp00000 00000 00000 00000 01179 01179 00000&yp0000 0000 0000 0000 2058 1968 "
      "0000&tm0 0 0 0 1 1 0&tp2243tz2163th2450ti1", DROP_TIP_FORMAT)

  def test_single_channel_aspiration(self):
    self.lh.aspirate(self.plate["A1"], vols=[100 * 1.072]) # TODO: Hamilton liquid classes

    # This passes the test, but is not the real command.
    self._assert_command_sent_once(
      "C0ASid0000at0&tm1 0&xp02980 00000&yp1460 0000&th2450te2450lp1931&ch000&zl1881&"
      "zx1831&ip0000&it0&fp0000&av01072&as1000&ta000&ba0000&oa000&lm0&ll1&lv1&ld00&"
      "de0020&wt10&mv00000&mc00&mp000&ms1000&gi000&gj0gk0zu0032&zr06180&mh0000&zo000&"
      "po0100&lk0&ik0000&sd0500&se0500&sz0300&io0000&il00000&in0000&",
      fmt=ASPIRATION_RESPONSE_FORMAT)

  def test_single_channel_aspiration_offset(self):
    # TODO: Hamilton liquid classes
    self.lh.aspirate(self.plate["A1"], vols=[100*1.072], offsets=Coordinate(0, 0, 10))

    # This passes the test, but is not the real command.
    self._assert_command_sent_once(
      "C0ASid0000at0&tm1 0&xp02980 00000&yp1460 0000&th2450te2450lp2021&ch000&zl1971&"
      "zx1921&ip0000&it0&fp0000&av01072&as1000&ta000&ba0000&oa000&lm0&ll1&lv1&ld00&"
      "de0020&wt10&mv00000&mc00&mp000&ms1000&gi000&gj0gk0zu0032&zr06180&mh0000&zo000&"
      "po0100&lk0&ik0000&sd0500&se0500&sz0300&io0000&il00000&in0000&",
      fmt=ASPIRATION_RESPONSE_FORMAT)

  def test_multi_channel_aspiration(self):
    # TODO: Hamilton liquid classes
    self.lh.aspirate(self.plate["A1:B1"], vols=100*1.072)

    # This passes the test, but is not the real command.
    self._assert_command_sent_once(
      "C0ASid0000at0&tm1 1 0&xp02980 02980 00000&yp1460 1370 0000&th2450te2450lp1931 1931&"
      "ch000 000&zl1881 1881&zx1831 1831&ip0000 0000&it0 0&fp0000 0000&"
      "av01072 01072&as1000 1000&ta000 000&ba0000 0000&oa000 000&lm0 0&ll1 1&lv1 1&ld00 00&"
      "de0020 0020&wt10 10&mv00000 00000&mc00 00&mp000 000&ms1000 1000&gi000 000&gj0gk0"
      "zu0032 0032&zr06180 06180&mh0000 0000&zo000 000&po0100 0100&lk0 0&ik0000 0000&"
      "sd0500 0500&se0500 0500&sz0300 0300&io0000 0000&il00000 00000&in0000 0000&",
      fmt=ASPIRATION_RESPONSE_FORMAT)

  def test_aspirate_single_resource(self):
    self.lh.aspirate(self.bb, vols=10, use_channels=[0, 1, 2, 3, 4])
    self._assert_command_sent_once(
      "C0ASid0009at0&tm1 1 1 1 1 0&xp04865 04865 04865 04865 04865 00000&yp2098 1961 1825 1688 "
      "1551 0000&th2450te2450lp1260 1260 1260 1260 1260&ch000 000 000 000 000&zl1210 1210 1210 "
      "1210 1210&po0100 0100 0100 0100 0100&zu0032 0032 0032 0032 0032&zr06180 06180 06180 06180 "
      "06180&zx1160 1160 1160 1160 1160&ip0000 0000 0000 0000 0000&it0 0 0 0 0&fp0000 0000 0000 "
      "0000 0000&av00100 00100 00100 00100 00100&as1000 1000 1000 1000 1000&ta000 000 000 000 000&"
      "ba0000 0000 0000 0000 0000&oa000 000 000 000 000&lm0 0 0 0 0&ll1 1 1 1 1&lv1 1 1 1 1&zo000 "
      "000 000 000 000&ld00 00 00 00 00&de0020 0020 0020 0020 0020&wt10 10 10 10 10&mv00000 00000 "
      "00000 00000 00000&mc00 00 00 00 00&mp000 000 000 000 000&ms1000 1000 1000 1000 1000&mh0000 "
      "0000 0000 0000 0000&gi000 000 000 000 000&gj0gk0lk0 0 0 0 0&ik0000 0000 0000 0000 0000&"
      "sd0500 0500 0500 0500 0500&se0500 0500 0500 0500 0500&sz0300 0300 0300 0300 0300&io0000 0000"
      " 0000 0000 0000&il00000 00000 00000 00000 00000&in0000 0000 0000 0000 0000&",
      fmt=ASPIRATION_RESPONSE_FORMAT)

  def test_dispense_single_resource(self):
    self.lh.dispense(self.bb, vols=10, use_channels=[0, 1, 2, 3, 4])
    self._assert_command_sent_once(
      "C0DSid0010dm2 2 2 2 2&tm1 1 1 1 1 0&xp04865 04865 04865 04865 04865 00000&yp2098 1961 1825 "
      "1688 1551 0000&zx1871 1871 1871 1871 1871&lp2321 2321 2321 2321 2321&zl1210 1210 1210 1210 "
      "1210&po0100 0100 0100 0100 0100&ip0000 0000 0000 0000 0000&it0 0 0 0 0&fp0000 0000 0000 0000"
      " 0000&zu0032 0032 0032 0032 0032&zr06180 06180 06180 06180 06180&th2450te2450dv00100 00100 "
      "00100 00100 00100&ds1200 1200 1200 1200 1200&ss0050 0050 0050 0050 0050&rv000 000 000 000 "
      "000&ta000 000 000 000 000&ba0000 0000 0000 0000 0000&lm0 0 0 0 0&dj00zo000 000 000 000 000&"
      "ll1 1 1 1 1&lv1 1 1 1 1&de0020 0020 0020 0020 0020&wt00 00 00 00 00&mv00000 00000 00000 "
      "00000 00000&mc00 00 00 00 00&mp000 000 000 000 000&ms0010 0010 0010 0010 0010&mh0000 0000 "
      "0000 0000 0000&gi000 000 000 000 000&gj0gk0",
      fmt=DISPENSE_RESPONSE_FORMAT)

  def test_single_channel_dispense(self):
    # TODO: Hamilton liquid classes
    self.lh.dispense(self.plate["A1"], vols=[100*1.072])
    self._assert_command_sent_once(
      "C0DSid0000dm2&tm1 0&xp02980 00000&yp1460 0000&zx1871&lp2321&zl1881&"
      "ip0000&it0&fp0000&th2450te2450dv01072&ds1200&ss0050&rv000&ta000&ba0000&lm0&zo000&ll1&"
      "lv1&de0020&mv00000&mc00&mp000&ms0010&wt00&gi000&gj0gk0zu0032&dj00zr06180&"
      " mh0000&po0100&",
      fmt=DISPENSE_RESPONSE_FORMAT)

  def test_multi_channel_dispense(self):
    # TODO: Hamilton liquid classes
    self.lh.dispense(self.plate["A1:B1"], vols=100*1.072)

    self._assert_command_sent_once(
      "C0DSid0317dm2 2&tm1 1 0&dv01072 01072&xp02980 02980 00000&yp1460 1370 0000&"
      "zx1871 1871&lp2321 2321&zl1881 1881&ip0000 0000&it0 0&fp0000 0000&th2450"
      "te2450ds1200 1200&ss0050 0050&rv000 000&ta000 000&ba0000 0000&lm0 0&zo000 000&ll1 1&"
      "lv1 1&de0020 0020&mv00000 00000&mc00 00&mp000 000&ms0010 0010&wt00 00&gi000 000&gj0gk0"
      "zu0032 0032&dj00zr06180 06180&mh0000 0000&po0100 0100&",
      fmt=DISPENSE_RESPONSE_FORMAT)

  def test_core_96_tip_pickup(self):
    self.lh.pick_up_tips96(self.tip_rack)

    self._assert_command_sent_once(
      "C0EPid0208xs01179xd0yh2418tt01wu0za2164zh2450ze2450",
                "xs#####xd#yh####tt##wu#za####zh####ze####")

  def test_core_96_tip_drop(self):
    self.lh.drop_tips96(self.tip_rack)

    self._assert_command_sent_once(
      "C0ERid0213xs01179xd0yh2418za2164zh2450ze2450",
                "xs#####xd#yh####za####zh####ze####")

  def test_core_96_aspirate(self):
    # TODO: Hamilton liquid classes
    self.lh.aspirate_plate(self.plate, 100*1.072)

    self._assert_command_sent_once(
      "C0EAid0001aa0xs02980xd0yh1460zh2450ze2450lz1999zt1881zm1269iw000ix0fh000af01072ag2500vt050"
      "bv00000wv00050cm0cs1bs0020wh10hv00000hc00hp000hs1200zv0032zq06180mj000cj0cx0cr000"
      "cwFFFFFFFFFFFFFFFFFFFFFFFFpp0100",
      "xs#####xd#yh####zh####ze####lz####zt####zm####iw###ix#fh###af#####ag####vt###"
      "bv#####wv#####cm#cs#bs####wh##hv#####hc##hp###hs####zv####zq#####mj###cj#cx#cr###"
      "cw************************pp####")

  def test_core_96_dispense(self):
    # TODO: Hamilton liquid classes
    self.lh.dispense_plate(self.plate, 100*1.072)

    self._assert_command_sent_once(
      "C0EDid0001da3xs02980xd0yh1460zh2450ze2450lz1999zt1881zm1869iw000ix0fh000df01072dg1200vt050"
      "bv00000cm0cs1bs0020wh00hv00000hc00hp000hs1200es0050ev000zv0032ej00zq06180mj000cj0cx0cr000"
      "cwFFFFFFFFFFFFFFFFFFFFFFFFpp0100",
      "da#xs#####xd#yh##6#zh####ze####lz####zt####zm##6#iw###ix#fh###df#####dg####vt###"
      "bv#####cm#cs#bs####wh##hv#####hc##hp###hs####es####ev###zv####ej##zq#6###mj###cj#cx#cr###"
      "cw************************pp####")

  def test_iswap(self):
    self.lh.move_plate(self.plate, self.plt_car[2])
    self._assert_command_sent_once(
      "C0PPid0011xs03475xd0yj1145yd0zj1874zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#")
    self._assert_command_sent_once(
      "C0PRid0012xs03475xd0yj3065yd0zj1874zd0th2840te2840gr1go1300ga0",
      "xs#####xd#yj####yd#zj####zd#th####te####go####ga#")

  def test_iswap_plate_reader(self):
    plate_reader = PlateReader(name="plate_reader")
    self.lh.deck.assign_child_resource(plate_reader,
      location=Coordinate(979.5, 285.2-63, 200 - 100))

    self.lh.move_plate(self.plate, plate_reader, pickup_distance_from_top=12.2,
      get_direction=Move.Direction.FRONT, put_direction=Move.Direction.LEFT)
    self._assert_command_sent_once(
      "C0PPid0003xs03475xd0yj1145yd0zj1884zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
                "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#")
    self._assert_command_sent_once(
      "C0PRid0004xs10430xd0yj3282yd0zj2023zd0th2840te2840gr4go1300ga0",
                "xs#####xd#yj####yd#zj####zd#th####te####gr#go####ga#")

    self.lh.move_plate(plate_reader.get_plate(), self.plt_car[0], pickup_distance_from_top=14.2,
      get_direction=Move.Direction.LEFT, put_direction=Move.Direction.FRONT)
    self._assert_command_sent_once(
      "C0PPid0005xs10430xd0yj3282yd0zj2003zd0gr4th2840te2840gw4go1300gb1237gt20ga0gc1",
                "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#")
    self._assert_command_sent_once(
      "C0PRid0006xs03475xd0yj1145yd0zj1864zd0th2840te2840gr1go1300ga0",
                "xs#####xd#yj####yd#zj####zd#th####te####gr#go####ga#")

  def test_iswap_move_lid(self):
    assert self.plate.lid is not None and self.other_plate.lid is not None
    self.other_plate.lid.unassign() # remove lid from plate
    self.lh.move_lid(self.plate.lid, self.other_plate)

    get_plate_fmt = "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#"
    put_plate_fmt = "xs#####xd#yj####yd#zj####zd#th####te####gr#go####ga#"

    self._assert_command_sent_once(
      "C0PPid0002xs03475xd0yj1145yd0zj1949zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      get_plate_fmt)
    self._assert_command_sent_once( # zj sent = 1849
      "C0PRid0003xs03475xd0yj2105yd0zj1949zd0th2840te2840gr1go1300ga0", put_plate_fmt)

  def test_iswap_stacking_area(self):
    stacking_area = ResourceStack("stacking_area", direction="z")
    # for some reason it was like this at some point
    # self.lh.assign_resource(hotel, location=Coordinate(6, 414-63, 217.2 - 100))
    # self.lh.deck.assign_child_resource(hotel, location=Coordinate(6, 414-63, 231.7 - 100 +4.5))
    self.lh.deck.assign_child_resource(stacking_area, location=Coordinate(6, 414-63, 226.2 - 100))

    get_plate_fmt = "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#"
    put_plate_fmt = "xs#####xd#yj####yd#zj####zd#th####te####gr#go####ga#"

    assert self.plate.lid is not None
    self.lh.move_lid(self.plate.lid, stacking_area)
    self._assert_command_sent_once(
      "C0PPid0002xs03475xd0yj1145yd0zj1949zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
        get_plate_fmt)
    self._assert_command_sent_once(
      "C0PRid0003xs00695xd0yj4570yd0zj2305zd0th2840te2840gr1go1300ga0", put_plate_fmt)

    # Move lids back (reverse order)
    self.lh.move_lid(cast(Lid, stacking_area.get_top_item()), self.plate)
    self._assert_command_sent_once(
      "C0PPid0004xs00695xd0yj4570yd0zj2305zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      get_plate_fmt)
    self._assert_command_sent_once(
      "C0PRid0005xs03475xd0yj1145yd0zj1949zd0th2840te2840gr1go1300ga0", put_plate_fmt)

  def test_iswap_stacking_area_2lids(self):
    # for some reason it was like this at some point
    # self.lh.assign_resource(hotel, location=Coordinate(6, 414-63, 217.2 - 100))
    stacking_area = ResourceStack("stacking_area", direction="z")
    self.lh.deck.assign_child_resource(stacking_area, location=Coordinate(6, 414-63, 226.2 - 100))

    get_plate_fmt = "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#"
    put_plate_fmt = "xs#####xd#yj####yd#zj####zd#th####te####gr#go####ga#"

    assert self.plate.lid is not None and self.other_plate.lid is not None

    self.lh.move_lid(self.plate.lid, stacking_area)
    self._assert_command_sent_once(
      "C0PPid0002xs03475xd0yj1145yd0zj1949zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
        get_plate_fmt)
    self._assert_command_sent_once(
      "C0PRid0003xs00695xd0yj4570yd0zj2305zd0th2840te2840gr1go1300ga0", put_plate_fmt)

    self.lh.move_lid(self.other_plate.lid, stacking_area)
    self._assert_command_sent_once(
      "C0PPid0004xs03475xd0yj2105yd0zj1949zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
        get_plate_fmt)
    self._assert_command_sent_once(
      "C0PRid0005xs00695xd0yj4570yd0zj2405zd0th2840te2840gr1go1300ga0", put_plate_fmt)

    # Move lids back (reverse order)
    top_item = stacking_area.get_top_item()
    assert isinstance(top_item, Lid)
    self.lh.move_lid(top_item, self.plate)
    self._assert_command_sent_once(
      "C0PPid0004xs00695xd0yj4570yd0zj2405zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      get_plate_fmt)
    self._assert_command_sent_once(
      "C0PRid0005xs03475xd0yj1145yd0zj1949zd0th2840te2840gr1go1300ga0", put_plate_fmt)

    top_item = stacking_area.get_top_item()
    assert isinstance(top_item, Lid)
    self.lh.move_lid(top_item, self.other_plate)
    self._assert_command_sent_once(
      "C0PPid0004xs00695xd0yj4570yd0zj2305zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      get_plate_fmt)
    self._assert_command_sent_once(
      "C0PRid0005xs03475xd0yj2105yd0zj1949zd0th2840te2840gr1go1300ga0", put_plate_fmt)

  def test_discard_tips(self):
    self.lh.pick_up_tips(self.tip_rack["A1:H1"])
    self.lh.discard_tips()
    self._assert_command_sent_once(
     "C0TRid0206xp08000 08000 08000 08000 08000 08000 08000 08000yp4050 3782 3514 3246 2978 2710 "
     "2442 2174tp1970tz1890th2450te2450tm1 1 1 1 1 1 1 1ti0",
     DROP_TIP_FORMAT)



if __name__ == "__main__":
  unittest.main()
