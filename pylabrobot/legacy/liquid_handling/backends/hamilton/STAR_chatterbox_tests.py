# mypy: disable-error-code="attr-defined,method-assign"

import inspect
import unittest

from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.resources import (
  PLT_CAR_L5AC_A00,
  TIP_CAR_480_A00,
  cor_96_wellplate_360uL_Fb,
  set_tip_tracking,
)
from pylabrobot.resources.hamilton import STARLetDeck, hamilton_96_tiprack_300uL_filter

from .STAR_backend import STARBackend
from .STAR_chatterbox import STARChatterboxBackend, STARChatterboxState


class TestSTARChatterboxState(unittest.IsolatedAsyncioTestCase):
  """Per-instance STAR chatterbox query state. Unconfigured defaults match the query replies."""

  async def test_default_backend_keeps_unconfigured_query_values(self):
    backend = STARChatterboxBackend()

    self.assertEqual(await backend.request_z_pos_channel_n(0), 285.0)
    self.assertEqual(await backend.request_z_pos_channel_n(7), 285.0)
    self.assertTrue(await backend.request_iswap_initialization_status())
    self.assertEqual(await backend.channel_dispensing_drive_request_position(0), 0.0)
    self.assertEqual(await backend.channel_dispensing_drive_request_position(7), 0.0)

  async def test_custom_per_instance_query_values(self):
    z_positions = [100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0]
    drive_positions = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    backend = STARChatterboxBackend(
      chatterbox_state=STARChatterboxState(
        iswap_initialization_status=False,
        channel_z_positions=z_positions,
        dispensing_drive_positions=drive_positions,
      )
    )

    for channel, z in enumerate(z_positions):
      self.assertEqual(await backend.request_z_pos_channel_n(channel), z)
    self.assertFalse(await backend.request_iswap_initialization_status())
    for channel, vol in enumerate(drive_positions):
      self.assertEqual(await backend.channel_dispensing_drive_request_position(channel), vol)

  async def test_independent_backend_instances(self):
    shared = STARChatterboxState(channel_z_positions=[200.0] * 8)
    first = STARChatterboxBackend(chatterbox_state=shared)
    second = STARChatterboxBackend(chatterbox_state=shared)

    first.chatterbox_state = STARChatterboxState(
      iswap_initialization_status=False,
      channel_z_positions=[12.0] + [200.0] * 7,
      dispensing_drive_positions=[0.0, 9.5] + [0.0] * 6,
    )

    self.assertEqual(await first.request_z_pos_channel_n(0), 12.0)
    self.assertEqual(await second.request_z_pos_channel_n(0), 200.0)
    self.assertFalse(await first.request_iswap_initialization_status())
    self.assertTrue(await second.request_iswap_initialization_status())
    self.assertEqual(await first.channel_dispensing_drive_request_position(1), 9.5)
    self.assertEqual(await second.channel_dispensing_drive_request_position(1), 0.0)

  async def test_input_and_returned_collections_are_copied(self):
    z_positions = [285.0] * 8
    drive_positions = [0.0] * 8
    backend = STARChatterboxBackend(
      chatterbox_state=STARChatterboxState(
        channel_z_positions=z_positions,
        dispensing_drive_positions=drive_positions,
      )
    )

    z_positions[0] = 1.0
    drive_positions[0] = 99.0
    self.assertEqual(await backend.request_z_pos_channel_n(0), 285.0)
    self.assertEqual(await backend.channel_dispensing_drive_request_position(0), 0.0)

    with self.assertRaises(TypeError):
      backend.chatterbox_state.channel_z_positions[0] = 50.0  # type: ignore[index]
    with self.assertRaises(TypeError):
      backend.chatterbox_state.dispensing_drive_positions[0] = 7.0  # type: ignore[index]

    heights = await backend.request_pip_height_last_lld()
    heights[0] = 123.0
    self.assertEqual(await backend.request_pip_height_last_lld(), [0.0] * 8)

    y_spacings = await backend.channels_request_y_minimum_spacing()
    y_spacings[0] = 99.0
    self.assertNotEqual((await backend.channels_request_y_minimum_spacing())[0], 99.0)

  def test_rejects_wrong_channel_vector_length(self):
    with self.assertRaisesRegex(ValueError, "channel_z_positions"):
      STARChatterboxBackend(
        num_channels=8,
        chatterbox_state=STARChatterboxState(channel_z_positions=[285.0] * 4),
      )
    with self.assertRaisesRegex(ValueError, "dispensing_drive_positions"):
      STARChatterboxBackend(
        num_channels=8,
        chatterbox_state=STARChatterboxState(dispensing_drive_positions=[0.0] * 16),
      )

  async def test_rejects_out_of_range_channel_index(self):
    backend = STARChatterboxBackend(num_channels=8)
    with self.assertRaisesRegex(ValueError, "channel"):
      await backend.request_z_pos_channel_n(-1)
    with self.assertRaisesRegex(ValueError, "channel"):
      await backend.request_z_pos_channel_n(8)
    with self.assertRaisesRegex(ValueError, "channel_idx"):
      await backend.channel_dispensing_drive_request_position(-1)
    with self.assertRaisesRegex(ValueError, "channel_idx"):
      await backend.channel_dispensing_drive_request_position(8)

  async def test_setup_preserves_configured_query_state(self):
    backend = STARChatterboxBackend(
      chatterbox_state=STARChatterboxState(
        iswap_initialization_status=False,
        channel_z_positions=[90.0] * 8,
        dispensing_drive_positions=[3.0] * 8,
      )
    )
    backend.set_deck(STARLetDeck())
    await backend.setup()

    self.assertFalse(await backend.request_iswap_initialization_status())
    self.assertEqual(await backend.request_z_pos_channel_n(2), 90.0)
    self.assertEqual(await backend.channel_dispensing_drive_request_position(2), 3.0)

  async def test_replacing_state_is_visible_to_queries(self):
    backend = STARChatterboxBackend()
    backend.chatterbox_state = STARChatterboxState(
      iswap_initialization_status=False,
      channel_z_positions=[40.0] * 8,
      dispensing_drive_positions=[11.0] * 8,
    )

    self.assertFalse(await backend.request_iswap_initialization_status())
    self.assertEqual(await backend.request_z_pos_channel_n(0), 40.0)
    self.assertEqual(await backend.channel_dispensing_drive_request_position(0), 11.0)

  async def test_replacing_state_validates_channel_length(self):
    backend = STARChatterboxBackend(num_channels=8)
    with self.assertRaisesRegex(ValueError, "channel_z_positions"):
      backend.chatterbox_state = STARChatterboxState(channel_z_positions=[1.0])

  def test_rejects_non_state_replacement(self):
    backend = STARChatterboxBackend()
    with self.assertRaisesRegex(TypeError, "STARChatterboxState"):
      backend.chatterbox_state = object()  # type: ignore[assignment]

  async def test_replacing_state_refills_omitted_vectors(self):
    backend = STARChatterboxBackend(
      chatterbox_state=STARChatterboxState(
        channel_z_positions=[40.0] * 8,
        dispensing_drive_positions=[11.0] * 8,
      )
    )
    backend.chatterbox_state = STARChatterboxState(iswap_initialization_status=False)

    self.assertFalse(await backend.request_iswap_initialization_status())
    self.assertEqual(await backend.request_z_pos_channel_n(0), 285.0)
    self.assertEqual(await backend.channel_dispensing_drive_request_position(0), 0.0)

  async def test_explicit_zero_dispensing_override_is_not_omitted_argument(self):
    backend = STARChatterboxBackend(
      chatterbox_state=STARChatterboxState(dispensing_drive_positions=[5.0] * 8)
    )

    self.assertEqual(await backend.channel_dispensing_drive_request_position(0), 5.0)
    self.assertEqual(
      await backend.channel_dispensing_drive_request_position(0, simulated_value=0.0),
      0.0,
    )
    self.assertEqual(
      await backend.channel_dispensing_drive_request_position(0, simulated_value=12.5),
      12.5,
    )
    self.assertEqual(await backend.channel_dispensing_drive_request_position(0), 5.0)

  def test_hardware_backend_does_not_define_chatterbox_state(self):
    with self.assertRaises(AttributeError):
      STARBackend.chatterbox_state
    self.assertIsInstance(STARChatterboxBackend.chatterbox_state, property)


class TestSTARChatterboxStateDoesNotOwnTrackers(unittest.IsolatedAsyncioTestCase):
  """Tip queries stay on TipTracker; last-LLD stays latched by simulated sensing."""

  async def asyncSetUp(self):
    self.backend = STARChatterboxBackend(
      chatterbox_state=STARChatterboxState(
        iswap_initialization_status=False,
        channel_z_positions=[77.0] * 8,
      )
    )
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.backend, deck=self.deck)
    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[1] = self.tip_rack = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    self.deck.assign_child_resource(self.tip_car, rails=1)
    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = cor_96_wellplate_360uL_Fb(name="plate_01")
    self.deck.assign_child_resource(self.plt_car, rails=9)
    await self.lh.setup()

  async def asyncTearDown(self):
    await self.lh.stop()

  def test_state_object_has_no_tip_fields(self):
    params = inspect.signature(STARChatterboxState.__init__).parameters
    self.assertNotIn("tip_presence", params)
    self.assertNotIn("head96_tip_presence", params)
    self.assertNotIn("tip_length", params)
    self.assertNotIn("last_lld_heights", params)

  async def test_tip_presence_follows_tracker_not_chatterbox_state(self):
    self.assertEqual(await self.backend.request_tip_presence(), [False] * 8)

    await self.lh.pick_up_tips(self.tip_rack["A1"], use_channels=[0])
    presence = await self.backend.request_tip_presence()
    self.assertEqual(presence[0], True)
    self.assertEqual(presence[1:], [False] * 7)

    tip = self.lh.head[0].get_tip()
    self.assertEqual(await self.backend.request_tip_len_on_channel(0), tip.total_tip_length)

  async def test_head96_tip_presence_follows_tracker(self):
    set_tip_tracking(enabled=True)
    try:
      self.assertEqual(await self.backend.head96_request_tip_presence(), 0)
      await self.lh.pick_up_tips96(self.tip_rack)
      self.assertEqual(await self.backend.head96_request_tip_presence(), 1)
    finally:
      set_tip_tracking(enabled=False)

  async def test_last_lld_latching_still_works_with_configured_state(self):
    self.assertFalse(await self.backend.request_iswap_initialization_status())
    self.assertEqual(await self.backend.request_z_pos_channel_n(0), 77.0)

    channel = 3
    well = self.plate.get_item("D1")
    await self.lh.pick_up_tips(self.tip_rack["D1"], use_channels=[channel])
    well.tracker.set_volume(150.0)
    await self.backend.probe_liquid_heights(containers=[well], use_channels=[channel])

    expected = well.get_absolute_location(
      "c", "c", "cavity_bottom"
    ).z + well.compute_height_from_volume(150.0)
    heights = await self.backend.request_pip_height_last_lld()
    self.assertAlmostEqual(heights[channel], expected)
    self.assertEqual(
      [h for i, h in enumerate(heights) if i != channel],
      [0.0] * (self.backend.num_channels - 1),
    )
    leaked = heights
    leaked[channel] = 0.0
    self.assertAlmostEqual((await self.backend.request_pip_height_last_lld())[channel], expected)
