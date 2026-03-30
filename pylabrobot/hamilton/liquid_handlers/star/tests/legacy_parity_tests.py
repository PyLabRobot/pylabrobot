"""Tests that verify new backends produce the same firmware commands as legacy.

Sets up identical decks, runs operations through both, compares the firmware command strings.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, call

from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_chatterbox import STARChatterboxBackend
from pylabrobot.resources import (
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cor_96_wellplate_360ul_Fb,
)
from pylabrobot.resources.hamilton import STARLetDeck, hamilton_96_tiprack_1000uL_filter

from pylabrobot.hamilton.liquid_handlers.star.chatterbox import STARChatterboxDriver
from pylabrobot.hamilton.liquid_handlers.star.star import STAR
from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop


class _CaptureDriver(STARChatterboxDriver):
  """Captures firmware commands instead of printing them."""

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self.commands = []

  async def send_command(self, module, command, auto_id=True, tip_pattern=None,
                         write_timeout=None, read_timeout=None, wait=True,
                         fmt=None, **kwargs):
    cmd, _ = self._assemble_command(module=module, command=command,
                                     auto_id=auto_id, tip_pattern=tip_pattern, **kwargs)
    self.commands.append(cmd)
    return None


class TestLegacyParity(unittest.IsolatedAsyncioTestCase):

  async def asyncSetUp(self):
    # --- Legacy setup ---
    self.legacy_backend = STARChatterboxBackend()
    self.legacy_backend._write_and_read_command = AsyncMock(return_value=None)
    self.legacy_deck = STARLetDeck()

    tip_car = TIP_CAR_480_A00(name="tip_carrier")
    tip_car[0] = hamilton_96_tiprack_1000uL_filter(name="tips_01")
    self.legacy_deck.assign_child_resource(tip_car, rails=3)

    plt_car = PLT_CAR_L5AC_A00(name="plate_carrier")
    plt_car[0] = Cor_96_wellplate_360ul_Fb(name="plate_01")
    self.legacy_deck.assign_child_resource(plt_car, rails=15)

    self.lh = LiquidHandler(self.legacy_backend, deck=self.legacy_deck)
    await self.lh.setup()

    # --- New setup ---
    self.new_driver = _CaptureDriver()
    self.new_deck = STARLetDeck()

    tip_car2 = TIP_CAR_480_A00(name="tip_carrier")
    tip_car2[0] = hamilton_96_tiprack_1000uL_filter(name="tips_01")
    self.new_deck.assign_child_resource(tip_car2, rails=3)

    plt_car2 = PLT_CAR_L5AC_A00(name="plate_carrier")
    plt_car2[0] = Cor_96_wellplate_360ul_Fb(name="plate_01")
    self.new_deck.assign_child_resource(plt_car2, rails=15)

    self.star = STAR(deck=self.new_deck, chatterbox=True)
    # Replace the driver with our capture driver
    self.star.driver = self.new_driver
    await self.star.driver.setup()
    from .pip_backend import STARPIPBackend
    self.star.driver.pip = STARPIPBackend(self.new_driver)
    from .head96_backend import STARHead96Backend
    self.star.driver.head96 = STARHead96Backend(self.new_driver)
    from pylabrobot.capabilities.liquid_handling.pip import PIP
    from pylabrobot.capabilities.liquid_handling.head96 import Head96
    self.star.pip = PIP(backend=self.star.driver.pip)
    self.star.head96 = Head96(backend=self.star.driver.head96)
    self.star._capabilities = [self.star.pip, self.star.head96]
    for cap in self.star._capabilities:
      await cap._on_setup()
    self.star._setup_finished = True

  def _get_legacy_commands(self):
    """Extract firmware command strings from legacy mock calls."""
    commands = []
    for c in self.legacy_backend._write_and_read_command.call_args_list:
      cmd = c.kwargs.get("cmd") or c.args[1]
      commands.append(cmd)
    return commands

  def _assert_commands_match(self, legacy_cmds, new_cmds, label=""):
    self.assertEqual(len(legacy_cmds), len(new_cmds),
                     f"{label} command count mismatch: legacy={len(legacy_cmds)}, new={len(new_cmds)}\n"
                     f"legacy: {legacy_cmds}\nnew: {new_cmds}")
    for i, (leg, new) in enumerate(zip(legacy_cmds, new_cmds)):
      # Strip the id (id####, 6 chars at position 4-9) since counters won't match
      leg_no_id = leg[:4] + leg[10:]
      new_no_id = new[:4] + new[10:]
      self.assertEqual(leg_no_id, new_no_id,
                       f"{label} command {i} mismatch:\nlegacy: {leg}\nnew:    {new}")

  async def test_pick_up_tips(self):
    tiprack_legacy = self.legacy_deck.get_resource("tips_01")
    tiprack_new = self.new_deck.get_resource("tips_01")

    self.legacy_backend._write_and_read_command.reset_mock()
    await self.lh.pick_up_tips(tiprack_legacy["A1:C1"])
    legacy_cmds = self._get_legacy_commands()

    self.new_driver.commands.clear()
    await self.star.pip.pick_up_tips(tiprack_new["A1:C1"])
    new_cmds = self.new_driver.commands

    self._assert_commands_match(legacy_cmds, new_cmds, "pick_up_tips")

  async def test_aspirate(self):
    tiprack_legacy = self.legacy_deck.get_resource("tips_01")
    tiprack_new = self.new_deck.get_resource("tips_01")
    plate_legacy = self.legacy_deck.get_resource("plate_01")
    plate_new = self.new_deck.get_resource("plate_01")

    # Pick up tips first (both sides)
    await self.lh.pick_up_tips(tiprack_legacy["A1:C1"])
    await self.star.pip.pick_up_tips(tiprack_new["A1:C1"])

    # Set volume so legacy doesn't complain
    for well in plate_legacy.get_items(["A1", "B1", "C1"]):
      well.tracker.set_volume(200)
    for well in plate_new.get_items(["A1", "B1", "C1"]):
      well.tracker.set_volume(200)

    # Aspirate
    self.legacy_backend._write_and_read_command.reset_mock()
    await self.lh.aspirate(plate_legacy["A1:C1"], vols=[100.0, 50.0, 200.0])
    legacy_cmds = self._get_legacy_commands()

    self.new_driver.commands.clear()
    await self.star.pip.aspirate(plate_new["A1:C1"], vols=[100.0, 50.0, 200.0])
    new_cmds = self.new_driver.commands

    self._assert_commands_match(legacy_cmds, new_cmds, "aspirate")

  async def test_dispense(self):
    tiprack_legacy = self.legacy_deck.get_resource("tips_01")
    tiprack_new = self.new_deck.get_resource("tips_01")
    plate_legacy = self.legacy_deck.get_resource("plate_01")
    plate_new = self.new_deck.get_resource("plate_01")

    # Pick up tips + aspirate first
    await self.lh.pick_up_tips(tiprack_legacy["A1:C1"])
    await self.star.pip.pick_up_tips(tiprack_new["A1:C1"])
    for well in plate_legacy.get_items(["A1", "B1", "C1"]):
      well.tracker.set_volume(200)
    for well in plate_new.get_items(["A1", "B1", "C1"]):
      well.tracker.set_volume(200)
    await self.lh.aspirate(plate_legacy["A1:C1"], vols=[100.0, 50.0, 200.0])
    await self.star.pip.aspirate(plate_new["A1:C1"], vols=[100.0, 50.0, 200.0])

    # Dispense
    self.legacy_backend._write_and_read_command.reset_mock()
    await self.lh.dispense(plate_legacy["D1:F1"], vols=[100.0, 50.0, 200.0])
    legacy_cmds = self._get_legacy_commands()

    self.new_driver.commands.clear()
    await self.star.pip.dispense(plate_new["D1:F1"], vols=[100.0, 50.0, 200.0])
    new_cmds = self.new_driver.commands

    self._assert_commands_match(legacy_cmds, new_cmds, "dispense")

  async def test_drop_tips(self):
    tiprack_legacy = self.legacy_deck.get_resource("tips_01")
    tiprack_new = self.new_deck.get_resource("tips_01")

    # Pick up tips first
    await self.lh.pick_up_tips(tiprack_legacy["A1:C1"])
    await self.star.pip.pick_up_tips(tiprack_new["A1:C1"])

    # Drop tips
    self.legacy_backend._write_and_read_command.reset_mock()
    await self.lh.drop_tips(tiprack_legacy["A1:C1"])
    legacy_cmds = self._get_legacy_commands()

    self.new_driver.commands.clear()
    await self.star.pip.drop_tips(tiprack_new["A1:C1"])
    new_cmds = self.new_driver.commands

    self._assert_commands_match(legacy_cmds, new_cmds, "drop_tips")
