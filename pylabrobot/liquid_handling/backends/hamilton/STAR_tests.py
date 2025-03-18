# mypy: disable-error-code="attr-defined,method-assign"

import unittest
import unittest.mock
from typing import cast

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.standard import GripDirection, Pickup
from pylabrobot.plate_reading import PlateReader
from pylabrobot.plate_reading.chatterbox import PlateReaderChatterboxBackend
from pylabrobot.resources import (
  HT,
  HTF,
  PLT_CAR_L5AC_A00,
  PLT_CAR_L5MD_A00,
  PLT_CAR_P3AC_A01,
  TIP_CAR_288_C00,
  TIP_CAR_480_A00,
  CellTreat_96_wellplate_350ul_Ub,
  Container,
  Coordinate,
  Cor_96_wellplate_360ul_Fb,
  Lid,
  ResourceStack,
  no_volume_tracking,
)
from pylabrobot.resources.hamilton import STF, STARLetDeck

from .STAR import (
  STAR,
  CommandSyntaxError,
  HamiltonNoTipError,
  HardwareError,
  STARFirmwareError,
  UnknownHamiltonError,
  parse_star_fw_string,
)


class TestSTARResponseParsing(unittest.TestCase):
  """Test parsing of response from Hamilton."""

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
      parse_star_fw_string("C0QM", "id####")

    with self.assertRaises(ValueError):
      parse_star_fw_string("C0RV", "")

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
    self.assertEqual(len(e.errors), 1)
    self.assertIn("Master", e.errors)
    self.assertIsInstance(e.errors["Master"], CommandSyntaxError)
    self.assertEqual(e.errors["Master"].message, "Unknown command")

  def test_parse_response_slave_errors(self):
    with self.assertRaises(STARFirmwareError) as ctx:
      self.star.check_fw_string_error("C0QMid1111 er99/00 P100/00 P235/00 P402/98 PG08/76")
    e = ctx.exception
    self.assertEqual(len(e.errors), 3)
    self.assertNotIn("Master", e.errors)
    self.assertNotIn("Pipetting channel 1", e.errors)

    self.assertEqual(e.errors["Pipetting channel 2"].raw_response, "35/00")
    self.assertEqual(e.errors["Pipetting channel 4"].raw_response, "02/98")
    self.assertEqual(e.errors["Pipetting channel 16"].raw_response, "08/76")

    self.assertIsInstance(e.errors["Pipetting channel 2"], UnknownHamiltonError)
    self.assertIsInstance(e.errors["Pipetting channel 4"], HardwareError)
    self.assertIsInstance(e.errors["Pipetting channel 16"], HamiltonNoTipError)

    self.assertEqual(e.errors["Pipetting channel 2"].message, "No error")
    self.assertEqual(
      e.errors["Pipetting channel 4"].message,
      "Unknown trace information code 98",
    )
    self.assertEqual(
      e.errors["Pipetting channel 16"].message,
      "Tip already picked up",
    )

  def test_parse_slave_response_errors(self):
    with self.assertRaises(STARFirmwareError) as ctx:
      self.star.check_fw_string_error("P1OQid1111er30")

    e = ctx.exception
    self.assertEqual(len(e.errors), 1)
    self.assertNotIn("Master", e.errors)
    self.assertIn("Pipetting channel 1", e.errors)
    self.assertIsInstance(e.errors["Pipetting channel 1"], UnknownHamiltonError)
    self.assertEqual(e.errors["Pipetting channel 1"].message, "Unknown command")


def _any_write_and_read_command_call(cmd):
  return unittest.mock.call(
    id_=unittest.mock.ANY,
    cmd=cmd,
    write_timeout=unittest.mock.ANY,
    read_timeout=unittest.mock.ANY,
    wait=unittest.mock.ANY,
  )


class TestSTARUSBComms(unittest.IsolatedAsyncioTestCase):
  """Test that USB data is parsed correctly."""

  async def asyncSetUp(self):
    self.star = STAR(read_timeout=2, packet_read_timeout=1)
    self.star.set_deck(STARLetDeck())
    self.star.io = unittest.mock.MagicMock()
    await super().asyncSetUp()

  async def test_send_command_correct_response(self):
    self.star.io.read.side_effect = [b"C0QMid0001"]
    resp = await self.star.send_command("C0", command="QM", fmt="id####")
    self.assertEqual(resp, {"id": 1})

  async def test_send_command_wrong_id(self):
    self.star.io.read.side_effect = lambda: b"C0QMid0002"
    with self.assertRaises(TimeoutError):
      await self.star.send_command("C0", command="QM", fmt="id####")

  async def test_send_command_plaintext_response(self):
    self.star.io.read.side_effect = lambda: b"this is plaintext"
    with self.assertRaises(TimeoutError):
      await self.star.send_command("C0", command="QM", fmt="id####")


class STARCommandCatcher(STAR):
  """Mock backend for star that catches commands and saves them instead of sending them to the
  machine."""

  def __init__(self):
    super().__init__()
    self.commands = []

  async def setup(self) -> None:  # type: ignore
    self._num_channels = 8
    self.iswap_installed = True
    self.core96_head_installed = True
    self._core_parked = True

  async def send_command(  # type: ignore
    self,
    module,
    command,
    auto_id=True,
    tip_pattern=None,
    fmt="",
    read_timeout=0,
    write_timeout=0,
    **kwargs,
  ):
    cmd, _ = self._assemble_command(
      module=module, command=command, auto_id=auto_id, tip_pattern=tip_pattern, **kwargs
    )
    self.commands.append(cmd)

  async def stop(self):
    self.stop_finished = True


class TestSTARLiquidHandlerCommands(unittest.IsolatedAsyncioTestCase):
  """Test STAR backend for liquid handling."""

  async def asyncSetUp(self):
    self.STAR = STAR(read_timeout=1)
    self.STAR._write_and_read_command = unittest.mock.AsyncMock()
    self.STAR.io = unittest.mock.MagicMock()
    self.STAR.io.setup = unittest.mock.AsyncMock()
    self.STAR.io.write = unittest.mock.MagicMock()
    self.STAR.io.read = unittest.mock.MagicMock()

    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.STAR, deck=self.deck)

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[1] = self.tip_rack = STF(name="tip_rack_01")
    self.tip_car[2] = self.tip_rack2 = HTF(name="tip_rack_02")
    self.deck.assign_child_resource(self.tip_car, rails=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = Cor_96_wellplate_360ul_Fb(name="plate_01")
    lid = Lid(
      name="plate_01_lid",
      size_x=self.plate.get_size_x(),
      size_y=self.plate.get_size_y(),
      size_z=10,
      nesting_z_height=10,
    )
    self.plate.assign_child_resource(lid)
    assert self.plate.lid is not None
    self.plt_car[1] = self.other_plate = Cor_96_wellplate_360ul_Fb(name="plate_02")
    lid = Lid(
      name="plate_02_lid",
      size_x=self.other_plate.get_size_x(),
      size_y=self.other_plate.get_size_y(),
      size_z=10,
      nesting_z_height=10,
    )
    self.other_plate.assign_child_resource(lid)
    self.deck.assign_child_resource(self.plt_car, rails=9)

    class BlueBucket(Container):
      def __init__(self, name: str):
        super().__init__(
          name,
          size_x=123,
          size_y=82,
          size_z=75,
          category="bucket",
          max_volume=123 * 82 * 75,
          material_z_thickness=1,
        )

    self.bb = BlueBucket(name="blue bucket")
    self.deck.assign_child_resource(self.bb, location=Coordinate(425, 141.5, 120 - 1))

    self.maxDiff = None

    self.STAR._num_channels = 8
    self.STAR.core96_head_installed = True
    self.STAR.iswap_installed = True
    self.STAR.setup = unittest.mock.AsyncMock()
    self.STAR._core_parked = True
    self.STAR._iswap_parked = True
    await self.lh.setup()

  async def asyncTearDown(self):
    await self.lh.stop()

  async def test_indictor_light(self):
    await self.STAR.set_loading_indicators(bit_pattern=[True] * 54, blink_pattern=[False] * 54)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0CPid0001cl3FFFFFFFFFFFFFcb00000000000000",
        )
      ]
    )

  def test_ops_to_fw_positions(self):
    """Convert channel positions to firmware positions."""
    tip_a1 = self.tip_rack.get_item("A1")
    tip_f1 = self.tip_rack.get_item("F1")
    tip = self.tip_rack.get_tip("A1")

    op1 = Pickup(resource=tip_a1, tip=tip, offset=Coordinate.zero())
    op2 = Pickup(resource=tip_f1, tip=tip, offset=Coordinate.zero())
    self.assertEqual(
      self.STAR._ops_to_fw_positions((op1,), use_channels=[0]),
      ([1179, 0], [2418, 0], [True, False]),
    )

    self.assertEqual(
      self.STAR._ops_to_fw_positions((op1, op2), use_channels=[0, 1]),
      ([1179, 1179, 0], [2418, 1968, 0], [True, True, False]),
    )

    self.assertEqual(
      self.STAR._ops_to_fw_positions((op1, op2), use_channels=[1, 2]),
      (
        [0, 1179, 1179, 0],
        [0, 2418, 1968, 0],
        [False, True, True, False],
      ),
    )

    # check two operations on the same row, different column.
    tip_a2 = self.tip_rack.get_item("A2")
    op3 = Pickup(resource=tip_a2, tip=tip, offset=Coordinate.zero())
    self.assertEqual(
      self.STAR._ops_to_fw_positions((op1, op3), use_channels=[0, 1]),
      ([1179, 1269, 0], [2418, 2418, 0], [True, True, False]),
    )

    # A1, A2, B1, B2
    tip_b1 = self.tip_rack.get_item("B1")
    op4 = Pickup(resource=tip_b1, tip=tip, offset=Coordinate.zero())
    tip_b2 = self.tip_rack.get_item("B2")
    op5 = Pickup(resource=tip_b2, tip=tip, offset=Coordinate.zero())
    self.assertEqual(
      self.STAR._ops_to_fw_positions((op1, op4, op3, op5), use_channels=[0, 1, 2, 3]),
      (
        [1179, 1179, 1269, 1269, 0],
        [2418, 2328, 2418, 2328, 0],
        [True, True, True, True, False],
      ),
    )

    # make sure two operations on the same spot are not allowed
    with self.assertRaises(ValueError):
      self.STAR._ops_to_fw_positions((op1, op1), use_channels=[0, 1])

  def test_tip_definition(self):
    pass

  async def test_tip_pickup_01(self):
    await self.lh.pick_up_tips(self.tip_rack["A1", "B1"])
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0TTid0001tt01tf1tl0519tv03600tg2tu0",
        ),
        _any_write_and_read_command_call(
          "C0TPid0002xp01179 01179 00000&yp2418 2328 0000&tm1 1 0&tt01tp2244tz2164th2450td0",
        ),
      ]
    )

  async def test_tip_pickup_56(self):
    await self.lh.pick_up_tips(self.tip_rack["E1", "F1"], use_channels=[4, 5])
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0TTid0001tt01tf1tl0519tv03600tg2tu0",
        ),
        _any_write_and_read_command_call(
          "C0TPid0002xp00000 00000 00000 00000 01179 01179 00000&yp0000 0000 0000 0000 2058 1968 0000&tm0 0 0 0 1 1 0&tt01tp2244tz2164th2450td0",
        ),
      ]
    )
    self.STAR.io.write.reset_mock()

  async def test_tip_drop_56(self):
    await self.test_tip_pickup_56()  # pick up tips first
    self.STAR._write_and_read_command.side_effect = [
      "C0TRid0003kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
    ]
    await self.lh.drop_tips(self.tip_rack["E1", "F1"], use_channels=[4, 5])
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0TRid0003xp00000 00000 00000 00000 01179 01179 00000&yp0000 0000 0000 0000 2058 1968 "
          "0000&tm0 0 0 0 1 1 0&tp2244tz2164th2450te2450ti1",
        )
      ]
    )

  async def test_aspirate56(self):
    self.maxDiff = None
    await self.test_tip_pickup_56()  # pick up tips first
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    for well in self.plate.get_items(["A1", "B1"]):
      well.tracker.set_liquids([(None, 100 * 1.072)])  # liquid class correction
    await self.lh.aspirate(self.plate["A1", "B1"], vols=[100, 100], use_channels=[4, 5])
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0ASid0003at0 0 0 0 0 0 0&tm0 0 0 0 1 1 0&xp00000 00000 00000 "
          "00000 02983 02983 00000&yp0000 0000 0000 0000 1457 1367 0000&th2450te2450lp2000 2000 2000 "
          "2000 2000 2000 2000&ch000 000 000 000 000 000 000&zl1866 1866 1866 1866 1866 1866 1866&"
          "po0100 0100 0100 0100 0100 0100 0100&zu0032 0032 0032 0032 0032 0032 0032&zr06180 06180 "
          "06180 06180 06180 06180 06180&zx1866 1866 1866 1866 1866 1866 1866&ip0000 0000 0000 0000 "
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
        )
      ]
    )
    self.STAR.io.write.reset_mock()

  async def test_single_channel_aspiration(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    well = self.plate.get_item("A1")
    well.tracker.set_liquids([(None, 100 * 1.072)])  # liquid class correction
    await self.lh.aspirate([well], vols=[100])
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0ASid0001at0 0&tm1 0&xp02983 00000&yp1457 0000&th2450te2450lp2000 2000&ch000 000&zl1866 "
          "1866&po0100 0100&zu0032 0032&zr06180 06180&zx1866 1866&ip0000 0000&it0 0&fp0000 0000&"
          "av01072 01072&as1000 1000&ta000 000&ba0000 0000&oa000 000&lm0 0&ll1 1&lv1 1&zo000 000&"
          "ld00 00&de0020 0020&wt10 10&mv00000 00000&mc00 00&mp000 000&ms1000 1000&mh0000 0000&"
          "gi000 000&gj0gk0lk0 0&ik0000 0000&sd0500 0500&se0500 0500&sz0300 0300&io0000 0000&"
          "il00000 00000&in0000 0000&",
        )
      ]
    )

  async def test_single_channel_aspiration_liquid_height(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
    # TODO: Hamilton liquid classes
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    well = self.plate.get_item("A1")
    well.tracker.set_liquids([(None, 100 * 1.072)])  # liquid class correction
    await self.lh.aspirate([well], vols=[100], liquid_height=[10])

    # This passes the test, but is not the real command.
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0ASid0001at0 0&tm1 0&xp02983 00000&yp1457 0000&th2450te2450lp2000 2000&ch000 000&zl1966 "
          "1966&po0100 0100&zu0032 0032&zr06180 06180&zx1866 1866&ip0000 0000&it0 0&fp0000 0000&"
          "av01072 01072&as1000 1000&ta000 000&ba0000 0000&oa000 000&lm0 0&ll1 1&lv1 1&zo000 000&"
          "ld00 00&de0020 0020&wt10 10&mv00000 00000&mc00 00&mp000 000&ms1000 1000&mh0000 0000&"
          "gi000 000&gj0gk0lk0 0&ik0000 0000&sd0500 0500&se0500 0500&sz0300 0300&io0000 0000&"
          "il00000 00000&in0000 0000&",
        )
      ]
    )

  async def test_multi_channel_aspiration(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1"), 1: self.tip_rack.get_tip("B1")})
    # TODO: Hamilton liquid classes
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    wells = self.plate.get_items("A1:B1")
    for well in wells:
      well.tracker.set_liquids([(None, 100 * 1.072)])  # liquid class correction
    await self.lh.aspirate(self.plate["A1:B1"], vols=[100] * 2)

    # This passes the test, but is not the real command.
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0ASid0001at0 0 0&tm1 1 0&xp02983 02983 00000&yp1457 1367 0000&th2450te2450lp2000 2000 2000&"
          "ch000 000 000&zl1866 1866 1866&po0100 0100 0100&zu0032 0032 0032&zr06180 06180 06180&"
          "zx1866 1866 1866&ip0000 0000 0000&it0 0 0&fp0000 0000 0000&av01072 01072 01072&as1000 1000 "
          "1000&ta000 000 000&ba0000 0000 0000&oa000 000 000&lm0 0 0&ll1 1 1&lv1 1 1&zo000 000 000&"
          "ld00 00 00&de0020 0020 0020&wt10 10 10&mv00000 00000 00000&mc00 00 00&mp000 000 000&"
          "ms1000 1000 1000&mh0000 0000 0000&gi000 000 000&gj0gk0lk0 0 0&ik0000 0000 0000&sd0500 0500 "
          "0500&se0500 0500 0500&sz0300 0300 0300&io0000 0000 0000&il00000 00000 00000&in0000 0000 "
          "0000&",
        )
      ]
    )

  async def test_aspirate_single_resource(self):
    self.lh.update_head_state({i: self.tip_rack.get_tip(i) for i in range(5)})
    with no_volume_tracking():
      await self.lh.aspirate(
        [self.bb] * 5,
        vols=[10] * 5,
        use_channels=[0, 1, 2, 3, 4],
        liquid_height=[1] * 5,
      )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0ASid0001at0 0 0 0 0 0&tm1 1 1 1 1 0&xp04865 04865 04865 04865 04865 00000&yp2098 1962 "
          "1825 1688 1552 0000&th2450te2450lp2000 2000 2000 2000 2000 2000&ch000 000 000 000 000 000&"
          "zl1210 1210 1210 1210 1210 1210&po0100 0100 0100 0100 0100 0100&zu0032 0032 0032 0032 0032 "
          "0032&zr06180 06180 06180 06180 06180 06180&zx1200 1200 1200 1200 1200 1200&ip0000 0000 0000 "
          "0000 0000 0000&it0 0 0 0 0 0&fp0000 0000 0000 0000 0000 0000&av00119 00119 00119 00119 "
          "00119 00119&as1000 1000 1000 1000 1000 1000&ta000 000 000 000 000 000&ba0000 0000 0000 0000 "
          "0000 0000&oa000 000 000 000 000 000&lm0 0 0 0 0 0&ll1 1 1 1 1 1&lv1 1 1 1 1 1&zo000 000 000 "
          "000 000 000&ld00 00 00 00 00 00&de0020 0020 0020 0020 0020 0020&wt10 10 10 10 10 10&mv00000 "
          "00000 00000 00000 00000 00000&mc00 00 00 00 00 00&mp000 000 000 000 000 000&ms1000 1000 "
          "1000 1000 1000 1000&mh0000 0000 0000 0000 0000 0000&gi000 000 000 000 000 000&gj0gk0lk0 0 0 "
          "0 0 0&ik0000 0000 0000 0000 0000 0000&sd0500 0500 0500 0500 0500 0500&se0500 0500 0500 0500 "
          "0500 0500&sz0300 0300 0300 0300 0300 0300&io0000 0000 0000 0000 0000 0000&il00000 00000 "
          "00000 00000 00000 00000&in0000 0000 0000 0000 0000 0000&",
        )
      ]
    )

  async def test_dispense_single_resource(self):
    self.lh.update_head_state({i: self.tip_rack.get_tip(i) for i in range(5)})
    with no_volume_tracking():
      await self.lh.dispense(
        [self.bb] * 5,
        vols=[10] * 5,
        use_channels=[0, 1, 2, 3, 4],
        liquid_height=[1] * 5,
        blow_out=[True] * 5,
        jet=[True] * 5,
      )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0DSid0001dm1 1 1 1 1 1&tm1 1 1 1 1 0&xp04865 04865 04865 04865 04865 00000&yp2098 1962 "
          "1825 1688 1552 0000&zx1200 1200 1200 1200 1200 1200&lp2000 2000 2000 2000 2000 2000&zl1210 "
          "1210 1210 1210 1210 1210&po0100 0100 0100 0100 0100 0100&ip0000 0000 0000 0000 0000 0000&"
          "it0 0 0 0 0 0&fp0000 0000 0000 0000 0000 0000&zu0032 0032 0032 0032 0032 0032&zr06180 06180 "
          "06180 06180 06180 06180&th2450te2450dv00116 00116 00116 00116 00116 00116&ds1800 1800 1800 "
          "1800 1800 1800&ss0050 0050 0050 0050 0050 0050&rv000 000 000 000 000 000&ta050 050 050 050 "
          "050 050&ba0300 0300 0300 0300 0300 0300&lm0 0 0 0 0 0&dj00zo000 000 000 000 000 000&ll1 1 1 "
          "1 1 1&lv1 1 1 1 1 1&de0010 0010 0010 0010 0010 0010&wt00 00 00 00 00 00&mv00000 00000 00000 "
          "00000 00000 00000&mc00 00 00 00 00 00&mp000 000 000 000 000 000&ms0010 0010 0010 0010 0010 "
          "0010&mh0000 0000 0000 0000 0000 0000&gi000 000 000 000 000 000&gj0gk0",
        )
      ]
    )

  async def test_single_channel_dispense(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    with no_volume_tracking():
      await self.lh.dispense(self.plate["A1"], vols=[100], jet=[True], blow_out=[True])
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0DSid0001dm1 1&tm1 0&xp02983 00000&yp1457 0000&zx1866 1866&lp2000 2000&zl1866 1866&po0100 0100&ip0000 0000&it0 0&fp0000 0000&zu0032 0032&zr06180 06180&th2450te2450dv01072 01072&ds1800 1800&ss0050 0050&rv000 000&ta050 050&ba0300 0300&lm0 0&dj00zo000 000&ll1 1&lv1 1&de0010 0010&wt00 00&mv00000 00000&mc00 00&mp000 000&ms0010 0010&mh0000 0000&gi000 000&gj0gk0",
        )
      ]
    )

  async def test_multi_channel_dispense(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1"), 1: self.tip_rack.get_tip("B1")})
    # TODO: Hamilton liquid classes
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    with no_volume_tracking():
      await self.lh.dispense(
        self.plate["A1:B1"],
        vols=[100] * 2,
        jet=[True] * 2,
        blow_out=[True] * 2,
      )

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0DSid0001dm1 1 1&tm1 1 0&xp02983 02983 00000&yp1457 1367 0000&zx1866 1866 1866&lp2000 2000 "
          "2000&zl1866 1866 1866&po0100 0100 0100&ip0000 0000 0000&it0 0 0&fp0000 0000 0000&zu0032 "
          "0032 0032&zr06180 06180 06180&th2450te2450dv01072 01072 01072&ds1800 1800 1800&"
          "ss0050 0050 0050&rv000 000 000&ta050 050 050&ba0300 0300 0300&lm0 0 0&dj00zo000 000 000&"
          "ll1 1 1&lv1 1 1&de0010 0010 0010&wt00 00 00&mv00000 00000 00000&mc00 00 00&mp000 000 000&"
          "ms0010 0010 0010&mh0000 0000 0000&gi000 000 000&gj0gk0",
        )
      ]
    )

  async def test_core_96_tip_pickup(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call("C0TTid0001tt01tf1tl0519tv03600tg2tu0"),
        _any_write_and_read_command_call("C0EPid0002xs01179xd0yh2418tt01wu0za2164zh2450ze2450"),
      ]
    )

  async def test_core_96_tip_drop(self):
    await self.lh.pick_up_tips96(self.tip_rack)  # pick up tips first
    self.STAR._write_and_read_command.reset_mock()
    await self.lh.drop_tips96(self.tip_rack)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call("C0ERid0003xs01179xd0yh2418za2164zh2450ze2450"),
      ]
    )

  async def test_core_96_tip_discard(self):
    await self.lh.pick_up_tips96(self.tip_rack)  # pick up tips first
    self.STAR._write_and_read_command.reset_mock()
    await self.lh.discard_tips96()
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call("C0ERid0003xs00420xd1yh1203za2164zh2450ze2450"),
      ]
    )

  async def test_core_96_aspirate(self):
    await self.lh.pick_up_tips96(self.tip_rack2)  # pick up high volume tips
    self.STAR._write_and_read_command.reset_mock()

    # TODO: Hamilton liquid classes
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    await self.lh.aspirate96(self.plate, volume=100, blow_out=True)

    # volume used to be 01072, but that was generated using a non-core liquid class.
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0EAid0003aa0xs02983xd0yh1457zh2450ze2450lz1999zt1866pp0100zm1866zv0032zq06180iw000ix0fh000af01083ag2500vt050bv00000wv00050cm0cs1bs0020wh10hv00000hc00hp000mj000hs1200cwFFFFFFFFFFFFFFFFFFFFFFFFcr000cj0cx0"
        ),
      ]
    )

  async def test_core_96_dispense(self):
    await self.lh.pick_up_tips96(self.tip_rack2)  # pick up high volume tips
    if self.plate.lid is not None:
      self.plate.lid.unassign()
    await self.lh.aspirate96(self.plate, 100, blow_out=True)  # aspirate first
    self.STAR._write_and_read_command.reset_mock()

    with no_volume_tracking():
      await self.lh.dispense96(self.plate, 100, blow_out=True)

    # volume used to be 01072, but that was generated using a non-core liquid class.
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0EDid0004da3xs02983xd0yh1457zm1866zv0032zq06180lz1999zt1866pp0100iw000ix0fh000zh2450ze2450df01083dg1200es0050ev000vt050bv00000cm0cs1ej00bs0020wh00hv00000hc00hp000mj000hs1200cwFFFFFFFFFFFFFFFFFFFFFFFFcr000cj0cx0"
        ),
      ]
    )

  async def test_zero_volume_liquid_handling96(self):
    # just test that this does not throw an error
    await self.lh.pick_up_tips96(self.tip_rack)
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    await self.lh.aspirate96(self.plate, 0)
    await self.lh.dispense96(self.plate, 0)

  async def test_iswap(self):
    await self.lh.move_plate(
      self.plate,
      self.plt_car[2],
      pickup_distance_from_top=13.2 - 3.33,
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs03479xd0yj1142yd0zj1874zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1",
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs03479xd0yj3062yd0zj1874zd0th2840te2840gr1go1308ga0",
        ),
      ]
    )

  async def test_iswap_plate_reader(self):
    plate_reader = PlateReader(
      name="plate_reader",
      backend=PlateReaderChatterboxBackend(),
      size_x=0,
      size_y=0,
      size_z=0,
    )
    self.lh.deck.assign_child_resource(
      plate_reader, location=Coordinate(1000, 264.7, 200 - 3.03)
    )  # 666: 00002

    await self.lh.move_plate(
      self.plate,
      plate_reader,
      pickup_distance_from_top=8.2 - 3.33,
      pickup_direction=GripDirection.FRONT,
      drop_direction=GripDirection.LEFT,
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs03479xd0yj1142yd0zj1924zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1",
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs10427xd0yj3286yd0zj2063zd0th2840te2840gr4go1308ga0",
        ),
      ]
    )
    self.STAR._write_and_read_command.reset_mock()

    assert self.plate.rotation.z == 270
    self.assertAlmostEqual(self.plate.get_absolute_size_x(), 85.48, places=2)
    self.assertAlmostEqual(self.plate.get_absolute_size_y(), 127.76, places=2)

    await self.lh.move_plate(
      plate_reader.get_plate(),
      self.plt_car[0],
      pickup_distance_from_top=8.2 - 3.33,
      pickup_direction=GripDirection.LEFT,
      drop_direction=GripDirection.FRONT,
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0003xs10427xd0yj3286yd0zj2063zd0gr4th2840te2840gw4go1308gb1245gt20ga0gc1",
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs03479xd0yj1142yd0zj1924zd0th2840te2840gr1go1308ga0",
        ),
      ]
    )

  async def test_iswap_move_lid(self):
    assert self.plate.lid is not None and self.other_plate.lid is not None
    self.other_plate.lid.unassign()  # remove lid from plate
    await self.lh.move_lid(self.plate.lid, self.other_plate)

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs03479xd0yj1142yd0zj1950zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1"
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs03479xd0yj2102yd0zj1950zd0th2840te2840gr1go1308ga0"
        ),
      ]
    )

  async def test_iswap_stacking_area(self):
    stacking_area = ResourceStack("stacking_area", direction="z")
    # for some reason it was like this at some point
    # self.lh.assign_resource(hotel, location=Coordinate(6, 414-63, 217.2 - 100))
    # self.lh.deck.assign_child_resource(hotel, location=Coordinate(6, 414-63, 231.7 - 100 +4.5))
    self.lh.deck.assign_child_resource(stacking_area, location=Coordinate(6, 414, 226.2 - 3.33))

    assert self.plate.lid is not None
    await self.lh.move_lid(self.plate.lid, stacking_area)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs03479xd0yj1142yd0zj1950zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1"
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs00699xd0yj4567yd0zj2305zd0th2840te2840gr1go1308ga0"
        ),
      ]
    )
    self.STAR._write_and_read_command.reset_mock()

    # Move lids back (reverse order)
    await self.lh.move_lid(cast(Lid, stacking_area.get_top_item()), self.plate)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0003xs00699xd0yj4567yd0zj2305zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1"
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs03479xd0yj1142yd0zj1950zd0th2840te2840gr1go1308ga0"
        ),
      ]
    )

  async def test_iswap_stacking_area_2lids(self):
    # for some reason it was like this at some point
    # self.lh.assign_resource(hotel, location=Coordinate(6, 414-63, 217.2 - 100))
    stacking_area = ResourceStack("stacking_area", direction="z")
    self.lh.deck.assign_child_resource(stacking_area, location=Coordinate(6, 414, 226.2 - 3.33))

    assert self.plate.lid is not None and self.other_plate.lid is not None

    await self.lh.move_lid(self.plate.lid, stacking_area)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs03479xd0yj1142yd0zj1950zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1"
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs00699xd0yj4567yd0zj2305zd0th2840te2840gr1go1308ga0"
        ),
      ]
    )
    self.STAR._write_and_read_command.reset_mock()

    await self.lh.move_lid(self.other_plate.lid, stacking_area)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0003xs03479xd0yj2102yd0zj1950zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1"
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs00699xd0yj4567yd0zj2405zd0th2840te2840gr1go1308ga0"
        ),
      ]
    )
    self.STAR._write_and_read_command.reset_mock()

    # Move lids back (reverse order)
    top_item = stacking_area.get_top_item()
    assert isinstance(top_item, Lid)
    await self.lh.move_lid(top_item, self.plate)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0005xs00699xd0yj4567yd0zj2405zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1"
        ),
        _any_write_and_read_command_call(
          "C0PRid0006xs03479xd0yj1142yd0zj1950zd0th2840te2840gr1go1308ga0"
        ),
      ]
    )
    self.STAR._write_and_read_command.reset_mock()

    top_item = stacking_area.get_top_item()
    assert isinstance(top_item, Lid)
    await self.lh.move_lid(top_item, self.other_plate)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0007xs00699xd0yj4567yd0zj2305zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1"
        ),
        _any_write_and_read_command_call(
          "C0PRid0008xs03479xd0yj2102yd0zj1950zd0th2840te2840gr1go1308ga0"
        ),
      ]
    )
    self.STAR._write_and_read_command.reset_mock()

  async def test_iswap_move_with_intermediate_locations(self):
    self.plt_car[1].resource.unassign()
    await self.lh.move_plate(
      self.plate,
      self.plt_car[1],
      intermediate_locations=[
        self.plt_car[2].get_absolute_location() + Coordinate(50, 0, 50),
        self.plt_car[3].get_absolute_location() + Coordinate(-50, 0, 50),
      ],
    )

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs03479xd0yj1142yd0zj1874zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1"
        ),
        _any_write_and_read_command_call("C0PMid0002xs03979xd0yj3062yd0zj2432zd0gr1th2840ga1xe4 1"),
        _any_write_and_read_command_call("C0PMid0003xs02979xd0yj4022yd0zj2432zd0gr1th2840ga1xe4 1"),
        _any_write_and_read_command_call(
          "C0PRid0004xs03479xd0yj2102yd0zj1874zd0th2840te2840gr1go1308ga0"
        ),
      ]
    )

  async def test_discard_tips(self):
    await self.lh.pick_up_tips(self.tip_rack["A1:H1"])
    # {"id": 2, "kz": [000, 000, 000, 000, 000, 000, 000, 000], "vz": [000, 000, 000, 000, 000, 000, 000, 000]}
    self.STAR._write_and_read_command.side_effect = [
      "C0TRid0003kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
    ]
    self.STAR._write_and_read_command.reset_mock()
    await self.lh.discard_tips()
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0TRid0003xp08000 08000 08000 08000 08000 08000 08000 08000yp3427 3337 3247 3157 3067 2977 2887 2797tm1 1 1 1 1 1 1 1tp1970tz1870th2450te2450ti0",
        )
      ]
    )

  async def test_portrait_tip_rack_handling(self):
    deck = STARLetDeck()
    lh = LiquidHandler(self.STAR, deck=deck)
    tip_car = TIP_CAR_288_C00(name="tip carrier")
    tip_car[0] = tr = HT(name="tips_01").rotated(z=90)
    assert tr.rotation.z == 90
    assert tr.location == Coordinate(82.6, 0, 0)
    deck.assign_child_resource(tip_car, rails=2)
    await lh.setup()

    await lh.pick_up_tips(tr["A4:A1"])
    self.STAR._write_and_read_command.side_effect = [
      "C0TRid0002kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
    ]
    await lh.drop_tips(tr["A4:A1"])

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0TPid0002xp01360 01360 01360 01360 00000&yp1380 1290 1200 1110 0000&tm1 1 1 1 0&tt01tp2263tz2163th2450td0"
        ),
        _any_write_and_read_command_call(
          "C0TRid0003xp01360 01360 01360 01360 00000&yp1380 1290 1200 1110 0000&tm1 1 1 1 0&tp2263tz2183th2450te2450ti1"
        ),
      ]
    )

  def test_serialize(self):
    serialized = LiquidHandler(backend=STAR(), deck=STARLetDeck()).serialize()
    deserialized = LiquidHandler.deserialize(serialized)
    self.assertEqual(deserialized.__class__.__name__, "LiquidHandler")
    self.assertEqual(deserialized.backend.__class__.__name__, "STAR")

  async def test_move_core(self):
    self.plt_car[1].resource.unassign()
    await self.lh.move_plate(
      self.plate,
      self.plt_car[1],
      pickup_distance_from_top=13 - 3.33,
      use_arm="core",
      # kwargs specific to pickup and drop
      channel_1=7,
      channel_2=8,
      return_core_gripper=True,
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0ZTid0001xs07975xd0ya1240yb1065pa07pb08tp2350tz2250th2840tt14"
        ),
        _any_write_and_read_command_call(
          "C0ZPid0002xs03479xd0yj1142yv0050zj1876zy0500yo0885yg0825yw15" "th2840te2840"
        ),
        _any_write_and_read_command_call(
          "C0ZRid0003xs03479xd0yj2102zj1876zi000zy0500yo0885th2840te2840"
        ),
        _any_write_and_read_command_call(
          "C0ZSid0004xs07975xd0ya1240yb1065tp2150tz2050th2840te2840"
        ),
      ]
    )


class STARIswapMovementTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.STAR = STAR()
    self.STAR._write_and_read_command = unittest.mock.AsyncMock()
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.STAR, deck=self.deck)

    self.plt_car = PLT_CAR_L5MD_A00(name="plt_car")
    self.plt_car[0] = self.plate = CellTreat_96_wellplate_350ul_Ub(name="plate", with_lid=True)
    self.deck.assign_child_resource(self.plt_car, rails=15)

    self.plt_car2 = PLT_CAR_P3AC_A01(name="plt_car2")
    self.deck.assign_child_resource(self.plt_car2, rails=3)

    self.STAR._num_channels = 8
    self.STAR.core96_head_installed = True
    self.STAR.iswap_installed = True
    self.STAR.setup = unittest.mock.AsyncMock()
    self.STAR._core_parked = True
    self.STAR._iswap_parked = True
    await self.lh.setup()

  async def test_simple_movement(self):
    await self.lh.move_plate(self.plate, self.plt_car[1])
    await self.lh.move_plate(self.plate, self.plt_car[0])

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs04829xd0yj1141yd0zj2143zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1",
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs04829xd0yj2101yd0zj2143zd0th2840te2840gr1go1308ga0",
        ),
        _any_write_and_read_command_call(
          "C0PPid0003xs04829xd0yj2101yd0zj2143zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1",
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs04829xd0yj1141yd0zj2143zd0th2840te2840gr1go1308ga0"
        ),
      ]
    )

  async def test_movement_to_portrait_site_left(self):
    await self.lh.move_plate(self.plate, self.plt_car2[0], drop_direction=GripDirection.LEFT)
    await self.lh.move_plate(self.plate, self.plt_car[0], drop_direction=GripDirection.LEFT)

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs04829xd0yj1141yd0zj2143zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1",
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs02317xd0yj1644yd0zj1884zd0th2840te2840gr4go1308ga0",
        ),
        _any_write_and_read_command_call(
          "C0PPid0003xs02317xd0yj1644yd0zj1884zd0gr1th2840te2840gw4go0881gb0818gt20ga0gc1",
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs04829xd0yj1141yd0zj2143zd0th2840te2840gr4go0881ga0",
        ),
      ]
    )

  async def test_movement_to_portrait_site_right(self):
    await self.lh.move_plate(self.plate, self.plt_car2[0], drop_direction=GripDirection.RIGHT)
    await self.lh.move_plate(self.plate, self.plt_car[0], drop_direction=GripDirection.RIGHT)

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs04829xd0yj1141yd0zj2143zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1",
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs02317xd0yj1644yd0zj1884zd0th2840te2840gr2go1308ga0",
        ),
        _any_write_and_read_command_call(
          "C0PPid0003xs02317xd0yj1644yd0zj1884zd0gr1th2840te2840gw4go0881gb0818gt20ga0gc1",
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs04829xd0yj1141yd0zj2143zd0th2840te2840gr2go0881ga0",
        ),
      ]
    )

  async def test_move_lid_across_rotated_resources(self):
    self.plt_car2[0] = plate2 = CellTreat_96_wellplate_350ul_Ub(
      name="plate2", with_lid=False
    ).rotated(z=270)
    self.plt_car2[1] = plate3 = CellTreat_96_wellplate_350ul_Ub(
      name="plate3", with_lid=False
    ).rotated(z=90)

    assert plate2.get_absolute_location() == Coordinate(x=189.1, y=228.26, z=183.98)
    assert plate3.get_absolute_location() == Coordinate(x=274.21, y=246.5, z=183.98)

    assert self.plate.lid is not None
    await self.lh.move_lid(self.plate.lid, plate2, drop_direction=GripDirection.LEFT)
    assert plate2.lid is not None
    await self.lh.move_lid(plate2.lid, plate3, drop_direction=GripDirection.BACK)
    assert plate3.lid is not None
    await self.lh.move_lid(plate3.lid, self.plate, drop_direction=GripDirection.LEFT)

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs04829xd0yj1142yd0zj2242zd0gr1th2840te2840gw4go1308gb1245gt20ga0gc1",
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs02318xd0yj1644yd0zj1983zd0th2840te2840gr4go1308ga0",
        ),
        _any_write_and_read_command_call(
          "C0PPid0003xs02318xd0yj1644yd0zj1983zd0gr1th2840te2840gw4go0885gb0822gt20ga0gc1",
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs02315xd0yj3104yd0zj1983zd0th2840te2840gr3go0885ga0",
        ),
        _any_write_and_read_command_call(
          "C0PPid0005xs02315xd0yj3104yd0zj1983zd0gr1th2840te2840gw4go0885gb0822gt20ga0gc1",
        ),
        _any_write_and_read_command_call(
          "C0PRid0006xs04829xd0yj1142yd0zj2242zd0th2840te2840gr4go0885ga0",
        ),
      ]
    )
