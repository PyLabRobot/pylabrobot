""" Tests for Hamilton backend. """

import unittest

from pylabrobot.liquid_handling.liquid_handler import LiquidHandler
from pylabrobot.liquid_handling.resources import (
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cos_96_EZWash,
  Coordinate,
  PlateReader,
  Hotel
)
from pylabrobot.liquid_handling.resources.ml_star import STF_L

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
    self.lh = LiquidHandler(self.mockSTAR)

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[1] = STF_L(name="tips_01")
    self.lh.assign_resource(self.tip_car, rails=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = Cos_96_EZWash(name="plate_01", with_lid=True)
    self.plt_car[1] = Cos_96_EZWash(name="plate_02", with_lid=True)
    self.lh.assign_resource(self.plt_car, rails=9)

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
    # pylint: disable=protected-access
    resource = self.lh.get_resource("tips_01")
    self.assertEqual(
      self.mockSTAR._channel_positions_to_fw_positions(resource["A1"]),
      ([1179, 0], [2418, 0], [True, False])
    )

    self.assertEqual(
      self.mockSTAR._channel_positions_to_fw_positions(resource["A1", "F1"]),
      ([1179, 1179, 0], [2418, 1968, 0], [True, True, False])
    )

  def _assert_command_sent_once(self, cmd: str, fmt: str):
    """ Assert that the given command was sent to the backend exactly once. """
    self._assert_command_in_command_buffer(cmd, True, fmt)
    self._assert_command_in_command_buffer(cmd, False, fmt)

  def test_tip_definition(self):
    pass

  def test_tip_pickup_01(self):
    self.lh.pickup_tips(self.tip_car[1].resource["A1", "B1"])
    self._assert_command_sent_once(
      "C0TPid0000xp01179 01179 00000&yp2418 2328 0000tm1 1 0&tt01tp2244tz2164th2450td0",
      "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_tip_pickup_56(self):
    self.lh.pickup_tips([None] * 4 + self.tip_car[1].resource["E1", "F1"])
    self._assert_command_sent_once(
      "C0TPid0000xp00000 00000 00000 00000 01179 01179 00000&yp0000 0000 0000 0000 2058 1968 "
      "0000&tm0 0 0 0 1 1 0 &tt01tp2244tz2164th2450td0",
      "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_tip_pickup_15(self):
    tips = self.tip_car[1].resource
    self.lh.pickup_tips(tips["A1"] + [None] * 3 + tips["F1"])
    self._assert_command_sent_once(
      "C0TPid0000xp01179 00000 00000 00000 01179 00000&yp2418 0000 0000 0000 1968 0000 "
      "&tm1 0 0 0 1 0&tt01tp2244tz2164th2450td0",
      "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#")

  def test_tip_discard_56(self):
    self.test_tip_pickup_56() # pick up tips first
    tips = self.tip_car[1].resource
    self.lh.discard_tips([None] * 4 + tips["E1", "F1"])
    self._assert_command_sent_once(
      "C0TRid0000xp00000 00000 00000 00000 01179 01179 00000&yp0000 0000 0000 0000 2058 1968 "
      "0000&tm0 0 0 0 1 1 0&tt01tp1314tz1414th2450ti0",
      "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####ti#")

  def test_single_channel_aspiration(self):
    self.lh.aspirate(self.plt_car[0].resource["A1"], vols=[100])

    # This passes the test, but is not the real command.
    self._assert_command_sent_once(
      "C0ASid0000at0&tm1 0&xp02980 00000&yp1460 0000&th2450te2450lp1931&ch000&zl1881&"
      "zx1831&ip0000&it0&fp0000&av01072&as1000&ta000&ba0000&oa000&lm0&ll1&lv1&ld00&"
      "de0020&wt10&mv00000&mc00&mp000&ms1000&gi000&gj0gk0zu0032&zr06180&mh0000&zo000&"
      "po0100&lk0&ik0000&sd0500&se0500&sz0300&io0000&il00000&in0000&",
      fmt="at# (n)tm# (n)xp##### (n)yp#### (n)th####te####lp#### (n)ch### (n)zl#### (n)zx#### (n)"
      "ip#### (n)it# (n)fp#### (n)av#### (n)as#### (n)ta### (n)ba#### (n)oa### (n)lm# (n)ll# (n)"
      "lv# (n)ld## (n)de#### (n)wt## (n)mv##### (n)mc## (n)mp### (n)ms#### (n)gi### (n)gj#gk#"
      "zu#### (n)zr#### (n)mh#### (n)zo### (n)po#### (n)lk# (n)ik#### (n)sd#### (n)se#### (n)"
      "sz#### (n)io#### (n)il##### (n)in#### (n)")

  def test_multi_channel_aspiration(self):
    self.lh.aspirate(self.plt_car[0].resource["A1:B1"], vols=100)

    # This passes the test, but is not the real command.
    self._assert_command_sent_once(
      "C0ASid0000at0&tm1 1 0&xp02980 02980 00000&yp1460 1370 0000&th2450te2450lp1931 1931&"
      "ch000 000&zl1881 1881&zx1831 1831&ip0000 0000&it0 0&fp0000 0000&"
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
    self.lh.dispense(self.plt_car[0].resource["A1"], vols=[100])
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
    print([x.get_absolute_location() for x in self.plt_car[0].resource["A1:B1"]])
    self.lh.dispense(self.plt_car[0].resource["A1:B1"], vols=100)

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

  def test_core_96_tip_pickup(self):
    self.lh.pickup_tips96("tips_01")

    self._assert_command_sent_once(
      "C0EPid0208xs01179xd0yh2418tt01wu0za2164zh2450ze2450",
                "xs#####xd#yh####tt##wu#za####zh####ze####")

  def test_core_96_tip_discard(self):
    self.lh.discard_tips96("tips_01")

    self._assert_command_sent_once(
      "C0ERid0213xs01179xd0yh2418za2164zh2450ze2450",
                "xs#####xd#yh####za####zh####ze####")

  def test_core_96_aspirate(self):
    self.lh.aspirate96("plate_01", 100)

    self._assert_command_sent_once(
      "C0EAid0001aa0xs02980xd0yh1460zh2450ze2450lz1999zt1881zm1269iw000ix0fh000af01072ag2500vt050"
      "bv00000wv00050cm0cs1bs0020wh10hv00000hc00hp000hs1200zv0032zq06180mj000cj0cx0cr000"
      "cwFFFFFFFFFFFFFFFFFFFFFFFFpp0100",
      "xs#####xd#yh####zh####ze####lz####zt####zm####iw###ix#fh###af#####ag####vt###"
      "bv#####wv#####cm#cs#bs####wh##hv#####hc##hp###hs####zv####zq#####mj###cj#cx#cr###"
      "cw************************pp####")

  def test_core_96_dispense(self):
    self.lh.dispense96("plate_01", 100)

    self._assert_command_sent_once(
      "C0EDid0001da3xs02980xd0yh1460zh2450ze2450lz1999zt1881zm1869iw000ix0fh000df01072dg1200vt050"
      "bv00000cm0cs1bs0020wh00hv00000hc00hp000hs1200es0050ev000zv0032ej00zq06180mj000cj0cx0cr000"
      "cwFFFFFFFFFFFFFFFFFFFFFFFFpp0100",
      "da#xs#####xd#yh##6#zh####ze####lz####zt####zm##6#iw###ix#fh###df#####dg####vt###"
      "bv#####cm#cs#bs####wh##hv#####hc##hp###hs####es####ev###zv####ej##zq#6###mj###cj#cx#cr###"
      "cw************************pp####")

  def test_iswap(self):
    self.lh.move_plate(self.plt_car[0], self.plt_car[2])
    self._assert_command_sent_once(
      "C0PPid0011xs03475xd0yj1145yd0zj1874zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#")
    self._assert_command_sent_once(
      "C0PRid0012xs03475xd0yj3065yd0zj1874zd0th2840te2840gr1go1300ga0",
      "xs#####xd#yj####yd#zj####zd#th####te####go####ga#")

  def test_iswap_plate_reader(self):
    plate_reader = PlateReader(name="plate_reader")
    self.lh.assign_resource(plate_reader, location=Coordinate(979.5, 285.2-63, 200 - 100),
      replace=True)

    self.lh.move_plate(self.plt_car[0], plate_reader, pickup_distance_from_top=12.2,
      get_open_gripper_position=1320, get_grip_direction=1,
      put_grip_direction=4, put_open_gripper_position=1320)
    self._assert_command_sent_once(
      "C0PPid0003xs03475xd0yj1145yd0zj1884zd0gr1th2840te2840gw4go1320gb1237gt20ga0gc1",
                "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#")
    self._assert_command_sent_once(
      "C0PRid0004xs10430xd0yj3282yd0zj2023zd0th2840te2840gr4go1320ga0",
                "xs#####xd#yj####yd#zj####zd#th####te####gr#go####ga#")

    self.lh.move_plate(plate_reader.get_plate(), self.plt_car[0], pickup_distance_from_top=14.2,
      get_open_gripper_position=1320, get_grip_direction=4,
      put_grip_direction=1, put_open_gripper_position=1320)
    self._assert_command_sent_once(
      "C0PPid0005xs10430xd0yj3282yd0zj2003zd0gr4th2840te2840gw4go1320gb1237gt20ga0gc1",
                "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#")
    self._assert_command_sent_once(
      "C0PRid0006xs03475xd0yj1145yd0zj1864zd0th2840te2840gr1go1320ga0",
                "xs#####xd#yj####yd#zj####zd#th####te####gr#go####ga#")

  def test_iswap_hotel(self):
    hotel = Hotel("hotel", size_x=35.0, size_y=35.0, size_z=0)
    # for some reason it was like this at some point
    # self.lh.assign_resource(hotel, location=Coordinate(6, 414-63, 217.2 - 100))
    self.lh.assign_resource(hotel, location=Coordinate(6, 414-63, 231.7 - 100))

    get_plate_fmt = "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#"
    put_plate_fmt = "xs#####xd#yj####yd#zj####zd#th####te####gr#go####ga#"

    self.lh.move_lid(self.plt_car[0].resource.lid, hotel)
    self._assert_command_sent_once(
      "C0PPid0002xs03475xd0yj1145yd0zj1949zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
        get_plate_fmt)
    self._assert_command_sent_once(
      "C0PRid0003xs00695xd0yj4570yd0zj2305zd0th2840te2840gr1go1300ga0", put_plate_fmt)

    self.lh.move_lid(self.plt_car[1].resource.lid, hotel)
    self._assert_command_sent_once(
      "C0PPid0004xs03475xd0yj2105yd0zj1949zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
        get_plate_fmt)
    self._assert_command_sent_once(
      "C0PRid0005xs00695xd0yj4570yd0zj2405zd0th2840te2840gr1go1300ga0", put_plate_fmt)

    # Move lids back (reverse order)
    self.lh.move_lid(hotel.get_top_item(), self.plt_car[0].resource)
    self._assert_command_sent_once(
      "C0PPid0004xs00695xd0yj4570yd0zj2405zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      get_plate_fmt)
    self._assert_command_sent_once(
      "C0PRid0005xs03475xd0yj1145yd0zj1949zd0th2840te2840gr1go1300ga0", put_plate_fmt)

    self.lh.move_lid(hotel.get_top_item(), self.plt_car[1].resource)
    self._assert_command_sent_once(
      "C0PPid0004xs00695xd0yj4570yd0zj2305zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      get_plate_fmt)
    self._assert_command_sent_once(
      "C0PRid0005xs03475xd0yj2105yd0zj1949zd0th2840te2840gr1go1300ga0", put_plate_fmt)


if __name__ == "__main__":
  unittest.main()
