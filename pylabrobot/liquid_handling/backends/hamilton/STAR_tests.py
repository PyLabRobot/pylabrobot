""" Tests for the Hamilton STAR backend. """

from typing import cast
import unittest

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.plate_reading import PlateReader
from pylabrobot.plate_reading.plate_reader_tests import MockPlateReaderBackend
from pylabrobot.resources import (
  Container,
  TIP_CAR_480_A00,
  TIP_CAR_288_C00,
  PLT_CAR_L5AC_A00,
  Cos_96_EZWash,
  HT_P,
  HTF_L,
  Coordinate,
  ResourceStack,
  Lid,
  no_volume_tracking
)
from pylabrobot.resources.hamilton import STARLetDeck
from pylabrobot.resources.ml_star import STF_L
from pylabrobot.liquid_handling.standard import Pickup, GripDirection

from tests.usb import MockDev, MockEndpoint

from .STAR import (
  STAR,
  parse_star_fw_string,
  STARFirmwareError,
  CommandSyntaxError,
  HamiltonNoTipError,
  HardwareError,
  UnknownHamiltonError
)


PICKUP_TIP_FORMAT = "xp##### (n)yp#### (n)tm# (n)tt##tp####tz####th####td#"
DROP_TIP_FORMAT = "xp##### (n)yp#### (n)tm# (n)tp####tz####th####ti#"
ASPIRATION_COMMAND_FORMAT = (
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

GET_PLATE_FMT = "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#"
PUT_PLATE_FMT = "xs#####xd#yj####yd#zj####zd#th####te####gr#go####ga#"
INTERMEDIATE_FMT = "xs#####xd#yj####yd#zj####zd#gr#th####ga#xe# #"


class TestSTARResponseParsing(unittest.TestCase):
  """ Test parsing of response from Hamilton. """

  def setUp(self):
    super().setUp()
    self.star = STAR()

  def test_parse_response_params(self):
    parsed = parse_star_fw_string("C0QMid1111", "")
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_star_fw_string("C0QMid1111", "id####")
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_star_fw_string("C0QMid1112aaabc", "aa&&&")
    self.assertEqual(parsed, {"id": 1112, "aa": "abc"})

    parsed = parse_star_fw_string("C0QMid1112aa-21", "aa##")
    self.assertEqual(parsed, {"id": 1112, "aa": -21})

    parsed = parse_star_fw_string("C0QMid1113pqABC", "pq***")
    self.assertEqual(parsed, {"id": 1113, "pq": int("ABC", base=16)})

    with self.assertRaises(ValueError):
      # should fail with auto-added id.
      parsed = parse_star_fw_string("C0QMaaabc", "")
      self.assertEqual(parsed, "")

    with self.assertRaises(ValueError):
      parse_star_fw_string("C0QM", "id####") # pylint: disable=expression-not-assigned

    with self.assertRaises(ValueError):
      parse_star_fw_string("C0RV", "") # pylint: disable=expression-not-assigned

  def test_parse_response_no_errors(self):
    parsed = parse_star_fw_string("C0QMid1111", "")
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_star_fw_string("C0QMid1111 er00/00", "")
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_star_fw_string("C0QMid1111 er00/00 P100/00", "")
    self.assertEqual(parsed, {"id": 1111})

  def test_parse_response_master_error(self):
    with self.assertRaises(STARFirmwareError) as ctx:
      self.star.check_fw_string_error("C0QMid1111 er01/30")
    e = ctx.exception
    self.assertEqual(len(e), 1)
    assert "Master" in e
    self.assertIsInstance(e["Master"], CommandSyntaxError)
    self.assertEqual(e["Master"].message, "Unknown command")

  def test_parse_response_slave_errors(self):
    with self.assertRaises(STARFirmwareError) as ctx:
      self.star.check_fw_string_error("C0QMid1111 er99/00 P100/00 P235/00 P402/98 PG08/76")
    e = ctx.exception
    self.assertEqual(len(e), 3)
    assert "Master" not in e
    assert "Pipetting channel 1" not in e
    self.assertEqual(e["Pipetting channel 2"].raw_response, "35/00")
    self.assertEqual(e["Pipetting channel 4"].raw_response, "02/98")
    self.assertEqual(e["Pipetting channel 16"].raw_response, "08/76")

    self.assertIsInstance(e["Pipetting channel 2"], UnknownHamiltonError)
    self.assertIsInstance(e["Pipetting channel 4"], HardwareError)
    self.assertIsInstance(e["Pipetting channel 16"], HamiltonNoTipError)

    self.assertEqual(e["Pipetting channel 2"].message, "No error")
    self.assertEqual(e["Pipetting channel 4"].message, "Unknown trace information code 98")
    self.assertEqual(e["Pipetting channel 16"].message, "Tip already picked up")

  def test_parse_slave_response_errors(self):
    with self.assertRaises(STARFirmwareError) as ctx:
      self.star.check_fw_string_error("P1OQid1111er30")

    e = ctx.exception
    self.assertEqual(len(e), 1)
    assert "Master" not in e
    assert "Pipetting channel 1" in e
    self.assertIsInstance(e["Pipetting channel 1"], UnknownHamiltonError)
    self.assertEqual(e["Pipetting channel 1"].message, "Unknown command")


class STARUSBCommsMocker(STAR):
  """ Mocks PyUSB """

  async def setup(self, send_response):
    self.dev = MockDev(send_response)
    self.read_endpoint = MockEndpoint()
    self.write_endpoint = MockEndpoint()


class TestSTARUSBComms(unittest.IsolatedAsyncioTestCase):
  """ Test that USB data is parsed correctly. """

  async def test_send_command_correct_response(self):
    star = STARUSBCommsMocker()
    await star.setup(send_response="C0QMid0001") # correct response
    resp = await star.send_command("C0", command="QM", fmt="id####")
    self.assertEqual(resp, {"id": 1})

  async def test_send_command_wrong_id(self):
    star = STARUSBCommsMocker(read_timeout=2, packet_read_timeout=1)
    await star.setup(send_response="C0QMid0000") # wrong response
    with self.assertRaises(TimeoutError):
      await star.send_command("C0", command="QM")

  async def test_send_command_plaintext_response(self):
    star = STARUSBCommsMocker(read_timeout=2, packet_read_timeout=1)
    await star.setup(send_response="this is plain text") # wrong response
    with self.assertRaises(TimeoutError):
      await star.send_command("C0", command="QM")


class STARCommandCatcher(STAR):
  """ Mock backend for star that catches commands and saves them instead of sending them to the
  machine. """

  def __init__(self):
    super().__init__()
    self.commands = []

  async def setup(self) -> None:
    self._num_channels = 8
    self.iswap_installed = True
    self.core96_head_installed = True
    self._core_parked = True

  async def send_command(self, module, command, tip_pattern=None, fmt="", read_timeout=0,
    write_timeout=0, **kwargs):
    cmd, _ = self._assemble_command(module, command, tip_pattern, **kwargs)
    self.commands.append(cmd)

  async def stop(self):
    self.stop_finished = True


class TestSTARLiquidHandlerCommands(unittest.IsolatedAsyncioTestCase):
  """ Test STAR backend for liquid handling. """

  async def asyncSetUp(self):
    # pylint: disable=invalid-name
    self.mockSTAR = STARCommandCatcher()
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.mockSTAR, deck=self.deck)

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[1] = self.tip_rack = STF_L(name="tip_rack_01")
    self.tip_car[2] = self.tip_rack2 = HTF_L(name="tip_rack_02")
    self.deck.assign_child_resource(self.tip_car, rails=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = Cos_96_EZWash(name="plate_01", with_lid=True)
    self.plt_car[1] = self.other_plate = Cos_96_EZWash(name="plate_02", with_lid=True)
    self.deck.assign_child_resource(self.plt_car, rails=9)

    class BlueBucket(Container):
      def __init__(self, name: str):
        super().__init__(name, size_x=123, size_y=82, size_z=75, category="bucket",
          max_volume=123 * 82 * 75)
    self.bb = BlueBucket(name="blue bucket")
    self.deck.assign_child_resource(self.bb, location=Coordinate(425, 141.5, 120))

    self.maxDiff = None

    await self.lh.setup()

  async def asyncTearDown(self):
    await self.lh.stop()

  def _assert_command_in_command_buffer(self, cmd: str, should_be: bool, fmt: str):
    """ Assert that the given command was sent to the backend. The ordering of the parameters is not
    taken into account, but the values and formatting should match. The id parameter of the command
    is ignored.

    If a command is found, it is removed from the command buffer.

    Args:
      cmd: the command to look for
      should_be: whether the command should be found or not
      fmt: the format of the command
    """

    found = False
    # Command that fits the format, but is not the same as the command we are looking for.
    similar = None

    parsed_cmd = parse_star_fw_string(cmd, fmt)
    parsed_cmd.pop("id")

    for sent_cmd in self.mockSTAR.commands:
      # When the module and command do not match, there is no point in comparing the parameters.
      if sent_cmd[0:4] != cmd[0:4]:
        continue

      try:
        parsed_sent_cmd = parse_star_fw_string(sent_cmd, fmt)
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

  async def test_indictor_light(self):
    """ Test the indicator light. """
    await self.mockSTAR.set_loading_indicators(bit_pattern=[True]*54, blink_pattern=[False]*54)
    self._assert_command_sent_once("C0CPid0000cl3FFFFFFFFFFFFFcb00000000000000",
                                             "cl**************cb**************")

  def test_ops_to_fw_positions(self):
    """ Convert channel positions to firmware positions. """
    # pylint: disable=protected-access
    tip_a1 = self.tip_rack.get_item("A1")
    tip_f1 = self.tip_rack.get_item("F1")
    tip = self.tip_rack.get_tip("A1")

    op1 = Pickup(resource=tip_a1, tip=tip, offset=Coordinate.zero())
    op2 = Pickup(resource=tip_f1, tip=tip, offset=Coordinate.zero())
    self.assertEqual(
      self.mockSTAR._ops_to_fw_positions((op1,), use_channels=[0]),
      ([1179, 0], [2418, 0], [True, False])
    )

    self.assertEqual(
      self.mockSTAR._ops_to_fw_positions((op1, op2), use_channels=[0, 1]),
      ([1179, 1179, 0], [2418, 1968, 0], [True, True, False])
    )

    self.assertEqual(
      self.mockSTAR._ops_to_fw_positions((op1, op2), use_channels=[1, 2]),
      ([0, 1179, 1179, 0], [0, 2418, 1968, 0], [False, True, True, False])
    )

    # check two operations on the same row, different column.
    tip_a2 = self.tip_rack.get_item("A2")
    op3 = Pickup(resource=tip_a2, tip=tip, offset=Coordinate.zero())
    self.assertEqual(
      self.mockSTAR._ops_to_fw_positions((op1, op3), use_channels=[0, 1]),
      ([1179, 1269, 0], [2418, 2418, 0], [True, True, False])
    )

    # A1, A2, B1, B2
    tip_b1 = self.tip_rack.get_item("B1")
    op4 = Pickup(resource=tip_b1, tip=tip, offset=Coordinate.zero())
    tip_b2 = self.tip_rack.get_item("B2")
    op5 = Pickup(resource=tip_b2, tip=tip, offset=Coordinate.zero())
    self.assertEqual(
      self.mockSTAR._ops_to_fw_positions((op1, op4, op3, op5), use_channels=[0, 1, 2, 3]),
      ([1179, 1179, 1269, 1269, 0], [2418, 2328, 2418, 2328, 0], [True, True, True, True, False])
    )

    # make sure two operations on the same spot are not allowed
    with self.assertRaises(ValueError):
      self.mockSTAR._ops_to_fw_positions((op1, op1), use_channels=[0, 1])

  def _assert_command_sent_once(self, cmd: str, fmt: str):
    """ Assert that the given command was sent to the backend exactly once. """
    self._assert_command_in_command_buffer(cmd, True, fmt)
    self._assert_command_in_command_buffer(cmd, False, fmt)

  def test_tip_definition(self):
    pass

  async def test_tip_pickup_01(self):
    await self.lh.pick_up_tips(self.tip_rack["A1", "B1"])
    self._assert_command_sent_once(
      "C0TPid0000xp01179 01179 00000&yp2418 2328 0000tm1 1 0&tt01tp2243tz2163th2450td0",
      PICKUP_TIP_FORMAT)

  async def test_tip_pickup_56(self):
    await self.lh.pick_up_tips(self.tip_rack["E1", "F1"], use_channels=[4, 5])
    self._assert_command_sent_once(
      "C0TPid0000xp00000 00000 00000 00000 01179 01179 00000&yp0000 0000 0000 0000 2058 1968 "
      "0000&tm0 0 0 0 1 1 0 &tt01tp2243tz2163th2450td0",
      PICKUP_TIP_FORMAT)

  async def test_tip_pickup_15(self):
    await self.lh.pick_up_tips(self.tip_rack["A1", "F1"], use_channels=[0, 4])
    self._assert_command_sent_once(
      "C0TPid0000xp01179 00000 00000 00000 01179 00000&yp2418 0000 0000 0000 1968 0000 "
      "&tm1 0 0 0 1 0&tt01tp2243tz2163th2450td0",
      PICKUP_TIP_FORMAT)

  async def test_tip_drop_56(self):
    await self.test_tip_pickup_56() # pick up tips first
    await self.lh.drop_tips(self.tip_rack["E1", "F1"], use_channels=[4, 5])
    self._assert_command_sent_once(
      "C0TRid0000xp00000 00000 00000 00000 01179 01179 00000&yp0000 0000 0000 0000 2058 1968 "
      "0000&tm0 0 0 0 1 1 0&tp2243tz2163th2450ti1", DROP_TIP_FORMAT)

  async def test_aspirate56(self):
    self.maxDiff = None
    await self.test_tip_pickup_56() # pick up tips first
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    for well in self.plate.get_items(["A1", "B1"]):
      well.tracker.set_liquids([(None, 100 * 1.072)]) # liquid class correction
    await self.lh.aspirate(self.plate["A1", "B1"], vols=[100, 100], use_channels=[4, 5])
    self._assert_command_sent_once("C0ASid0004at0 0 0 0 0 0 0&tm0 0 0 0 1 1 0&xp00000 00000 00000 "
      "00000 02980 02980 00000&yp0000 0000 0000 0000 1460 1370 0000&th2450te2450lp2011 2011 2011 "
      "2011 2011 2011 2011&ch000 000 000 000 000 000 000&zl1881 1881 1881 1881 1881 1881 1881&"
      "po0100 0100 0100 0100 0100 0100 0100&zu0032 0032 0032 0032 0032 0032 0032&zr06180 06180 "
      "06180 06180 06180 06180 06180&zx1831 1831 1831 1831 1831 1831 1831&ip0000 0000 0000 0000 "
      "0000 0000 0000&it0 0 0 0 0 0 0&fp0000 0000 0000 0000 0000 0000 0000&av01072 01072 01072 "
      "01072 01072 01072 01072&as1000 1000 1000 1000 1000 1000 1000&ta000 000 000 000 000 000 000&"
      "ba0000 0000 0000 0000 0000 0000 0000&oa000 000 000 000 000 000 000&lm0 0 0 0 0 0 0&ll1 1 1 "
      "1 1 1 1&lv1 1 1 1 1 1 1&zo000 000 000 000 000 000 000&ld00 00 00 00 00 00 00&de0020 0020 "
      "0020 0020 0020 0020 0020&wt10 10 10 10 10 10 10&mv00000 00000 00000 00000 00000 00000 00000&"
      "mc00 00 00 00 00 00 00&mp000 000 000 000 000 000 000&ms1000 1000 1000 1000 1000 1000 1000&"
      "mh0000 0000 0000 0000 0000 0000 0000&gi000 000 000 000 000 000 000&gj0gk0lk0 0 0 0 0 0 0&"
      "ik0000 0000 0000 0000 0000 0000 0000&sd0500 0500 0500 0500 0500 0500 0500&se0500 0500 0500 "
      "0500 0500 0500 0500&sz0300 0300 0300 0300 0300 0300 0300&io0000 0000 0000 0000 0000 0000 0"
      "000&il00000 00000 00000 00000 00000 00000 00000&in0000 0000 0000 0000 0000 0000 0000&",
      ASPIRATION_COMMAND_FORMAT)

  async def test_single_channel_aspiration(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    well = self.plate.get_item("A1")
    well.tracker.set_liquids([(None, 100 * 1.072)]) # liquid class correction
    await self.lh.aspirate([well], vols=[100])

    # This passes the test, but is not the real command.
    self._assert_command_sent_once(
      "C0ASid0002at0 0&tm1 0&xp02980 00000&yp1460 0000&th2450te2450lp2011 2011&ch000 000&zl1881 "
      "1881&po0100 0100&zu0032 0032&zr06180 06180&zx1831 1831&ip0000 0000&it0 0&fp0000 0000&"
      "av01072 01072&as1000 1000&ta000 000&ba0000 0000&oa000 000&lm0 0&ll1 1&lv1 1&zo000 000&"
      "ld00 00&de0020 0020&wt10 10&mv00000 00000&mc00 00&mp000 000&ms1000 1000&mh0000 0000&"
      "gi000 000&gj0gk0lk0 0&ik0000 0000&sd0500 0500&se0500 0500&sz0300 0300&io0000 0000&"
      "il00000 00000&in0000 0000&",
      fmt=ASPIRATION_COMMAND_FORMAT)

  async def test_single_channel_aspiration_liquid_height(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
    # TODO: Hamilton liquid classes
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    well = self.plate.get_item("A1")
    well.tracker.set_liquids([(None, 100 * 1.072)]) # liquid class correction
    await self.lh.aspirate([well], vols=[100], liquid_height=10)

    # This passes the test, but is not the real command.
    self._assert_command_sent_once(
      "C0ASid0002at0 0&tm1 0&xp02980 00000&yp1460 0000&th2450te2450lp2011 2011&ch000 000&zl1971 "
      "1971&po0100 0100&zu0032 0032&zr06180 06180&zx1921 1921&ip0000 0000&it0 0&fp0000 0000&"
      "av01072 01072&as1000 1000&ta000 000&ba0000 0000&oa000 000&lm0 0&ll1 1&lv1 1&zo000 000&"
      "ld00 00&de0020 0020&wt10 10&mv00000 00000&mc00 00&mp000 000&ms1000 1000&mh0000 0000&"
      "gi000 000&gj0gk0lk0 0&ik0000 0000&sd0500 0500&se0500 0500&sz0300 0300&io0000 0000&"
      "il00000 00000&in0000 0000&",
      fmt=ASPIRATION_COMMAND_FORMAT)

  async def test_multi_channel_aspiration(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1"), 1: self.tip_rack.get_tip("B1")})
    # TODO: Hamilton liquid classes
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    wells = self.plate.get_items("A1:B1")
    for well in wells:
      well.tracker.set_liquids([(None, 100 * 1.072)]) # liquid class correction
    await self.lh.aspirate(self.plate["A1:B1"], vols=100)

    # This passes the test, but is not the real command.
    self._assert_command_sent_once(
      "C0ASid0002at0 0 0&tm1 1 0&xp02980 02980 00000&yp1460 1370 0000&th2450te2450lp2011 2011 2011&"
      "ch000 000 000&zl1881 1881 1881&po0100 0100 0100&zu0032 0032 0032&zr06180 06180 06180&"
      "zx1831 1831 1831&ip0000 0000 0000&it0 0 0&fp0000 0000 0000&av01072 01072 01072&as1000 1000 "
      "1000&ta000 000 000&ba0000 0000 0000&oa000 000 000&lm0 0 0&ll1 1 1&lv1 1 1&zo000 000 000&"
      "ld00 00 00&de0020 0020 0020&wt10 10 10&mv00000 00000 00000&mc00 00 00&mp000 000 000&"
      "ms1000 1000 1000&mh0000 0000 0000&gi000 000 000&gj0gk0lk0 0 0&ik0000 0000 0000&sd0500 0500 "
      "0500&se0500 0500 0500&sz0300 0300 0300&io0000 0000 0000&il00000 00000 00000&in0000 0000 "
      "0000&",
      fmt=ASPIRATION_COMMAND_FORMAT)

  async def test_aspirate_single_resource(self):
    self.lh.update_head_state({i: self.tip_rack.get_tip(i) for i in range(5)})
    with no_volume_tracking():
      await self.lh.aspirate(self.bb, vols=10, use_channels=[0, 1, 2, 3, 4], liquid_height=1)
    self._assert_command_sent_once(
      "C0ASid0002at0 0 0 0 0 0&tm1 1 1 1 1 0&xp04865 04865 04865 04865 04865 00000&yp2098 1961 "
      "1825 1688 1551 0000&th2450te2450lp2000 2000 2000 2000 2000 2000&ch000 000 000 000 000 000&"
      "zl1210 1210 1210 1210 1210 1210&po0100 0100 0100 0100 0100 0100&zu0032 0032 0032 0032 0032 "
      "0032&zr06180 06180 06180 06180 06180 06180&zx1160 1160 1160 1160 1160 1160&ip0000 0000 0000 "
      "0000 0000 0000&it0 0 0 0 0 0&fp0000 0000 0000 0000 0000 0000&av00119 00119 00119 00119 "
      "00119 00119&as1000 1000 1000 1000 1000 1000&ta000 000 000 000 000 000&ba0000 0000 0000 0000 "
      "0000 0000&oa000 000 000 000 000 000&lm0 0 0 0 0 0&ll1 1 1 1 1 1&lv1 1 1 1 1 1&zo000 000 000 "
      "000 000 000&ld00 00 00 00 00 00&de0020 0020 0020 0020 0020 0020&wt10 10 10 10 10 10&mv00000 "
      "00000 00000 00000 00000 00000&mc00 00 00 00 00 00&mp000 000 000 000 000 000&ms1000 1000 "
      "1000 1000 1000 1000&mh0000 0000 0000 0000 0000 0000&gi000 000 000 000 000 000&gj0gk0lk0 0 0 "
      "0 0 0&ik0000 0000 0000 0000 0000 0000&sd0500 0500 0500 0500 0500 0500&se0500 0500 0500 0500 "
      "0500 0500&sz0300 0300 0300 0300 0300 0300&io0000 0000 0000 0000 0000 0000&il00000 00000 "
      "00000 00000 00000 00000&in0000 0000 0000 0000 0000 0000&",
      fmt=ASPIRATION_COMMAND_FORMAT)

  async def test_dispense_single_resource(self):
    self.lh.update_head_state({i: self.tip_rack.get_tip(i) for i in range(5)})
    with no_volume_tracking():
      await self.lh.dispense(self.bb, vols=10, use_channels=[0, 1, 2, 3, 4], liquid_height=1,
                             blow_out=[True]*5, jet=[True]*5)
    self._assert_command_sent_once(
      "C0DSid0002dm1 1 1 1 1 1&tm1 1 1 1 1 0&xp04865 04865 04865 04865 04865 00000&yp2098 1961 "
      "1825 1688 1551 0000&zx1260 1260 1260 1260 1260 1260&lp2000 2000 2000 2000 2000 2000&zl1210 "
      "1210 1210 1210 1210 1210&po0100 0100 0100 0100 0100 0100&ip0000 0000 0000 0000 0000 0000&"
      "it0 0 0 0 0 0&fp0000 0000 0000 0000 0000 0000&zu0032 0032 0032 0032 0032 0032&zr06180 06180 "
      "06180 06180 06180 06180&th2450te2450dv00116 00116 00116 00116 00116 00116&ds1800 1800 1800 "
      "1800 1800 1800&ss0050 0050 0050 0050 0050 0050&rv000 000 000 000 000 000&ta050 050 050 050 "
      "050 050&ba0000 0000 0000 0000 0000 0000&lm0 0 0 0 0 0&dj00zo000 000 000 000 000 000&ll1 1 1 "
      "1 1 1&lv1 1 1 1 1 1&de0010 0010 0010 0010 0010 0010&wt00 00 00 00 00 00&mv00000 00000 00000 "
      "00000 00000 00000&mc00 00 00 00 00 00&mp000 000 000 000 000 000&ms0010 0010 0010 0010 0010 "
      "0010&mh0000 0000 0000 0000 0000 0000&gi000 000 000 000 000 000&gj0gk0",
      fmt=DISPENSE_RESPONSE_FORMAT)

  async def test_single_channel_dispense(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    with no_volume_tracking():
      await self.lh.dispense(self.plate["A1"], vols=[100], jet=[True], blow_out=[True])
    self._assert_command_sent_once(
      "C0DSid0002dm1 1&tm1 0&xp02980 00000&yp1460 0000&zx1931 1931&lp2011 2011&zl1881 1881&"
      "po0100 0100&ip0000 0000&it0 0&fp0000 0000&zu0032 0032&zr06180 06180&th2450te2450"
      "dv01072 01072&ds1800 1800&ss0050 0050&rv000 000&ta050 050&ba0000 0000&lm0 0&"
      "dj00zo000 000&ll1 1&lv1 1&de0010 0010&wt00 00&mv00000 00000&mc00 00&mp000 000&"
      "ms0010 0010&mh0000 0000&gi000 000&gj0gk0",
      fmt=DISPENSE_RESPONSE_FORMAT)

  async def test_multi_channel_dispense(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1"), 1: self.tip_rack.get_tip("B1")})
    # TODO: Hamilton liquid classes
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    with no_volume_tracking():
      await self.lh.dispense(self.plate["A1:B1"], vols=100, jet=[True]*2, blow_out=[True]*2)

    self._assert_command_sent_once(
      "C0DSid0002dm1 1 1&tm1 1 0&xp02980 02980 00000&yp1460 1370 0000&zx1931 1931 1931&lp2011 2011 "
      "2011&zl1881 1881 1881&po0100 0100 0100&ip0000 0000 0000&it0 0 0&fp0000 0000 0000&zu0032 "
      "0032 0032&zr06180 06180 06180&th2450te2450dv01072 01072 01072&ds1800 1800 1800&"
      "ss0050 0050 0050&rv000 000 000&ta050 050 050&ba0000 0000 0000&lm0 0 0&dj00zo000 000 000&"
      "ll1 1 1&lv1 1 1&de0010 0010 0010&wt00 00 00&mv00000 00000 00000&mc00 00 00&mp000 000 000&"
      "ms0010 0010 0010&mh0000 0000 0000&gi000 000 000&gj0gk0",
      fmt=DISPENSE_RESPONSE_FORMAT)

  async def test_core_96_tip_pickup(self):
    await self.lh.pick_up_tips96(self.tip_rack)

    self._assert_command_sent_once(
      "C0EPid0208xs01179xd0yh2418tt01wu0za2164zh2450ze2450",
                "xs#####xd#yh####tt##wu#za####zh####ze####")

  async def test_core_96_tip_drop(self):
    await self.lh.pick_up_tips96(self.tip_rack) # pick up tips first
    await self.lh.drop_tips96(self.tip_rack)

    self._assert_command_sent_once(
      "C0ERid0213xs01179xd0yh2418za2164zh2450ze2450",
                "xs#####xd#yh####za####zh####ze####")

  async def test_core_96_aspirate(self):
    await self.lh.pick_up_tips96(self.tip_rack2) # pick up high volume tips

    # TODO: Hamilton liquid classes
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    await self.lh.aspirate_plate(self.plate, volume=100, blow_out=True)

    # volume used to be 01072, but that was generated using a non-core liquid class.
    self._assert_command_sent_once(
      "C0EAid0001aa0xs02980xd0yh1460zh2450ze2450lz1999zt1881zm1269iw000ix0fh000af01083ag2500vt050"
      "bv00000wv00050cm0cs1bs0020wh10hv00000hc00hp000hs1200zv0032zq06180mj000cj0cx0cr000"
      "cwFFFFFFFFFFFFFFFFFFFFFFFFpp0100",
      "xs#####xd#yh####zh####ze####lz####zt####zm####iw###ix#fh###af#####ag####vt###"
      "bv#####wv#####cm#cs#bs####wh##hv#####hc##hp###hs####zv####zq#####mj###cj#cx#cr###"
      "cw************************pp####")

  async def test_core_96_dispense(self):
    await self.lh.pick_up_tips96(self.tip_rack2) # pick up high volume tips
    if self.plate.lid is not None:
      self.plate.lid.unassign()
    await self.lh.aspirate_plate(self.plate, 100, blow_out=True) # aspirate first

    with no_volume_tracking():
      await self.lh.dispense_plate(self.plate, 100, blow_out=True)

    # volume used to be 01072, but that was generated using a non-core liquid class.
    self._assert_command_sent_once(
      "C0EDid0001da3xs02980xd0yh1460zh2450ze2450lz1999zt1881zm1869iw000ix0fh000df01083dg1200vt050"
      "bv00000cm0cs1bs0020wh00hv00000hc00hp000hs1200es0050ev000zv0032ej00zq06180mj000cj0cx0cr000"
      "cwFFFFFFFFFFFFFFFFFFFFFFFFpp0100",
      "da#xs#####xd#yh##6#zh####ze####lz####zt####zm##6#iw###ix#fh###df#####dg####vt###"
      "bv#####cm#cs#bs####wh##hv#####hc##hp###hs####es####ev###zv####ej##zq#6###mj###cj#cx#cr###"
      "cw************************pp####")

  async def test_iswap(self):
    await self.lh.move_plate(self.plate, self.plt_car[2])
    self._assert_command_sent_once(
      "C0PPid0011xs03475xd0yj1145yd0zj1874zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#")
    self._assert_command_sent_once(
      "C0PRid0012xs03475xd0yj3065yd0zj1874zd0th2840te2840gr1go1300ga0",
      "xs#####xd#yj####yd#zj####zd#th####te####go####ga#")

  async def test_iswap_plate_reader(self):
    plate_reader = PlateReader(name="plate_reader", backend=MockPlateReaderBackend(),
      size_x=0, size_y=0, size_z=0)
    self.lh.deck.assign_child_resource(plate_reader,
      location=Coordinate(979.5, 285.2, 200)) # 666: 00002

    await self.lh.move_plate(self.plate, plate_reader, pickup_distance_from_top=8.2,
      get_direction=GripDirection.FRONT, put_direction=GripDirection.LEFT)
    self._assert_command_sent_once(
      "C0PPid0003xs03475xd0yj1145yd0zj1924zd0th2840te2840gw4gb1237go1300gt20gr1ga0gc1",
                "xs#####xd#yj####yd#zj####zd#th####te####gw#gb####go####gt##gr#ga#gc#")
    self._assert_command_sent_once(
      "C0PRid0004xs10430xd0yj3282yd0zj2063zd0th2840te2840go1300gr4ga0",
                "xs#####xd#yj####yd#zj####zd#th####te####go####gr#ga#")

    await self.lh.move_plate(plate_reader.get_plate(), self.plt_car[0],
      pickup_distance_from_top=8.2, get_direction=GripDirection.LEFT,
      put_direction=GripDirection.FRONT)
    self._assert_command_sent_once(
      "C0PPid0005xs10430xd0yj3282yd0zj2063zd0gr4th2840te2840gw4go1300gb1237gt20ga0gc1",
                "xs#####xd#yj####yd#zj####zd#gr#th####te####gw#go####gb####gt##ga#gc#")
    self._assert_command_sent_once(
      "C0PRid0006xs03475xd0yj1145yd0zj1924zd0th2840te2840gr1go1300ga0",
                "xs#####xd#yj####yd#zj####zd#th####te####gr#go####ga#")

  async def test_iswap_move_lid(self):
    assert self.plate.lid is not None and self.other_plate.lid is not None
    self.other_plate.lid.unassign() # remove lid from plate
    await self.lh.move_lid(self.plate.lid, self.other_plate)

    self._assert_command_sent_once(
      "C0PPid0002xs03475xd0yj1145yd0zj1949zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      GET_PLATE_FMT)
    self._assert_command_sent_once( # zj sent = 1849
      "C0PRid0003xs03475xd0yj2105yd0zj1949zd0th2840te2840gr1go1300ga0", PUT_PLATE_FMT)

  async def test_iswap_stacking_area(self):
    stacking_area = ResourceStack("stacking_area", direction="z")
    # for some reason it was like this at some point
    # self.lh.assign_resource(hotel, location=Coordinate(6, 414-63, 217.2 - 100))
    # self.lh.deck.assign_child_resource(hotel, location=Coordinate(6, 414-63, 231.7 - 100 +4.5))
    self.lh.deck.assign_child_resource(stacking_area, location=Coordinate(6, 414, 226.2))

    assert self.plate.lid is not None
    await self.lh.move_lid(self.plate.lid, stacking_area)
    self._assert_command_sent_once(
      "C0PPid0002xs03475xd0yj1145yd0zj1949zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
        GET_PLATE_FMT)
    self._assert_command_sent_once(
      "C0PRid0003xs00695xd0yj4570yd0zj2305zd0th2840te2840gr1go1300ga0", PUT_PLATE_FMT)

    # Move lids back (reverse order)
    await self.lh.move_lid(cast(Lid, stacking_area.get_top_item()), self.plate)
    self._assert_command_sent_once(
      "C0PPid0004xs00695xd0yj4570yd0zj2305zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      GET_PLATE_FMT)
    self._assert_command_sent_once(
      "C0PRid0005xs03475xd0yj1145yd0zj1949zd0th2840te2840gr1go1300ga0", PUT_PLATE_FMT)

  async def test_iswap_stacking_area_2lids(self):
    # for some reason it was like this at some point
    # self.lh.assign_resource(hotel, location=Coordinate(6, 414-63, 217.2 - 100))
    stacking_area = ResourceStack("stacking_area", direction="z")
    self.lh.deck.assign_child_resource(stacking_area, location=Coordinate(6, 414, 226.2))

    assert self.plate.lid is not None and self.other_plate.lid is not None

    await self.lh.move_lid(self.plate.lid, stacking_area)
    self._assert_command_sent_once(
      "C0PPid0002xs03475xd0yj1145yd0zj1949zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
        GET_PLATE_FMT)
    self._assert_command_sent_once(
      "C0PRid0003xs00695xd0yj4570yd0zj2305zd0th2840te2840gr1go1300ga0", PUT_PLATE_FMT)

    await self.lh.move_lid(self.other_plate.lid, stacking_area)
    self._assert_command_sent_once(
      "C0PPid0004xs03475xd0yj2105yd0zj1949zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
        GET_PLATE_FMT)
    self._assert_command_sent_once(
      "C0PRid0005xs00695xd0yj4570yd0zj2405zd0th2840te2840gr1go1300ga0", PUT_PLATE_FMT)

    # Move lids back (reverse order)
    top_item = stacking_area.get_top_item()
    assert isinstance(top_item, Lid)
    await self.lh.move_lid(top_item, self.plate)
    self._assert_command_sent_once(
      "C0PPid0004xs00695xd0yj4570yd0zj2405zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      GET_PLATE_FMT)
    self._assert_command_sent_once(
      "C0PRid0005xs03475xd0yj1145yd0zj1949zd0th2840te2840gr1go1300ga0", PUT_PLATE_FMT)

    top_item = stacking_area.get_top_item()
    assert isinstance(top_item, Lid)
    await self.lh.move_lid(top_item, self.other_plate)
    self._assert_command_sent_once(
      "C0PPid0004xs00695xd0yj4570yd0zj2305zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      GET_PLATE_FMT)
    self._assert_command_sent_once(
      "C0PRid0005xs03475xd0yj2105yd0zj1949zd0th2840te2840gr1go1300ga0", PUT_PLATE_FMT)

  async def test_iswap_move_with_intermediate_locations(self):
    await self.lh.move_plate(self.plate, self.plt_car[1], intermediate_locations=[
      self.plt_car[2].get_absolute_location() + Coordinate(50, 0, 50),
      self.plt_car[3].get_absolute_location() + Coordinate(-50, 0, 50),
    ])

    self._assert_command_sent_once(
      "C0PPid0023xs03475xd0yj1145yd0zj1874zd0gr1th2840te2840gw4go1300gb1237gt20ga0gc1",
      GET_PLATE_FMT)
    self._assert_command_sent_once(
      "C0PMid0025xs03975xd0yj3065yd0zj2434zd0gr1th2840ga1xe4 1", INTERMEDIATE_FMT)
    self._assert_command_sent_once(
      "C0PMid0024xs02975xd0yj4025yd0zj2434zd0gr1th2430ga1xe4 1", INTERMEDIATE_FMT)
    self._assert_command_sent_once(
      "C0PRid0026xs03475xd0yj2105yd0zj1874zd0th2430te2840gr1go1300ga0",
      PUT_PLATE_FMT)

  async def test_discard_tips(self):
    await self.lh.pick_up_tips(self.tip_rack["A1:H1"])
    await self.lh.discard_tips()
    self._assert_command_sent_once(
     "C0TRid0206xp08000 08000 08000 08000 08000 08000 08000 08000yp4050 3782 3514 3246 2978 2710 "
     "2442 2174tp1970tz1870th2450te2450tm1 1 1 1 1 1 1 1ti0",
     DROP_TIP_FORMAT)

  async def test_portrait_tip_rack_handling(self):
    # Test with an alternative setup.

    deck = STARLetDeck()
    lh = LiquidHandler(self.mockSTAR, deck=deck)
    tip_car = TIP_CAR_288_C00(name="tip carrier")
    tip_car[0] = tr = HT_P(name="tips_01")
    deck.assign_child_resource(tip_car, rails=2)
    await lh.setup()

    await lh.pick_up_tips(tr["A4:A1"])

    self._assert_command_sent_once(
     "C0TPid0035xp01360 01360 01360 01360 00000&yp1380 1290 1200 1110 0000&tm1 1 1 1 0&tt01tp2263tz"
     "2163th2450td0",
     PICKUP_TIP_FORMAT)

    await lh.drop_tips(tr["A4:A1"])

    self._assert_command_sent_once(
     "C0TRid0036xp01360 01360 01360 01360 00000&yp1380 1290 1200 1110 0000&tm1 1 1 1 0&tp2263tz"
     "2183th2450ti1",
     DROP_TIP_FORMAT)

  def test_serialize(self):
    serialized = STAR().serialize()
    deserialized = LiquidHandlerBackend.deserialize(serialized)
    self.assertEqual(deserialized.__class__.__name__, "STAR")

  async def test_move_core(self):
    await self.lh.move_plate(self.plate, self.plt_car[1], pickup_distance_from_top=13,
                             use_arm="core")
    self._assert_command_sent_once("C0ZTid0020xs07975xd0ya1250yb1070pa07pb08tp2350tz2250th2450tt14",
                                   "xs#####xd#ya####yb####pa##pb##tp####tz####th####tt##")
    self._assert_command_sent_once("C0ZPid0021xs03475xd0yj1145yv0050zj1876zy0500yo0890yg0830yw15"
                                   "th2840te2750",
                                   "xs#####xd#yj####yv####zj####zy####yo####yg####yw##th####te####")
    self._assert_command_sent_once("C0ZRid0022xs03475xd0yj2105zj1876zi000zy0500yo0890th2840te2750",
                                   "xs#####xd#yj####zj####zi###zy####yo####th####te####")
    self._assert_command_sent_once("C0ZSid0023xs07975xd0ya1250yb1070tp2150tz2050th2450te2450",
                                    "xs#####xd#ya####yb####tp####tz####th####te####")
