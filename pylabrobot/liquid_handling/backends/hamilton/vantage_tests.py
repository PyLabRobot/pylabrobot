import unittest

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.resources import (
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cos_96_EZWash,
  HT_L,
  LT_L,
  Coordinate,
)
from pylabrobot.resources.hamilton import VantageDeck
from pylabrobot.liquid_handling.standard import Pickup

from .vantage import (
  Vantage,
  VantageFirmwareError,
  parse_vantage_fw_string,
  vantage_response_string_to_error
)


PICKUP_TIP_FORMAT = {"xp": "[int]", "yp": "[int]", "tm": "[int]", "tt": "[int]", "tp": "[int]",
  "tz": "[int]", "th": "[int]", "ba": "[int]", "td": "[int]"}

DROP_TIP_FORMAT = {"xp": "[int]", "yp": "[int]", "tm": "[int]", "tp": "[int]", "tz": "[int]",
  "th": "[int]", "te": "[int]", "ts": "[int]", "td": "[int]"}

# "dj": "int" = side_touch_off_distance, only documented for dispense, but for some reason VoV also
# sends it for aspirate.
ASPIRATE_FORMAT = {
  "at": "[int]", "tm": "[int]", "xp": "[int]", "yp": "[int]", "th": "[int]", "te": "[int]",
  "lp": "[int]", "ch": "[int]", "zl": "[int]", "zx": "[int]", "ip": "[int]", "fp": "[int]",
  "av": "[int]", "as": "[int]", "ta": "[int]", "ba": "[int]", "oa": "[int]", "lm": "[int]",
  "ll": "[int]", "lv": "[int]", "de": "[int]", "wt": "[int]", "mv": "[int]", "mc": "[int]",
  "mp": "[int]", "ms": "[int]", "gi": "[int]", "gj": "[int]", "gk": "[int]", "zu": "[int]",
  "zr": "[int]", "mh": "[int]", "zo": "[int]", "po": "[int]",  "la": "[int]",
  "lb": "[int]", "lc": "[int]", "id": "int" }

DISPENSE_FORMAT = {
  "dm": "[int]", "tm": "[int]", "xp": "[int]", "yp": "[int]", "zx": "[int]", "lp": "[int]",
  "zl": "[int]", "ip": "[int]", "fp": "[int]", "th": "[int]", "te": "[int]", "dv": "[int]",
  "ds": "[int]", "ss": "[int]", "rv": "[int]", "ta": "[int]", "ba": "[int]", "lm": "[int]",
  "zo": "[int]", "ll": "[int]", "lv": "[int]", "de": "[int]", "mv": "[int]", "mc": "[int]",
  "mp": "[int]", "ms": "[int]", "wt": "[int]", "gi": "[int]", "gj": "[int]", "gk": "[int]",
  "zu": "[int]", "zr": "[int]", "dj": "[int]", "mh": "[int]", "po": "[int]", "la": "[int]"}


class TestVantageResponseParsing(unittest.TestCase):
  """ Test parsing of response from Hamilton. """

  def test_parse_response_params(self):
    parsed = parse_vantage_fw_string("A1PMDAid1111", None)
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_vantage_fw_string("A1PMDAid1111", {"id": "int"})
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_vantage_fw_string("A1PMDAid1112rw\"abc\"", {"rw": "str"})
    self.assertEqual(parsed, {"id": 1112, "rw": "abc"})

    parsed = parse_vantage_fw_string("A1PMDAid1112rw-21", {"rw": "int"})
    self.assertEqual(parsed, {"id": 1112, "rw": -21})

    parsed = parse_vantage_fw_string("A1PMDAid1113rwABC", {"rw": "hex"})
    self.assertEqual(parsed, {"id": 1113, "rw": int("ABC", base=16)})

    parsed = parse_vantage_fw_string("A1PMDAid1113rw1 -2 +3", {"rw": "[int]"})
    self.assertEqual(parsed, {"id": 1113, "rw": [1, -2, 3]})

    with self.assertRaises(ValueError):
      # should fail with auto-added id.
      parsed = parse_vantage_fw_string("A1PMDrwbc", None)
      self.assertEqual(parsed, "")

    with self.assertRaises(ValueError):
      parse_vantage_fw_string("A1PMDA", {"id": "int"})

  def test_parse_error_response(self):
    resp = "I1AMRQid0000er4et\"Slave not available\""
    error = vantage_response_string_to_error(resp)
    self.assertEqual(error, VantageFirmwareError(
      errors={"Cover": "Slave not available"},
      raw_response=resp))

    resp = "I1AMLPid215er57et\"S-Drive: Drive not initialized\""
    error = vantage_response_string_to_error(resp)
    self.assertEqual(error, VantageFirmwareError(
      errors={"Cover": "S-Drive: Drive not initialized"},
      raw_response=resp))

    resp = "A1HMDAid239er99es\"H070\""
    error = vantage_response_string_to_error(resp)
    self.assertEqual(error, VantageFirmwareError(
      errors={"Core 96": "No liquid level found"},
      raw_response=resp))

    resp = "A1PMDAid262er99es\"P170 P270 P370 P470 P570 P670 P770 P870\""
    error = vantage_response_string_to_error(resp)
    self.assertEqual(error, VantageFirmwareError(
      errors={
        "Pipetting channel 1": "No liquid level found",
        "Pipetting channel 2": "No liquid level found",
        "Pipetting channel 3": "No liquid level found",
        "Pipetting channel 4": "No liquid level found",
        "Pipetting channel 5": "No liquid level found",
        "Pipetting channel 6": "No liquid level found",
        "Pipetting channel 7": "No liquid level found",
        "Pipetting channel 8": "No liquid level found",
      },
      raw_response=resp))


class VantageCommandCatcher(Vantage):
  """ Mock backend for Vantage that catches commands and saves them instead of sending them to the
  machine. """

  def __init__(self):
    super().__init__()
    self.commands = []

  async def setup(self) -> None:
    self.setup_finished = True
    self._num_channels = 8
    self.iswap_installed = True
    self.core96_head_installed = True

  async def send_command(self, module, command, tip_pattern=None, read_timeout=0,
    write_timeout=0, **kwargs):
    cmd, _ = self._assemble_command(module, command, tip_pattern, **kwargs)
    self.commands.append(cmd)

  async def stop(self):
    self.stop_finished = True


class TestVantageLiquidHandlerCommands(unittest.IsolatedAsyncioTestCase):
  """ Test Vantage backend for liquid handling. """

  async def asyncSetUp(self):
    # pylint: disable=invalid-name
    self.mockVantage = VantageCommandCatcher()
    self.deck = VantageDeck(size=1.3)
    self.lh = LiquidHandler(self.mockVantage, deck=self.deck)

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[0] = self.tip_rack = HT_L(name="tip_rack_01")
    self.tip_car[1] = self.small_tip_rack = LT_L(name="tip_rack_02")
    self.deck.assign_child_resource(self.tip_car, rails=18)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = Cos_96_EZWash(name="plate_01", with_lid=False)
    self.plt_car[1] = self.other_plate = Cos_96_EZWash(name="plate_02", with_lid=False)
    self.deck.assign_child_resource(self.plt_car, rails=24)

    self.maxDiff = None

    await self.lh.setup()

  async def asyncTearDown(self):
    await self.lh.stop()

  def _assert_command_in_command_buffer(self, cmd: str, should_be: bool, fmt: dict):
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

    parsed_cmd = parse_vantage_fw_string(cmd, fmt)
    parsed_cmd.pop("id")

    for sent_cmd in self.mockVantage.commands:
      # When the module and command do not match, there is no point in comparing the parameters.
      if sent_cmd[0:6] != cmd[0:6]:
        continue

      try:
        parsed_sent_cmd = parse_vantage_fw_string(sent_cmd, fmt)
        parsed_sent_cmd.pop("id")

        if parsed_cmd == parsed_sent_cmd:
          self.mockVantage.commands.remove(sent_cmd)
          found = True
          break
        else:
          similar = parsed_sent_cmd
      except ValueError as e:
        # The command could not be parsed.
        print("Could not parse command", e)
        continue

    if should_be and not found:
      if similar is not None:
        # similar != parsed_cmd, but assertEqual gives a better error message than `fail`.
        self.assertEqual(similar, parsed_cmd)
      else:
        self.fail(f"Command {cmd} not found in sent commands: {self.mockVantage.commands}")
    elif not should_be and found:
      self.fail(f"Command {cmd} was found in sent commands: {self.mockVantage.commands}")

  def test_ops_to_fw_positions(self):
    """ Convert channel positions to firmware positions. """
    # pylint: disable=protected-access
    tip_a1 = self.tip_rack.get_item("A1")
    tip_f1 = self.tip_rack.get_item("F1")
    tip = self.tip_rack.get_tip("A1")

    op1 = Pickup(resource=tip_a1, tip=tip, offset=Coordinate.zero())
    op2 = Pickup(resource=tip_f1, tip=tip, offset=Coordinate.zero())
    self.assertEqual(
      self.mockVantage._ops_to_fw_positions((op1,), use_channels=[0]),
      ([4329, 0], [1458, 0], [True, False])
    )

    self.assertEqual(
      self.mockVantage._ops_to_fw_positions((op1, op2), use_channels=[0, 1]),
      ([4329, 4329, 0], [1458, 1008, 0], [True, True, False])
    )

    self.assertEqual(
      self.mockVantage._ops_to_fw_positions((op1, op2), use_channels=[1, 2]),
      ([0, 4329, 4329, 0], [0, 1458, 1008, 0], [False, True, True, False])
    )

  def _assert_command_sent_once(self, cmd: str, fmt: dict):
    """ Assert that the given command was sent to the backend exactly once. """
    self._assert_command_in_command_buffer(cmd, True, fmt)
    self._assert_command_in_command_buffer(cmd, False, fmt)

  def test_tip_definition(self):
    pass

  async def test_tip_pickup_01(self):
    await self.lh.pick_up_tips(self.tip_rack["A1", "B1"])
    self._assert_command_sent_once(
      "A1PMTPid0012xp4329 4329 0&yp1458 1368 0&tm1 1 0&tt1 1&tp2265 2265&tz2165 2165&th2450 2450&"
      "te2450 2450&ba0 0&td1 1&",
      PICKUP_TIP_FORMAT)

  async def test_tip_drop_01(self):
    await self.test_tip_pickup_01() # pick up tips first
    await self.lh.drop_tips(self.tip_rack["A1", "B1"])
    self._assert_command_sent_once(
      "A1PMTRid013xp04329 04329 0&yp1458 1368 0&tm1 1 0&tp1414 1414&tz1314 1314&th2450 2450&"
      "te2450 2450&ts0td0 0&",
      DROP_TIP_FORMAT)

  async def test_small_tip_pickup(self):
    await self.lh.pick_up_tips(self.small_tip_rack["A1"])
    self._assert_command_sent_once(
      "A1PMTPid0010xp4329 0&yp2418 0&tm1 0&tt1&tp2223&tz2163&th2450&te2450&ba0&td1&",
      PICKUP_TIP_FORMAT)

  async def test_small_tip_drop(self):
    await self.test_small_tip_pickup() # pick up tips first
    await self.lh.drop_tips(self.small_tip_rack["A1"])
    self._assert_command_sent_once(
      "A1PMTRid0012xp4329 0&yp2418 0&tp2024&tz1924&th2450&te2450&tm1 0&ts0td0&",
      DROP_TIP_FORMAT)

  async def test_aspirate(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"]) # pick up tips first
    await self.lh.aspirate(self.plate["A1"], vols=100)

    self._assert_command_sent_once(
      "A1PMDAid0248at0&tm1 0&xp05680 0&yp1460 0 &th2450&te2450&lp2001&"
      "ch000&zl1871&zx1871&ip0000&fp0000&av010830&as2500&ta000&ba00000&oa000&lm0&ll4&lv4&de0020&"
      "wt10&mv00000&mc00&mp000&ms2500&gi000&gj0gk0zu0000&zr00000&mh0000&zo005&po0109&dj0la0&lb0&"
      "lc0&",
      ASPIRATE_FORMAT)

  async def test_dispense(self):
    await self.lh.pick_up_tips(self.tip_rack["A1"]) # pick up tips first
    await self.lh.aspirate(self.plate["A1"], vols=100)
    await self.lh.dispense(self.plate["A2"], vols=100, liquid_height=[5], jet=[False],
                           blow_out=[True])

    self._assert_command_sent_once(
      "A1PMDDid0253dm3&tm1 0&xp05770 0&yp1460 0&zx1871&lp2001&zl1921&"
      "ip0000&fp0021&th2450&te2450&dv010830&ds1200&ss2500&rv000&ta050&ba00000&lm0&zo005&ll1&lv1&"
      "de0010&mv00000&mc00&mp000&ms0010&wt00&gi000&gj0gk0zu0000&dj00zr00000&mh0000&po0050&la0&",
      DISPENSE_FORMAT)

  async def test_tip_pickup96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    self._assert_command_sent_once(
      "A1HMTPid0237xp04329yp1458tt01td0tz2164th2450te2450",
      {"xp": "int", "yp": "int", "tt": "int", "td": "int", "tz": "int", "th": "int", "te": "int"})

  async def test_tip_drop96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.drop_tips96(self.tip_rack)
    self._assert_command_sent_once(
      "A1HMTRid0284xp04329yp1458tz2164th2450te2450",
      {"xp": "int", "yp": "int", "tz": "int", "th": "int", "te": "int"})

  async def test_aspirate96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate_plate(self.plate, volume=100, jet=True, blow_out=True)
    self._assert_command_sent_once(
      "A1HMDAid0236at0xp05680yp1460th2450te2450lp2001zl1871zx1871ip000fp000av010720as2500ta050"
      "ba004000oa00000lm0ll4de0020wt10mv00000mc00mp000ms2500zu0000zr00000mh000gj0gk0gi000"
      "cwFFFFFFFFFFFFFFFFFFFFFFFFpo0050",
      {"xp": "int", "yp": "int", "th": "int", "te": "int", "lp": "int", "zl": "int", "zx": "int",
       "ip": "int", "fp": "int", "av": "int", "as": "int", "ta": "int", "ba": "int", "oa": "int",
       "lm": "int", "ll": "int", "de": "int", "wt": "int", "mv": "int", "mc": "int", "mp": "int",
       "zu": "int", "zr": "int", "mh": "int", "gj": "int", "gk": "int", "gi": "int", "cw": "hex",
       "po": "int"})

  async def test_dispense96(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    await self.lh.aspirate_plate(self.plate, volume=100, jet=True, blow_out=True)
    await self.lh.dispense_plate(self.plate, volume=100, jet=True, blow_out=True)
    self._assert_command_sent_once(
      "A1HMDDid0238dm1xp05680yp1460th2450te2450lp2001zl1971zx1871ip000fp029dv010720ds4000ta050"
      "ba004000lm0ll4de0010wt00mv00000mc00mp000ms0010ss2500rv000zu0000dj00zr00000mh000gj0gk0gi000"
      "cwFFFFFFFFFFFFFFFFFFFFFFFFpo0050",
      {"xp": "int", "yp": "int", "th": "int", "te": "int", "lp": "int", "zl": "int", "zx": "int",
        "ip": "int", "fp": "int", "dv": "int", "ds": "int", "ta": "int", "ba": "int", "lm": "int",
        "ll": "int", "de": "int", "wt": "int", "mv": "int", "mc": "int", "mp": "int", "ms": "int",
        "ss": "int", "rv": "int", "zu": "int", "zr": "int", "dj": "int", "mh": "int", "gj": "int",
        "gk": "int", "gi": "int", "cw": "hex", "po": "int"})

  async def test_move_plate(self):
    await self.lh.move_plate(self.plate, self.plt_car[1], pickup_distance_from_top=5.2)

    # pickup
    self._assert_command_sent_once(
      "A1RMDGid0240xp6175yp1145zp1954yw81yo1302yg1237pt20zc0hd0te2840",
      {"xp": "int", "yp": "int", "zp": "int", "yw": "int", "yo": "int", "yg": "int", "pt": "int",
       "zc": "int", "hd": "int", "te": "int"})

    # release
    self._assert_command_sent_once(
      "A1RMDRid0242xp6175yp2105zp1954yo1302zc0hd0te2840",
      {"xp": "int", "yp": "int", "zp": "int", "yo": "int", "zc": "int", "hd": "int", "te": "int"})
