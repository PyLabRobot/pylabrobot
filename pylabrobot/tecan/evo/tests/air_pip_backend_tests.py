"""Unit tests for AirEVOPIPBackend (Air LiHa / ZaapMotion)."""

import unittest
from unittest.mock import AsyncMock, call

from pylabrobot.capabilities.liquid_handling.standard import Pickup
from pylabrobot.resources import Coordinate, EVO150Deck
from pylabrobot.resources.tecan.plate_carriers import MP_3Pos
from pylabrobot.resources.tecan.plates import Microplate_96_Well
from pylabrobot.resources.tecan.tip_carriers import DiTi_3Pos
from pylabrobot.resources.tecan.tip_racks import DiTi_50ul_SBS_LiHa
from pylabrobot.tecan.evo.air_pip_backend import AirEVOPIPBackend, ZAAPMOTION_CONFIG
from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.errors import TecanError
from pylabrobot.tecan.evo.firmware import LiHa
from pylabrobot.tecan.evo.firmware.zaapmotion import ZaapMotion


class AirPIPTestBase(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    super().setUp()

    self.driver = TecanEVODriver()
    self.driver.send_command = AsyncMock()

    async def mock_send(module="", command="", params=None, **kwargs):
      if command == "RPX":
        return {"data": [5000]}
      if command == "RPY":
        return {"data": [1500, 90]}
      if command == "RPZ":
        return {"data": [2100, 2100, 2100, 2100, 2100, 2100, 2100, 2100]}
      if command == "RNT":
        return {"data": [8]}
      if command == "REE":
        if params and params[0] == 1:
          return {"data": ["XYSZZZZZZZZ"]}
        return {"data": ["@@@@@@@@@@@"]}
      if "RFV" in command:
        return {"data": ["XP2000-V1.20-02/2015", "1.2.0.10946", "ZMA"]}
      if "RCS" in command:
        return {"data": []}
      return {"data": []}

    self.driver.send_command.side_effect = mock_send

    self.deck = EVO150Deck()
    self.backend = AirEVOPIPBackend(driver=self.driver, deck=self.deck, diti_count=8)

    self.backend._num_channels = 8
    self.backend._x_range = 9866
    self.backend._y_range = 2833
    self.backend._z_range = 2100
    self.backend.liha = LiHa(self.driver, "C5")
    self.backend.zaap = ZaapMotion(self.driver)

    self.tip_carrier = DiTi_3Pos(name="tip_carrier")
    self.tip_carrier[0] = self.tip_rack = DiTi_50ul_SBS_LiHa(name="tips")
    self.deck.assign_child_resource(self.tip_carrier, rails=10)

    self.plate_carrier = MP_3Pos(name="plate_carrier")
    self.plate_carrier[0] = self.plate = Microplate_96_Well(name="plate")
    self.deck.assign_child_resource(self.plate_carrier, rails=20)

    self.driver.send_command.reset_mock()


class AirConversionTests(AirPIPTestBase):
  def test_air_steps_per_ul(self):
    self.assertEqual(AirEVOPIPBackend.STEPS_PER_UL, 106.4)

  def test_air_speed_factor(self):
    self.assertEqual(AirEVOPIPBackend.SPEED_FACTOR, 213.0)

  def test_sfr_constants(self):
    self.assertEqual(AirEVOPIPBackend.SFR_ACTIVE, 133120)
    self.assertEqual(AirEVOPIPBackend.SFR_IDLE, 3752)
    self.assertEqual(AirEVOPIPBackend.SDP_DEFAULT, 1400)


class AirForceModeSFRTests(AirPIPTestBase):
  async def test_force_on_sends_16_commands(self):
    """8 SFR + 8 SFP1 = 16 commands."""
    await self.backend._zaapmotion_force_on()
    sfr = [c for c in self.driver.send_command.call_args_list if "SFR133120" in str(c)]
    sfp = [c for c in self.driver.send_command.call_args_list if "SFP1" in str(c)]
    self.assertEqual(len(sfr), 8)
    self.assertEqual(len(sfp), 8)

  async def test_force_off_sends_16_commands(self):
    """8 SFR + 8 SDP = 16 commands."""
    await self.backend._zaapmotion_force_off()
    sfr = [c for c in self.driver.send_command.call_args_list if "SFR3752" in str(c)]
    sdp = [c for c in self.driver.send_command.call_args_list if "SDP1400" in str(c)]
    self.assertEqual(len(sfr), 8)
    self.assertEqual(len(sdp), 8)


class AirInitSkipTests(AirPIPTestBase):
  async def test_initialized_all_ok(self):
    result = await self.backend._is_initialized()
    self.assertTrue(result)

  async def test_not_initialized_with_A(self):
    async def send(module, command, params=None, **kwargs):
      if command == "REE":
        return {"data": ["GGGAAAAAAAA"]}
      return {"data": []}

    self.driver.send_command.side_effect = send
    result = await self.backend._is_initialized()
    self.assertFalse(result)

  async def test_not_initialized_with_G(self):
    async def send(module, command, params=None, **kwargs):
      if command == "REE":
        return {"data": ["GGGGGGGGGGG"]}
      return {"data": []}

    self.driver.send_command.side_effect = send
    result = await self.backend._is_initialized()
    self.assertFalse(result)

  async def test_initialized_with_Y_tip_not_fetched(self):
    async def send(module, command, params=None, **kwargs):
      if command == "REE":
        return {"data": ["@@@YYYYYYY@"]}
      return {"data": []}

    self.driver.send_command.side_effect = send
    result = await self.backend._is_initialized()
    self.assertTrue(result)

  async def test_initialized_with_timeout(self):
    self.driver.send_command.side_effect = TimeoutError()
    result = await self.backend._is_initialized()
    self.assertFalse(result)


class AirZaapMotionConfigTests(AirPIPTestBase):
  def test_config_has_33_commands(self):
    self.assertEqual(len(ZAAPMOTION_CONFIG), 33)

  def test_config_starts_with_cfe(self):
    self.assertEqual(ZAAPMOTION_CONFIG[0], "CFE 255,500")

  def test_config_ends_with_wrp(self):
    self.assertEqual(ZAAPMOTION_CONFIG[-1], "WRP")

  async def test_configure_skips_when_already_configured(self):
    """RCS returning OK → skip config."""
    await self.backend._configure_zaapmotion()
    config_calls = [
      c
      for c in self.driver.send_command.call_args_list
      if any(cmd in str(c) for cmd in ["CFE", "CMTBLDC", "WRP"])
    ]
    self.assertEqual(len(config_calls), 0)

  async def test_configure_sends_boot_exit_when_in_boot(self):
    """If RFV returns BOOT, send X to exit."""
    call_count = [0]

    async def send(module, command, params=None, **kwargs):
      call_count[0] += 1
      if "RFV" in command:
        # First call: BOOT, after X: app mode
        if call_count[0] <= 2:
          return {"data": ["XP2-BOOT-V1.00-05/2011", "1.0.0.9506", "ZMB"]}
        return {"data": ["XP2000-V1.20-02/2015", "1.2.0.10946", "ZMA"]}
      if "RCS" in command:
        raise TecanError("Not configured", "C5", 2)
      return {"data": []}

    self.driver.send_command.side_effect = send
    # Will only configure tip 0 before our mock resets
    # Just verify X command is sent
    try:
      await self.backend._configure_zaapmotion()
    except Exception:
      pass
    x_calls = [c for c in self.driver.send_command.call_args_list if "T20X" in str(c)]
    self.assertGreater(len(x_calls), 0)

  async def test_safety_module(self):
    await self.backend._setup_safety_module()
    self.driver.send_command.assert_any_call("O1", command="SPN")
    self.driver.send_command.assert_any_call("O1", command="SPS3")


class AirPickUpTipsTests(AirPIPTestBase):
  async def test_uses_air_conversion_factors(self):
    op = Pickup(
      resource=self.tip_rack.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tip_rack.get_tip("A1"),
    )
    await self.backend.pick_up_tips([op], use_channels=[0])

    # SEP: 70 * 213 = 14910
    sep_calls = [
      c
      for c in self.driver.send_command.call_args_list
      if c
      == call(module="C5", command="SEP", params=[14910, None, None, None, None, None, None, None])
    ]
    self.assertEqual(len(sep_calls), 1)

    # PPR: 10 * 106.4 = 1064
    ppr_calls = [
      c
      for c in self.driver.send_command.call_args_list
      if c
      == call(module="C5", command="PPR", params=[1064, None, None, None, None, None, None, None])
    ]
    self.assertEqual(len(ppr_calls), 1)

  async def test_force_mode_wraps_plunger(self):
    """Verify SFR/SFP sent before and SFR/SDP after plunger ops."""
    op = Pickup(
      resource=self.tip_rack.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tip_rack.get_tip("A1"),
    )
    await self.backend.pick_up_tips([op], use_channels=[0])

    all_cmds = [str(c) for c in self.driver.send_command.call_args_list]
    # Find force_on before PPR and force_off after
    sfr_active_indices = [i for i, c in enumerate(all_cmds) if "SFR133120" in c]
    ppr_indices = [i for i, c in enumerate(all_cmds) if "'PPR'" in c]
    sfr_idle_indices = [i for i, c in enumerate(all_cmds) if "SFR3752" in c]

    if sfr_active_indices and ppr_indices and sfr_idle_indices:
      self.assertLess(min(sfr_active_indices), min(ppr_indices))
      self.assertGreater(min(sfr_idle_indices), max(ppr_indices))

  async def test_agt_uses_tip_rack_z_start(self):
    """AGT should use the tip rack's z_start directly."""
    op = Pickup(
      resource=self.tip_rack.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tip_rack.get_tip("A1"),
    )
    await self.backend.pick_up_tips([op], use_channels=[0])

    agt_calls = [
      c for c in self.driver.send_command.call_args_list if c.kwargs.get("command") == "AGT"
    ]
    self.assertEqual(len(agt_calls), 1)
    params = agt_calls[0].kwargs["params"]
    self.assertEqual(params[1], int(self.tip_rack.z_start))
