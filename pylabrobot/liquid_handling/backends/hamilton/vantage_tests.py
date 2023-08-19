""" Tests for the Hamilton Vantage backend. """

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

from tests.usb import MockDev, MockEndpoint

from .vantage import Vantage



PICKUP_TIP_FORMAT = "xp##### (n)yp#### (n)tm# (n)tt# (n)tp####tz####th####ba# (n)td# (n)"
DROP_TIP_FORMAT = "xp##### (n)yp#### (n)tm# (n)tp#### (n)tz#### (n)th#### (n)te#### (n)ts#td# (n)"


class VantageUSBCommsMocker(Vantage):
  """ Mocks PyUSB """

  async def setup(self, send_response):
    self.dev = MockDev(send_response)
    self.read_endpoint = MockEndpoint()
    self.write_endpoint = MockEndpoint()


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

  async def send_command(self, module, command, tip_pattern=None, fmt="", read_timeout=0,
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
    self.plt_car[0] = self.plate = Cos_96_EZWash(name="plate_01", with_lid=True)
    self.plt_car[1] = self.other_plate = Cos_96_EZWash(name="plate_02", with_lid=True)
    self.deck.assign_child_resource(self.plt_car, rails=26)

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

    parsed_cmd = self.mockVantage.parse_fw_string(cmd, fmt)
    parsed_cmd.pop("id")

    for sent_cmd in self.mockVantage.commands:
      # When the module and command do not match, there is no point in comparing the parameters.
      if sent_cmd[0:4] != cmd[0:4]:
        continue

      try:
        parsed_sent_cmd = self.mockVantage.parse_fw_string(sent_cmd, fmt)
        parsed_sent_cmd.pop("id")

        if parsed_cmd == parsed_sent_cmd:
          self.mockVantage.commands.remove(sent_cmd)
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

  def _assert_command_sent_once(self, cmd: str, fmt: str):
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
      "te2450 2450&ts0 0&td0 0&",
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
