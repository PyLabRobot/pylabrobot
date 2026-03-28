"""Unit tests for AirEVOBackend.

Tests conversion factors, ZaapMotion config sequence, force mode wrapping,
and init-skip logic — all with mocked USB (no hardware needed).
"""

import unittest
import unittest.mock
from unittest.mock import AsyncMock, call

from pylabrobot.liquid_handling.backends.tecan.air_evo_backend import (
  AirEVOBackend,
  ZAAPMOTION_CONFIG,
)
from pylabrobot.liquid_handling.backends.tecan.EVO_backend import LiHa
from pylabrobot.liquid_handling.standard import Pickup
from pylabrobot.resources import (
  Coordinate,
  EVO150Deck,
)
from pylabrobot.resources.tecan.plate_carriers import MP_3Pos
from pylabrobot.resources.tecan.plates import Microplate_96_Well
from pylabrobot.resources.tecan.tip_carriers import DiTi_3Pos
from pylabrobot.resources.tecan.tip_racks import DiTi_50ul_SBS_LiHa


class AirEVOTestBase(unittest.IsolatedAsyncioTestCase):
  """Base class with mocked AirEVOBackend setup."""

  def setUp(self):
    super().setUp()

    self.evo = AirEVOBackend(diti_count=8)
    self.evo.send_command = AsyncMock()

    async def send_command(module, command, params=None, **kwargs):
      if command == "RPX":
        return {"data": [9000]}
      if command == "RPY":
        return {"data": [90]}
      if command == "RPZ":
        return {"data": [2100]}
      if command == "RNT":
        return {"data": [8]}
      if command.startswith("REE"):
        if params and params[0] == 1:
          return {"data": ["XYSZZZZZZZZ"]}
        return {"data": ["@@@@@@@@@@@"]}
      if command.startswith("T2") and "RFV" in command:
        return {"data": ["XP2000-V1.20-02/2015", "1.2.0.10946", "ZMA"]}
      if command.startswith("T2") and "RCS" in command:
        return {"data": []}
      return {"data": []}

    self.evo.send_command.side_effect = send_command

    self.deck = EVO150Deck()
    self.evo.set_deck(self.deck)

    self.evo.setup = AsyncMock()
    self.evo._num_channels = 8
    self.evo._x_range = 9866
    self.evo._y_range = 2833
    self.evo._z_range = 2100
    self.evo._liha_connected = True
    self.evo._roma_connected = False
    self.evo._mca_connected = False
    self.evo.liha = LiHa(self.evo, "C5")

    # Deck setup
    self.tip_carrier = DiTi_3Pos(name="tip_carrier")
    self.tip_carrier[0] = self.tip_rack = DiTi_50ul_SBS_LiHa(name="tips")
    self.deck.assign_child_resource(self.tip_carrier, rails=15)

    self.plate_carrier = MP_3Pos(name="plate_carrier")
    self.plate_carrier[0] = self.plate = Microplate_96_Well(name="plate")
    self.deck.assign_child_resource(self.plate_carrier, rails=25)

    self.evo.send_command.reset_mock()


class ConversionFactorTests(AirEVOTestBase):
  """Test that Air LiHa uses correct conversion factors (106.4/213)."""

  def test_steps_per_ul(self):
    self.assertEqual(AirEVOBackend.STEPS_PER_UL, 106.4)

  def test_speed_factor(self):
    self.assertEqual(AirEVOBackend.SPEED_FACTOR, 213.0)

  def test_aspirate_airgap_uses_air_factors(self):
    """Verify airgap calculation uses 106.4 steps/uL and 213 speed factor."""
    from pylabrobot.liquid_handling.liquid_classes.tecan import TecanLiquidClass

    tlc = TecanLiquidClass(
      lld_mode=7,
      lld_conductivity=1,
      lld_speed=60,
      lld_distance=4,
      clot_speed=50,
      clot_limit=4,
      pmp_sensitivity=1,
      pmp_viscosity=1,
      pmp_character=0,
      density=1,
      calibration_factor=1.0,
      calibration_offset=0,
      aspirate_speed=50,
      aspirate_delay=200,
      aspirate_stag_volume=0,
      aspirate_stag_speed=20,
      aspirate_lag_volume=10,
      aspirate_lag_speed=70,
      aspirate_tag_volume=5,
      aspirate_tag_speed=20,
      aspirate_excess=0,
      aspirate_conditioning=0,
      aspirate_pinch_valve=False,
      aspirate_lld=False,
      aspirate_lld_position=3,
      aspirate_lld_offset=0,
      aspirate_mix=False,
      aspirate_mix_volume=100,
      aspirate_mix_cycles=1,
      aspirate_retract_position=4,
      aspirate_retract_speed=5,
      aspirate_retract_offset=-5,
      dispense_speed=600,
      dispense_breakoff=400,
      dispense_delay=0,
      dispense_tag=False,
      dispense_pinch_valve=False,
      dispense_lld=False,
      dispense_lld_position=7,
      dispense_lld_offset=0,
      dispense_touching_direction=0,
      dispense_touching_speed=10,
      dispense_touching_delay=100,
      dispense_mix=False,
      dispense_mix_volume=100,
      dispense_mix_cycles=1,
      dispense_retract_position=1,
      dispense_retract_speed=50,
      dispense_retract_offset=0,
    )

    # Leading airgap: 10uL at 70uL/s
    pvl, sep, ppr = self.evo._aspirate_airgap([0], [tlc], "lag")
    self.assertEqual(sep[0], int(70 * 213))  # 14910
    self.assertEqual(ppr[0], int(10 * 106.4))  # 1064

    # Trailing airgap: 5uL at 20uL/s
    pvl, sep, ppr = self.evo._aspirate_airgap([0], [tlc], "tag")
    self.assertEqual(sep[0], int(20 * 213))  # 4260
    self.assertEqual(ppr[0], int(5 * 106.4))  # 532


class ForceModeSFRTests(AirEVOTestBase):
  """Test that force mode commands are sent correctly."""

  def test_sfr_constants(self):
    self.assertEqual(AirEVOBackend.SFR_ACTIVE, 133120)
    self.assertEqual(AirEVOBackend.SFR_IDLE, 3752)
    self.assertEqual(AirEVOBackend.SDP_DEFAULT, 1400)

  async def test_force_on_sends_sfr_and_sfp(self):
    await self.evo._zaapmotion_force_on()

    # Should send SFR to all 8 tips, then SFP1 to all 8 tips
    sfr_calls = [c for c in self.evo.send_command.call_args_list if "SFR133120" in str(c)]
    sfp_calls = [c for c in self.evo.send_command.call_args_list if "SFP1" in str(c)]
    self.assertEqual(len(sfr_calls), 8)
    self.assertEqual(len(sfp_calls), 8)

  async def test_force_off_sends_sfr_and_sdp(self):
    await self.evo._zaapmotion_force_off()

    sfr_calls = [c for c in self.evo.send_command.call_args_list if "SFR3752" in str(c)]
    sdp_calls = [c for c in self.evo.send_command.call_args_list if "SDP1400" in str(c)]
    self.assertEqual(len(sfr_calls), 8)
    self.assertEqual(len(sdp_calls), 8)


class InitSkipTests(AirEVOTestBase):
  """Test init-skip logic."""

  async def test_is_initialized_all_ok(self):
    """REE0 returning all '@' should mean initialized."""
    result = await self.evo._is_initialized()
    self.assertTrue(result)

  async def test_is_initialized_with_init_failed(self):
    """REE0 with 'A' (init failed) should mean not initialized."""

    async def send_cmd(module, command, params=None, **kwargs):
      if command == "REE0":
        return {"data": ["GGGAAAAAAAA"]}
      return {"data": []}

    self.evo.send_command.side_effect = send_cmd
    result = await self.evo._is_initialized()
    self.assertFalse(result)

  async def test_is_initialized_with_not_initialized(self):
    """REE0 with 'G' (not initialized) should mean not initialized."""

    async def send_cmd(module, command, params=None, **kwargs):
      if command == "REE0":
        return {"data": ["GGGGGGGGGGG"]}
      return {"data": []}

    self.evo.send_command.side_effect = send_cmd
    result = await self.evo._is_initialized()
    self.assertFalse(result)

  async def test_is_initialized_with_tip_not_fetched(self):
    """REE0 with 'Y' (tip not fetched) means axes ARE initialized."""

    async def send_cmd(module, command, params=None, **kwargs):
      if command == "REE0":
        return {"data": ["@@@YYYYYYY@"]}
      return {"data": []}

    self.evo.send_command.side_effect = send_cmd
    result = await self.evo._is_initialized()
    self.assertTrue(result)


class ZaapMotionConfigTests(AirEVOTestBase):
  """Test ZaapMotion boot exit and motor configuration."""

  def test_config_sequence_length(self):
    """Verify all 33 config commands are defined."""
    self.assertEqual(len(ZAAPMOTION_CONFIG), 33)

  def test_config_starts_with_cfe(self):
    self.assertEqual(ZAAPMOTION_CONFIG[0], "CFE 255,500")

  def test_config_ends_with_wrp(self):
    self.assertEqual(ZAAPMOTION_CONFIG[-1], "WRP")

  async def test_configure_skips_when_rcs_ok(self):
    """If RCS returns OK, skip motor config (already configured)."""
    await self.evo._configure_zaapmotion()

    # RCS returned OK for all tips, so no config commands should be sent
    config_calls = [
      c
      for c in self.evo.send_command.call_args_list
      if any(cfg_cmd in str(c) for cfg_cmd in ["CFE", "CMTBLDC", "WRP"])
    ]
    self.assertEqual(len(config_calls), 0)

  async def test_safety_module_sends_spn_sps3(self):
    await self.evo._setup_safety_module()

    self.evo.send_command.assert_any_call("O1", command="SPN")
    self.evo.send_command.assert_any_call("O1", command="SPS3")


class PickUpTipsAirTests(AirEVOTestBase):
  """Test Air LiHa tip pickup uses correct conversion factors."""

  async def test_tip_pickup_uses_air_speed_factor(self):
    op = Pickup(
      resource=self.tip_rack.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tip_rack.get_tip("A1"),
    )
    await self.evo.pick_up_tips([op], use_channels=[0])

    # Check that SEP used Air LiHa speed factor (70 * 213 = 14910)
    sep_calls = [
      c
      for c in self.evo.send_command.call_args_list
      if c
      == call(module="C5", command="SEP", params=[14910, None, None, None, None, None, None, None])
    ]
    self.assertEqual(len(sep_calls), 1)

    # Check that PPR used Air LiHa steps/uL (10 * 106.4 = 1064)
    ppr_calls = [
      c
      for c in self.evo.send_command.call_args_list
      if c
      == call(module="C5", command="PPR", params=[1064, None, None, None, None, None, None, None])
    ]
    self.assertEqual(len(ppr_calls), 1)
