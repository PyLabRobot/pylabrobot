"""Tests for Hamilton Nimbus backend implementation.

This module tests the Nimbus backend, its command classes, tip type handling,
and liquid handling operations.
"""

import unittest
import unittest.mock
from typing import Optional

from pylabrobot.liquid_handling.backends.hamilton.nimbus_backend import (
  Aspirate,
  DisableADC,
  Dispense,
  DropTips,
  DropTipsRoll,
  EnableADC,
  GetChannelConfiguration,
  GetChannelConfiguration_1,
  InitializeSmartRoll,
  IsDoorLocked,
  IsInitialized,
  IsTipPresent,
  LockDoor,
  NimbusBackend,
  NimbusTipType,
  Park,
  PickupTips,
  PreInitializeSmart,
  SetChannelConfiguration,
  UnlockDoor,
  _get_tip_type_from_tip,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import HoiParams, HoiParamsParser
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import HamiltonProtocol
from pylabrobot.liquid_handling.standard import (
  Drop,
  Pickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb
from pylabrobot.resources.hamilton import HamiltonTip, TipPickupMethod, TipSize
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck
from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_300uL


class TestNimbusTipType(unittest.TestCase):
  """Tests for NimbusTipType enum and tip type mapping."""

  def test_tip_type_values(self):
    self.assertEqual(NimbusTipType.STANDARD_300UL, 0)
    self.assertEqual(NimbusTipType.STANDARD_300UL_FILTER, 1)
    self.assertEqual(NimbusTipType.LOW_VOLUME_10UL, 2)
    self.assertEqual(NimbusTipType.LOW_VOLUME_10UL_FILTER, 3)
    self.assertEqual(NimbusTipType.HIGH_VOLUME_1000UL, 4)
    self.assertEqual(NimbusTipType.HIGH_VOLUME_1000UL_FILTER, 5)
    self.assertEqual(NimbusTipType.TIP_50UL, 22)
    self.assertEqual(NimbusTipType.TIP_50UL_FILTER, 23)
    self.assertEqual(NimbusTipType.SLIM_CORE_300UL, 36)

  def test_get_tip_type_low_volume(self):
    tip = HamiltonTip(
      name="test_tip",
      has_filter=False,
      total_tip_length=50.0,
      maximal_volume=10.0,
      tip_size=TipSize.LOW_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertEqual(_get_tip_type_from_tip(tip), NimbusTipType.LOW_VOLUME_10UL)

  def test_get_tip_type_low_volume_filter(self):
    tip = HamiltonTip(
      name="test_tip",
      has_filter=True,
      total_tip_length=50.0,
      maximal_volume=10.0,
      tip_size=TipSize.LOW_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertEqual(_get_tip_type_from_tip(tip), NimbusTipType.LOW_VOLUME_10UL_FILTER)

  def test_get_tip_type_standard_50ul(self):
    tip = HamiltonTip(
      name="test_tip",
      has_filter=False,
      total_tip_length=50.0,
      maximal_volume=50.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertEqual(_get_tip_type_from_tip(tip), NimbusTipType.TIP_50UL)

  def test_get_tip_type_standard_50ul_filter(self):
    tip = HamiltonTip(
      name="test_tip",
      has_filter=True,
      total_tip_length=50.0,
      maximal_volume=50.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertEqual(_get_tip_type_from_tip(tip), NimbusTipType.TIP_50UL_FILTER)

  def test_get_tip_type_standard_300ul(self):
    tip = HamiltonTip(
      name="test_tip",
      has_filter=False,
      total_tip_length=59.9,
      maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertEqual(_get_tip_type_from_tip(tip), NimbusTipType.STANDARD_300UL)

  def test_get_tip_type_standard_300ul_filter(self):
    tip = HamiltonTip(
      name="test_tip",
      has_filter=True,
      total_tip_length=59.9,
      maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertEqual(_get_tip_type_from_tip(tip), NimbusTipType.STANDARD_300UL_FILTER)

  def test_get_tip_type_high_volume(self):
    tip = HamiltonTip(
      name="test_tip",
      has_filter=False,
      total_tip_length=95.1,
      maximal_volume=1000.0,
      tip_size=TipSize.HIGH_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertEqual(_get_tip_type_from_tip(tip), NimbusTipType.HIGH_VOLUME_1000UL)

  def test_get_tip_type_high_volume_filter(self):
    tip = HamiltonTip(
      name="test_tip",
      has_filter=True,
      total_tip_length=95.1,
      maximal_volume=1000.0,
      tip_size=TipSize.HIGH_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertEqual(_get_tip_type_from_tip(tip), NimbusTipType.HIGH_VOLUME_1000UL_FILTER)

  def test_get_tip_type_non_hamilton_tip_raises(self):
    from pylabrobot.resources import Tip

    # Regular Tip (non-Hamilton) should raise
    tip = Tip(
      name="test_tip",
      has_filter=False,
      total_tip_length=50.0,
      maximal_volume=300.0,
      fitting_depth=8.0,
    )
    with self.assertRaises(ValueError) as ctx:
      _get_tip_type_from_tip(tip)
    self.assertIn("HamiltonTip", str(ctx.exception))


class TestNimbusCommands(unittest.TestCase):
  """Tests for Nimbus command classes."""

  def test_lock_door_command(self):
    cmd = LockDoor(Address(1, 1, 268))
    self.assertEqual(cmd.interface_id, 1)
    self.assertEqual(cmd.command_id, 1)
    self.assertEqual(cmd.protocol, HamiltonProtocol.OBJECT_DISCOVERY)
    # Default action_code is 3 (COMMAND_REQUEST)
    self.assertEqual(cmd.action_code, 3)

  def test_unlock_door_command(self):
    cmd = UnlockDoor(Address(1, 1, 268))
    self.assertEqual(cmd.interface_id, 1)
    self.assertEqual(cmd.command_id, 2)

  def test_is_door_locked_command(self):
    cmd = IsDoorLocked(Address(1, 1, 268))
    self.assertEqual(cmd.interface_id, 1)
    self.assertEqual(cmd.command_id, 3)
    # STATUS_REQUEST must have action_code=0
    self.assertEqual(cmd.action_code, 0)

  def test_is_door_locked_parse_response(self):
    # Simulate response: bool fragment with True
    response_data = HoiParams().bool_value(True).build()
    result = IsDoorLocked.parse_response_parameters(response_data)
    self.assertEqual(result, {"locked": True})

    response_data = HoiParams().bool_value(False).build()
    result = IsDoorLocked.parse_response_parameters(response_data)
    self.assertEqual(result, {"locked": False})

  def test_park_command(self):
    cmd = Park(Address(1, 1, 48896))
    self.assertEqual(cmd.interface_id, 1)
    self.assertEqual(cmd.command_id, 3)

  def test_is_initialized_command(self):
    cmd = IsInitialized(Address(1, 1, 48896))
    self.assertEqual(cmd.interface_id, 1)
    self.assertEqual(cmd.command_id, 14)
    self.assertEqual(cmd.action_code, 0)

  def test_is_initialized_parse_response(self):
    response_data = HoiParams().bool_value(True).build()
    result = IsInitialized.parse_response_parameters(response_data)
    self.assertEqual(result, {"initialized": True})

  def test_is_tip_present_command(self):
    cmd = IsTipPresent(Address(1, 1, 257))
    self.assertEqual(cmd.interface_id, 1)
    self.assertEqual(cmd.command_id, 16)
    self.assertEqual(cmd.action_code, 0)

  def test_is_tip_present_parse_response(self):
    # Simulate response with i16 array
    response_data = HoiParams().i16_array([1, 0, 1, 0, 0, 0, 0, 0]).build()
    result = IsTipPresent.parse_response_parameters(response_data)
    self.assertEqual(result["tip_present"], [1, 0, 1, 0, 0, 0, 0, 0])

  def test_get_channel_configuration_1_command(self):
    cmd = GetChannelConfiguration_1(Address(1, 1, 48896))
    self.assertEqual(cmd.interface_id, 1)
    self.assertEqual(cmd.command_id, 15)
    self.assertEqual(cmd.action_code, 0)

  def test_get_channel_configuration_1_parse_response(self):
    response_data = HoiParams().u16(8).i16_array([0, 0, 0, 0, 0, 0, 0, 0]).build()
    result = GetChannelConfiguration_1.parse_response_parameters(response_data)
    self.assertEqual(result["channels"], 8)
    self.assertEqual(result["channel_types"], [0, 0, 0, 0, 0, 0, 0, 0])

  def test_pre_initialize_smart_command(self):
    cmd = PreInitializeSmart(Address(1, 1, 257))
    self.assertEqual(cmd.interface_id, 1)
    self.assertEqual(cmd.command_id, 32)


class TestNimbusCommandParameters(unittest.TestCase):
  """Tests for Nimbus command parameter building."""

  def test_initialize_smart_roll_parameters(self):
    cmd = InitializeSmartRoll(
      dest=Address(1, 1, 48896),
      x_positions=[1000, 2000, 3000, 4000, 0, 0, 0, 0],
      y_positions=[5000, 6000, 7000, 8000, 0, 0, 0, 0],
      begin_tip_deposit_process=[9000, 9000, 9000, 9000, 0, 0, 0, 0],
      end_tip_deposit_process=[8500, 8500, 8500, 8500, 0, 0, 0, 0],
      z_position_at_end_of_a_command=[14600, 14600, 14600, 14600, 0, 0, 0, 0],
      roll_distances=[900, 900, 900, 900, 0, 0, 0, 0],
    )

    params = cmd.build_parameters()
    data = params.build()

    # Verify parameters were built - should have 6 i32_array fragments
    parser = HoiParamsParser(data)
    results = parser.parse_all()
    self.assertEqual(len(results), 6)

    # Verify x_positions
    self.assertEqual(results[0][1], [1000, 2000, 3000, 4000, 0, 0, 0, 0])
    # Verify y_positions
    self.assertEqual(results[1][1], [5000, 6000, 7000, 8000, 0, 0, 0, 0])

  def test_pickup_tips_parameters(self):
    cmd = PickupTips(
      dest=Address(1, 1, 257),
      channels_involved=[1, 1, 0, 0, 0, 0, 0, 0],
      x_positions=[10000, 11000, 0, 0, 0, 0, 0, 0],
      y_positions=[20000, 21000, 0, 0, 0, 0, 0, 0],
      minimum_traverse_height_at_beginning_of_a_command=14600,
      begin_tip_pick_up_process=[5000, 5000, 0, 0, 0, 0, 0, 0],
      end_tip_pick_up_process=[4500, 4500, 0, 0, 0, 0, 0, 0],
      tip_types=[0, 0, 0, 0, 0, 0, 0, 0],
    )

    params = cmd.build_parameters()
    data = params.build()

    parser = HoiParamsParser(data)
    results = parser.parse_all()

    # Should have: u16_array, i32_array, i32_array, i32, i32_array, i32_array, u16_array
    self.assertEqual(len(results), 7)

    # Verify channels_involved (u16 array)
    self.assertEqual(results[0][1], [1, 1, 0, 0, 0, 0, 0, 0])
    # Verify traverse height (i32)
    self.assertEqual(results[3][1], 14600)

  def test_drop_tips_parameters(self):
    cmd = DropTips(
      dest=Address(1, 1, 257),
      channels_involved=[1, 0, 0, 0, 0, 0, 0, 0],
      x_positions=[10000, 0, 0, 0, 0, 0, 0, 0],
      y_positions=[20000, 0, 0, 0, 0, 0, 0, 0],
      minimum_traverse_height_at_beginning_of_a_command=14600,
      begin_tip_deposit_process=[5000, 0, 0, 0, 0, 0, 0, 0],
      end_tip_deposit_process=[4500, 0, 0, 0, 0, 0, 0, 0],
      z_position_at_end_of_a_command=[14600, 0, 0, 0, 0, 0, 0, 0],
      default_waste=False,
    )

    params = cmd.build_parameters()
    data = params.build()

    parser = HoiParamsParser(data)
    results = parser.parse_all()

    # Should have: u16_array, i32_array, i32_array, i32, i32_array, i32_array, i32_array, bool
    self.assertEqual(len(results), 8)
    # Verify default_waste (bool)
    self.assertEqual(results[7][1], False)

  def test_drop_tips_roll_parameters(self):
    cmd = DropTipsRoll(
      dest=Address(1, 1, 257),
      channels_involved=[1, 1, 1, 1, 0, 0, 0, 0],
      x_positions=[10000, 11000, 12000, 13000, 0, 0, 0, 0],
      y_positions=[20000, 21000, 22000, 23000, 0, 0, 0, 0],
      minimum_traverse_height_at_beginning_of_a_command=14600,
      begin_tip_deposit_process=[5000, 5000, 5000, 5000, 0, 0, 0, 0],
      end_tip_deposit_process=[4500, 4500, 4500, 4500, 0, 0, 0, 0],
      z_position_at_end_of_a_command=[14600, 14600, 14600, 14600, 0, 0, 0, 0],
      roll_distances=[900, 900, 900, 900, 0, 0, 0, 0],
    )

    params = cmd.build_parameters()
    data = params.build()

    parser = HoiParamsParser(data)
    results = parser.parse_all()

    # Should have: u16_array, i32_array, i32_array, i32, i32_array, i32_array, i32_array, i32_array
    self.assertEqual(len(results), 8)
    # Verify roll_distances
    self.assertEqual(results[7][1], [900, 900, 900, 900, 0, 0, 0, 0])

  def test_enable_adc_parameters(self):
    cmd = EnableADC(
      dest=Address(1, 1, 257),
      channels_involved=[1, 1, 0, 0, 0, 0, 0, 0],
    )

    params = cmd.build_parameters()
    data = params.build()

    parser = HoiParamsParser(data)
    results = parser.parse_all()

    self.assertEqual(len(results), 1)
    self.assertEqual(results[0][1], [1, 1, 0, 0, 0, 0, 0, 0])

  def test_disable_adc_parameters(self):
    cmd = DisableADC(
      dest=Address(1, 1, 257),
      channels_involved=[1, 1, 1, 1, 0, 0, 0, 0],
    )

    params = cmd.build_parameters()
    data = params.build()

    parser = HoiParamsParser(data)
    results = parser.parse_all()

    self.assertEqual(len(results), 1)
    self.assertEqual(results[0][1], [1, 1, 1, 1, 0, 0, 0, 0])

  def test_set_channel_configuration_parameters(self):
    cmd = SetChannelConfiguration(
      dest=Address(1, 1, 257),
      channel=1,
      indexes=[1, 3, 4],
      enables=[True, False, False, False],
    )

    params = cmd.build_parameters()
    data = params.build()

    parser = HoiParamsParser(data)
    results = parser.parse_all()

    # Should have: u16, i16_array, bool_array
    self.assertEqual(len(results), 3)
    self.assertEqual(results[0][1], 1)  # channel
    self.assertEqual(results[1][1], [1, 3, 4])  # indexes
    self.assertEqual(results[2][1], [True, False, False, False])  # enables

  def test_get_channel_configuration_parameters(self):
    cmd = GetChannelConfiguration(
      dest=Address(1, 1, 257),
      channel=2,
      indexes=[2],
    )

    params = cmd.build_parameters()
    data = params.build()

    parser = HoiParamsParser(data)
    results = parser.parse_all()

    self.assertEqual(len(results), 2)
    self.assertEqual(results[0][1], 2)  # channel
    self.assertEqual(results[1][1], [2])  # indexes


class TestAspirateDispenseCommands(unittest.TestCase):
  """Tests for Aspirate and Dispense command parameter building."""

  def test_aspirate_parameters(self):
    cmd = Aspirate(
      dest=Address(1, 1, 257),
      aspirate_type=[0, 0, 0, 0, 0, 0, 0, 0],
      channels_involved=[1, 0, 0, 0, 0, 0, 0, 0],
      x_positions=[10000, 0, 0, 0, 0, 0, 0, 0],
      y_positions=[20000, 0, 0, 0, 0, 0, 0, 0],
      minimum_traverse_height_at_beginning_of_a_command=14600,
      lld_search_height=[1000, 0, 0, 0, 0, 0, 0, 0],
      liquid_height=[500, 0, 0, 0, 0, 0, 0, 0],
      immersion_depth=[300, 0, 0, 0, 0, 0, 0, 0],
      surface_following_distance=[0, 0, 0, 0, 0, 0, 0, 0],
      minimum_height=[0, 0, 0, 0, 0, 0, 0, 0],
      clot_detection_height=[0, 0, 0, 0, 0, 0, 0, 0],
      min_z_endpos=0,
      swap_speed=[1000, 0, 0, 0, 0, 0, 0, 0],
      blow_out_air_volume=[0, 0, 0, 0, 0, 0, 0, 0],
      pre_wetting_volume=[0, 0, 0, 0, 0, 0, 0, 0],
      aspirate_volume=[1000, 0, 0, 0, 0, 0, 0, 0],  # 100uL in 0.1uL units
      transport_air_volume=[0, 0, 0, 0, 0, 0, 0, 0],
      aspiration_speed=[500, 0, 0, 0, 0, 0, 0, 0],
      settling_time=[10, 0, 0, 0, 0, 0, 0, 0],
      mix_volume=[0, 0, 0, 0, 0, 0, 0, 0],
      mix_cycles=[0, 0, 0, 0, 0, 0, 0, 0],
      mix_position_from_liquid_surface=[0, 0, 0, 0, 0, 0, 0, 0],
      mix_surface_following_distance=[0, 0, 0, 0, 0, 0, 0, 0],
      mix_speed=[0, 0, 0, 0, 0, 0, 0, 0],
      tube_section_height=[0, 0, 0, 0, 0, 0, 0, 0],
      tube_section_ratio=[0, 0, 0, 0, 0, 0, 0, 0],
      lld_mode=[0, 0, 0, 0, 0, 0, 0, 0],
      gamma_lld_sensitivity=[0, 0, 0, 0, 0, 0, 0, 0],
      dp_lld_sensitivity=[0, 0, 0, 0, 0, 0, 0, 0],
      lld_height_difference=[0, 0, 0, 0, 0, 0, 0, 0],
      tadm_enabled=False,
      limit_curve_index=[0, 0, 0, 0, 0, 0, 0, 0],
      recording_mode=0,
    )

    params = cmd.build_parameters()
    data = params.build()

    # Verify the parameters were built
    parser = HoiParamsParser(data)
    results = parser.parse_all()

    # Aspirate has 33 parameters
    self.assertEqual(len(results), 33)

    # Verify some key parameters
    # aspirate_type (i16_array)
    self.assertEqual(results[0][1], [0, 0, 0, 0, 0, 0, 0, 0])
    # channels_involved (u16_array)
    self.assertEqual(results[1][1], [1, 0, 0, 0, 0, 0, 0, 0])
    # aspirate_volume (u32_array) - at index 15
    self.assertEqual(results[15][1], [1000, 0, 0, 0, 0, 0, 0, 0])
    # tadm_enabled (bool) - at index 30
    self.assertEqual(results[30][1], False)
    # recording_mode (u16) - at index 32
    self.assertEqual(results[32][1], 0)

  def test_dispense_parameters(self):
    cmd = Dispense(
      dest=Address(1, 1, 257),
      dispense_type=[0, 0, 0, 0, 0, 0, 0, 0],
      channels_involved=[1, 0, 0, 0, 0, 0, 0, 0],
      x_positions=[10000, 0, 0, 0, 0, 0, 0, 0],
      y_positions=[20000, 0, 0, 0, 0, 0, 0, 0],
      minimum_traverse_height_at_beginning_of_a_command=14600,
      lld_search_height=[1000, 0, 0, 0, 0, 0, 0, 0],
      liquid_height=[500, 0, 0, 0, 0, 0, 0, 0],
      immersion_depth=[300, 0, 0, 0, 0, 0, 0, 0],
      surface_following_distance=[0, 0, 0, 0, 0, 0, 0, 0],
      minimum_height=[0, 0, 0, 0, 0, 0, 0, 0],
      min_z_endpos=0,
      swap_speed=[1000, 0, 0, 0, 0, 0, 0, 0],
      transport_air_volume=[0, 0, 0, 0, 0, 0, 0, 0],
      dispense_volume=[1000, 0, 0, 0, 0, 0, 0, 0],  # 100uL in 0.1uL units
      stop_back_volume=[0, 0, 0, 0, 0, 0, 0, 0],
      blow_out_air_volume=[0, 0, 0, 0, 0, 0, 0, 0],
      dispense_speed=[500, 0, 0, 0, 0, 0, 0, 0],
      cut_off_speed=[0, 0, 0, 0, 0, 0, 0, 0],
      settling_time=[10, 0, 0, 0, 0, 0, 0, 0],
      mix_volume=[0, 0, 0, 0, 0, 0, 0, 0],
      mix_cycles=[0, 0, 0, 0, 0, 0, 0, 0],
      mix_position_from_liquid_surface=[0, 0, 0, 0, 0, 0, 0, 0],
      mix_surface_following_distance=[0, 0, 0, 0, 0, 0, 0, 0],
      mix_speed=[0, 0, 0, 0, 0, 0, 0, 0],
      side_touch_off_distance=0,
      dispense_offset=[0, 0, 0, 0, 0, 0, 0, 0],
      tube_section_height=[0, 0, 0, 0, 0, 0, 0, 0],
      tube_section_ratio=[0, 0, 0, 0, 0, 0, 0, 0],
      lld_mode=[0, 0, 0, 0, 0, 0, 0, 0],
      gamma_lld_sensitivity=[0, 0, 0, 0, 0, 0, 0, 0],
      tadm_enabled=False,
      limit_curve_index=[0, 0, 0, 0, 0, 0, 0, 0],
      recording_mode=0,
    )

    params = cmd.build_parameters()
    data = params.build()

    parser = HoiParamsParser(data)
    results = parser.parse_all()

    # Dispense has 33 parameters
    self.assertEqual(len(results), 33)

    # Verify some key parameters
    # dispense_type (i16_array)
    self.assertEqual(results[0][1], [0, 0, 0, 0, 0, 0, 0, 0])
    # dispense_volume (u32_array)
    self.assertEqual(results[13][1], [1000, 0, 0, 0, 0, 0, 0, 0])


class TestNimbusBackendUnit(unittest.IsolatedAsyncioTestCase):
  """Unit tests for NimbusBackend class (no actual connection)."""

  async def test_backend_init(self):
    backend = NimbusBackend(host="192.168.1.100", port=2000)
    self.assertEqual(backend.io._host, "192.168.1.100")
    self.assertEqual(backend.io._port, 2000)
    self.assertIsNone(backend._num_channels)
    self.assertIsNone(backend._pipette_address)
    self.assertIsNone(backend._door_lock_address)
    self.assertIsNone(backend._nimbus_core_address)
    self.assertEqual(backend._channel_traversal_height, 146.0)

  async def test_backend_init_default_port(self):
    backend = NimbusBackend(host="192.168.1.100")
    self.assertEqual(backend.io._port, 2000)

  async def test_num_channels_before_setup_raises(self):
    backend = NimbusBackend(host="192.168.1.100")
    with self.assertRaises(RuntimeError) as ctx:
      _ = backend.num_channels
    self.assertIn("setup()", str(ctx.exception))

  async def test_set_minimum_channel_traversal_height(self):
    backend = NimbusBackend(host="192.168.1.100")
    backend.set_minimum_channel_traversal_height(100.0)
    self.assertEqual(backend._channel_traversal_height, 100.0)

  async def test_set_minimum_channel_traversal_height_invalid(self):
    backend = NimbusBackend(host="192.168.1.100")
    with self.assertRaises(ValueError):
      backend.set_minimum_channel_traversal_height(0)
    with self.assertRaises(ValueError):
      backend.set_minimum_channel_traversal_height(150)
    with self.assertRaises(ValueError):
      backend.set_minimum_channel_traversal_height(-10)

  async def test_fill_by_channels(self):
    backend = NimbusBackend(host="192.168.1.100")
    backend._num_channels = 8

    # Test with channels 0, 2, 4
    values = [100, 200, 300]
    use_channels = [0, 2, 4]
    result = backend._fill_by_channels(values, use_channels, default=0)

    expected = [100, 0, 200, 0, 300, 0, 0, 0]
    self.assertEqual(result, expected)

  async def test_fill_by_channels_mismatched_lengths(self):
    backend = NimbusBackend(host="192.168.1.100")
    backend._num_channels = 8

    with self.assertRaises(ValueError):
      backend._fill_by_channels([1, 2], [0, 1, 2], default=0)


def _mock_send_command_response(command) -> Optional[dict]:
  """Return appropriate mock responses based on command type."""
  if isinstance(command, IsTipPresent):
    return {"tip_present": [0] * 8}
  if isinstance(command, IsDoorLocked):
    return {"locked": True}
  if isinstance(command, IsInitialized):
    return {"initialized": True}
  if isinstance(command, GetChannelConfiguration_1):
    return {"channels": 8, "channel_types": [0] * 8}
  if isinstance(command, GetChannelConfiguration):
    return {"enabled": [False]}
  return None


def _setup_backend() -> NimbusBackend:
  """Create a NimbusBackend with pre-configured state for testing."""
  backend = NimbusBackend(host="192.168.1.100", port=2000)
  backend._num_channels = 8
  backend._pipette_address = Address(1, 1, 257)
  backend._door_lock_address = Address(1, 1, 268)
  backend._nimbus_core_address = Address(1, 1, 48896)
  backend._is_initialized = True
  return backend


def _setup_backend_with_deck(deck: NimbusDeck) -> NimbusBackend:
  """Create a NimbusBackend with pre-configured state and deck for testing."""
  backend = _setup_backend()
  backend._deck = deck
  return backend


class TestNimbusBackendCommands(unittest.IsolatedAsyncioTestCase):
  """Tests for NimbusBackend command methods."""

  async def asyncSetUp(self):
    self.backend = _setup_backend()
    self.mock_send = unittest.mock.AsyncMock(side_effect=_mock_send_command_response)
    self.backend.send_command = self.mock_send  # type: ignore[method-assign]

  def _get_command(self, cmd_type):
    for call in self.mock_send.call_args_list:
      if isinstance(call.args[0], cmd_type):
        return call.args[0]
    return None

  async def test_lock_door(self):
    await self.backend.lock_door()
    self.assertEqual(self.mock_send.call_count, 1)
    self.assertIsInstance(self._get_command(LockDoor), LockDoor)

  async def test_unlock_door(self):
    await self.backend.unlock_door()
    self.assertEqual(self.mock_send.call_count, 1)
    self.assertIsInstance(self._get_command(UnlockDoor), UnlockDoor)

  async def test_is_door_locked(self):
    result = await self.backend.is_door_locked()
    self.assertEqual(self.mock_send.call_count, 1)
    self.assertIsInstance(self._get_command(IsDoorLocked), IsDoorLocked)
    self.assertTrue(result)

  async def test_park(self):
    await self.backend.park()
    self.assertEqual(self.mock_send.call_count, 1)
    self.assertIsInstance(self._get_command(Park), Park)

  async def test_door_methods_without_address_raise(self):
    self.backend._door_lock_address = None

    with self.assertRaises(RuntimeError):
      await self.backend.lock_door()

    with self.assertRaises(RuntimeError):
      await self.backend.unlock_door()

    with self.assertRaises(RuntimeError):
      await self.backend.is_door_locked()

  async def test_park_without_address_raises(self):
    self.backend._nimbus_core_address = None

    with self.assertRaises(RuntimeError):
      await self.backend.park()


class TestNimbusBackendSerialization(unittest.IsolatedAsyncioTestCase):
  """Tests for NimbusBackend serialization."""

  async def test_serialize(self):
    backend = NimbusBackend(host="192.168.1.100", port=2000)
    backend._client_id = 5

    serialized = backend.serialize()
    self.assertEqual(serialized["client_id"], 5)
    self.assertIn("instrument_addresses", serialized)


class TestNimbusLiquidHandling(unittest.IsolatedAsyncioTestCase):
  """Tests for NimbusBackend liquid handling command generation."""

  async def asyncSetUp(self):
    self.deck = NimbusDeck()
    self.backend = _setup_backend_with_deck(self.deck)
    self.mock_send = unittest.mock.AsyncMock(side_effect=_mock_send_command_response)
    self.backend.send_command = self.mock_send  # type: ignore[method-assign]

    self.tip_rack = hamilton_96_tiprack_300uL("tip_rack")
    self.deck.assign_child_resource(self.tip_rack, rails=1)

    self.plate = Cor_96_wellplate_360ul_Fb("plate")
    self.deck.assign_child_resource(self.plate, rails=10)

    self.tip = HamiltonTip(
      name="test_tip",
      has_filter=False,
      total_tip_length=59.9,
      maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )

  def _get_commands(self, cmd_type):
    return [
      call.args[0] for call in self.mock_send.call_args_list if isinstance(call.args[0], cmd_type)
    ]

  # === Pick up tips tests ===

  async def test_pick_up_tips_single_channel(self):
    tip_spot = self.tip_rack.get_item("A1")
    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip_spot.get_tip())],
      use_channels=[0],
    )

    cmds = self._get_commands(PickupTips)
    self.assertEqual(len(cmds), 1)
    self.assertEqual(cmds[0].channels_involved, [1, 0, 0, 0, 0, 0, 0, 0])
    self.assertNotEqual(cmds[0].x_positions[0], 0)
    self.assertNotEqual(cmds[0].y_positions[0], 0)
    self.assertEqual(cmds[0].x_positions[1], 0)
    self.assertEqual(cmds[0].tip_types[0], NimbusTipType.STANDARD_300UL)

  async def test_pick_up_tips_multiple_channels(self):
    tip_spot_0 = self.tip_rack.get_item("A1")
    tip_spot_1 = self.tip_rack.get_item("B1")
    ops = [
      Pickup(resource=tip_spot_0, offset=Coordinate.zero(), tip=tip_spot_0.get_tip()),
      Pickup(resource=tip_spot_1, offset=Coordinate.zero(), tip=tip_spot_1.get_tip()),
    ]

    await self.backend.pick_up_tips(ops, use_channels=[0, 2])

    cmds = self._get_commands(PickupTips)
    self.assertEqual(cmds[0].channels_involved, [1, 0, 1, 0, 0, 0, 0, 0])

  async def test_pick_up_tips_traversal_height(self):
    tip_spot = self.tip_rack.get_item("A1")
    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip_spot.get_tip())],
      use_channels=[0],
      minimum_traverse_height_at_beginning_of_a_command=100.0,
    )

    cmds = self._get_commands(PickupTips)
    self.assertEqual(cmds[0].minimum_traverse_height_at_beginning_of_a_command, 10000)

  # === Drop tips tests ===

  async def test_drop_tips_to_waste_uses_roll(self):
    waste_pos = self.deck.get_resource("default_long_1")
    await self.backend.drop_tips(
      [Drop(resource=waste_pos, offset=Coordinate.zero(), tip=self.tip)], use_channels=[0]
    )

    cmds = self._get_commands(DropTipsRoll)
    self.assertEqual(len(cmds), 1)
    self.assertEqual(cmds[0].channels_involved, [1, 0, 0, 0, 0, 0, 0, 0])
    self.assertNotEqual(cmds[0].roll_distances[0], 0)

  async def test_drop_tips_to_rack_uses_drop_tips(self):
    tip_spot = self.tip_rack.get_item("A1")
    await self.backend.drop_tips(
      [Drop(resource=tip_spot, offset=Coordinate.zero(), tip=self.tip)], use_channels=[0]
    )

    cmds = self._get_commands(DropTips)
    self.assertEqual(len(cmds), 1)
    self.assertEqual(cmds[0].channels_involved, [1, 0, 0, 0, 0, 0, 0, 0])
    self.assertFalse(cmds[0].default_waste)

  async def test_drop_tips_multiple_channels_to_waste(self):
    ops = [
      Drop(
        resource=self.deck.get_resource("default_long_1"), offset=Coordinate.zero(), tip=self.tip
      ),
      Drop(
        resource=self.deck.get_resource("default_long_3"), offset=Coordinate.zero(), tip=self.tip
      ),
    ]
    await self.backend.drop_tips(ops, use_channels=[0, 2])

    cmds = self._get_commands(DropTipsRoll)
    self.assertEqual(cmds[0].channels_involved, [1, 0, 1, 0, 0, 0, 0, 0])

  # === Aspirate tests ===

  async def test_aspirate_single_channel(self):
    await self.backend.aspirate(
      [
        SingleChannelAspiration(
          resource=self.plate.get_item("A1"),
          offset=Coordinate.zero(),
          tip=self.tip,
          volume=100.0,
          flow_rate=50.0,
          liquid_height=5.0,
          blow_out_air_volume=40.0,
          mix=None,
        )
      ],
      use_channels=[0],
    )

    cmds = self._get_commands(Aspirate)
    self.assertEqual(len(cmds), 1)
    self.assertEqual(cmds[0].channels_involved, [1, 0, 0, 0, 0, 0, 0, 0])
    self.assertEqual(cmds[0].aspirate_volume[0], 1000)
    self.assertEqual(cmds[0].aspiration_speed[0], 500)
    self.assertEqual(cmds[0].blow_out_air_volume[0], 400)

  async def test_aspirate_default_flow_rate(self):
    """Test that flow_rate=None uses tip-based default (100 uL/s for 300ul tip)."""
    await self.backend.aspirate(
      [
        SingleChannelAspiration(
          resource=self.plate.get_item("A1"),
          offset=Coordinate.zero(),
          tip=self.tip,
          volume=100.0,
          flow_rate=None,
          liquid_height=5.0,
          blow_out_air_volume=None,
          mix=None,
        )
      ],
      use_channels=[0],
    )

    cmds = self._get_commands(Aspirate)
    self.assertEqual(cmds[0].aspiration_speed[0], 1000)  # 100 uL/s * 10

  async def test_aspirate_multiple_channels(self):
    ops = [
      SingleChannelAspiration(
        resource=self.plate.get_item("A1"),
        offset=Coordinate.zero(),
        tip=self.tip,
        volume=100.0,
        flow_rate=50.0,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      ),
      SingleChannelAspiration(
        resource=self.plate.get_item("A2"),
        offset=Coordinate.zero(),
        tip=self.tip,
        volume=150.0,
        flow_rate=75.0,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      ),
    ]
    await self.backend.aspirate(ops, use_channels=[1, 3])

    cmds = self._get_commands(Aspirate)
    self.assertEqual(cmds[0].channels_involved, [0, 1, 0, 1, 0, 0, 0, 0])
    self.assertEqual(cmds[0].aspirate_volume[1], 1000)
    self.assertEqual(cmds[0].aspirate_volume[3], 1500)
    self.assertEqual(cmds[0].aspiration_speed[1], 500)
    self.assertEqual(cmds[0].aspiration_speed[3], 750)

  async def test_aspirate_with_adc_enabled(self):
    await self.backend.aspirate(
      [
        SingleChannelAspiration(
          resource=self.plate.get_item("A1"),
          offset=Coordinate.zero(),
          tip=self.tip,
          volume=100.0,
          flow_rate=50.0,
          liquid_height=None,
          blow_out_air_volume=None,
          mix=None,
        )
      ],
      use_channels=[0],
      adc_enabled=True,
    )

    cmds = self._get_commands(EnableADC)
    self.assertEqual(len(cmds), 1)
    self.assertEqual(cmds[0].channels_involved, [1, 0, 0, 0, 0, 0, 0, 0])

  async def test_aspirate_with_adc_disabled(self):
    await self.backend.aspirate(
      [
        SingleChannelAspiration(
          resource=self.plate.get_item("A1"),
          offset=Coordinate.zero(),
          tip=self.tip,
          volume=100.0,
          flow_rate=50.0,
          liquid_height=None,
          blow_out_air_volume=None,
          mix=None,
        )
      ],
      use_channels=[0],
      adc_enabled=False,
    )

    cmds = self._get_commands(DisableADC)
    self.assertEqual(len(cmds), 1)

  # === Dispense tests ===

  async def test_dispense_single_channel(self):
    await self.backend.dispense(
      [
        SingleChannelDispense(
          resource=self.plate.get_item("A1"),
          offset=Coordinate.zero(),
          tip=self.tip,
          volume=100.0,
          flow_rate=50.0,
          liquid_height=5.0,
          blow_out_air_volume=40.0,
          mix=None,
        )
      ],
      use_channels=[0],
    )

    cmds = self._get_commands(Dispense)
    self.assertEqual(len(cmds), 1)
    self.assertEqual(cmds[0].channels_involved, [1, 0, 0, 0, 0, 0, 0, 0])
    self.assertEqual(cmds[0].dispense_volume[0], 1000)
    self.assertEqual(cmds[0].dispense_speed[0], 500)
    self.assertEqual(cmds[0].blow_out_air_volume[0], 400)

  async def test_dispense_multiple_channels(self):
    ops = [
      SingleChannelDispense(
        resource=self.plate.get_item("A1"),
        offset=Coordinate.zero(),
        tip=self.tip,
        volume=100.0,
        flow_rate=50.0,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      ),
      SingleChannelDispense(
        resource=self.plate.get_item("A2"),
        offset=Coordinate.zero(),
        tip=self.tip,
        volume=200.0,
        flow_rate=100.0,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      ),
    ]
    await self.backend.dispense(ops, use_channels=[2, 5])

    cmds = self._get_commands(Dispense)
    self.assertEqual(cmds[0].channels_involved, [0, 0, 1, 0, 0, 1, 0, 0])
    self.assertEqual(cmds[0].dispense_volume[2], 1000)
    self.assertEqual(cmds[0].dispense_volume[5], 2000)

  async def test_dispense_with_adc_enabled(self):
    await self.backend.dispense(
      [
        SingleChannelDispense(
          resource=self.plate.get_item("A1"),
          offset=Coordinate.zero(),
          tip=self.tip,
          volume=100.0,
          flow_rate=50.0,
          liquid_height=None,
          blow_out_air_volume=None,
          mix=None,
        )
      ],
      use_channels=[0],
      adc_enabled=True,
    )

    cmds = self._get_commands(EnableADC)
    self.assertEqual(len(cmds), 1)

  # === Coordinate conversion tests ===

  async def test_aspirate_coordinates(self):
    await self.backend.aspirate(
      [
        SingleChannelAspiration(
          resource=self.plate.get_item("A1"),
          offset=Coordinate.zero(),
          tip=self.tip,
          volume=100.0,
          flow_rate=50.0,
          liquid_height=None,
          blow_out_air_volume=None,
          mix=None,
        )
      ],
      use_channels=[0],
    )

    cmds = self._get_commands(Aspirate)
    self.assertEqual(cmds[0].x_positions[0], 8726)
    self.assertEqual(cmds[0].y_positions[0], -28972)

  async def test_offset_applied_to_coordinates(self):
    well = self.plate.get_item("A1")

    await self.backend.aspirate(
      [
        SingleChannelAspiration(
          resource=well,
          offset=Coordinate.zero(),
          tip=self.tip,
          volume=100.0,
          flow_rate=50.0,
          liquid_height=None,
          blow_out_air_volume=None,
          mix=None,
        )
      ],
      use_channels=[0],
    )
    x_no_offset = self._get_commands(Aspirate)[0].x_positions[0]

    self.mock_send.reset_mock()

    await self.backend.aspirate(
      [
        SingleChannelAspiration(
          resource=well,
          offset=Coordinate(x=10.0, y=0.0, z=0.0),
          tip=self.tip,
          volume=100.0,
          flow_rate=50.0,
          liquid_height=None,
          blow_out_air_volume=None,
          mix=None,
        )
      ],
      use_channels=[0],
    )
    x_with_offset = self._get_commands(Aspirate)[0].x_positions[0]

    self.assertEqual(x_with_offset - x_no_offset, 1000)


class TestNimbusTipPickupDropAllSizes(unittest.IsolatedAsyncioTestCase):
  """Tests for Nimbus tip pickup/drop Z positions across all tip sizes.

  These tests verify that the begin/end tip pickup and drop process values
  match the machine-validated values.
  """

  async def asyncSetUp(self):
    self.deck = NimbusDeck()
    self.backend = _setup_backend_with_deck(self.deck)
    self.mock_send = unittest.mock.AsyncMock(side_effect=_mock_send_command_response)
    self.backend.send_command = self.mock_send  # type: ignore[method-assign]

  def _get_commands(self, cmd_type):
    return [
      call.args[0] for call in self.mock_send.call_args_list if isinstance(call.args[0], cmd_type)
    ]

  async def test_10uL_tips(self):
    from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_10uL

    tip_rack = hamilton_96_tiprack_10uL("tips")
    self.deck.assign_child_resource(tip_rack, rails=1)
    tip_spot = tip_rack.get_item("A1")
    tip = tip_spot.get_tip()

    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    pickup_cmd = self._get_commands(PickupTips)[0]
    self.assertEqual(pickup_cmd.begin_tip_pick_up_process[0], 740)
    self.assertEqual(pickup_cmd.end_tip_pick_up_process[0], -60)

    self.mock_send.reset_mock()
    await self.backend.drop_tips(
      [Drop(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    drop_cmd = self._get_commands(DropTips)[0]
    self.assertEqual(drop_cmd.begin_tip_deposit_process[0], -1250)
    self.assertEqual(drop_cmd.end_tip_deposit_process[0], -2250)

    tip_rack.unassign()

  async def test_50uL_tips(self):
    from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_50uL

    tip_rack = hamilton_96_tiprack_50uL("tips")
    self.deck.assign_child_resource(tip_rack, rails=1)
    tip_spot = tip_rack.get_item("A1")
    tip = tip_spot.get_tip()

    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    pickup_cmd = self._get_commands(PickupTips)[0]
    self.assertEqual(pickup_cmd.begin_tip_pick_up_process[0], 990)
    self.assertEqual(pickup_cmd.end_tip_pick_up_process[0], 190)

    self.mock_send.reset_mock()
    await self.backend.drop_tips(
      [Drop(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    drop_cmd = self._get_commands(DropTips)[0]
    self.assertEqual(drop_cmd.begin_tip_deposit_process[0], -3050)
    self.assertEqual(drop_cmd.end_tip_deposit_process[0], -4050)

    tip_rack.unassign()

  async def test_300uL_tips(self):
    from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_300uL

    tip_rack = hamilton_96_tiprack_300uL("tips")
    self.deck.assign_child_resource(tip_rack, rails=1)
    tip_spot = tip_rack.get_item("A1")
    tip = tip_spot.get_tip()

    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    pickup_cmd = self._get_commands(PickupTips)[0]
    self.assertEqual(pickup_cmd.begin_tip_pick_up_process[0], 940)
    self.assertEqual(pickup_cmd.end_tip_pick_up_process[0], 140)

    self.mock_send.reset_mock()
    await self.backend.drop_tips(
      [Drop(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    drop_cmd = self._get_commands(DropTips)[0]
    self.assertEqual(drop_cmd.begin_tip_deposit_process[0], -4050)
    self.assertEqual(drop_cmd.end_tip_deposit_process[0], -5050)

    tip_rack.unassign()

  async def test_1000uL_tips(self):
    from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_1000uL

    tip_rack = hamilton_96_tiprack_1000uL("tips")
    self.deck.assign_child_resource(tip_rack, rails=1)
    tip_spot = tip_rack.get_item("A1")
    tip = tip_spot.get_tip()

    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    pickup_cmd = self._get_commands(PickupTips)[0]
    self.assertEqual(pickup_cmd.begin_tip_pick_up_process[0], 1160)
    self.assertEqual(pickup_cmd.end_tip_pick_up_process[0], 360)

    self.mock_send.reset_mock()
    await self.backend.drop_tips(
      [Drop(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    drop_cmd = self._get_commands(DropTips)[0]
    self.assertEqual(drop_cmd.begin_tip_deposit_process[0], -7350)
    self.assertEqual(drop_cmd.end_tip_deposit_process[0], -8350)

    tip_rack.unassign()


if __name__ == "__main__":
  unittest.main()
