"""Tests for OdysseyClassic — chatterbox-driven, no instrument required."""

import unittest

from pylabrobot.device_card import DeviceCard, HasDeviceCard
from pylabrobot.li_cor.odyssey import (
  ODYSSEY_CLASSIC_BASE,
  OdysseyClassic,
  OdysseyScanError,
  OdysseyScanningParams,
  StopResult,
)


class TestOdysseyDeviceCard(unittest.IsolatedAsyncioTestCase):
  """DeviceCard merging is exercised at construction, before any I/O."""

  def test_default_card_is_model_base(self):
    odyssey = OdysseyClassic(chatterbox=True)
    self.assertEqual(odyssey.card.name, "Odyssey Classic")
    self.assertEqual(odyssey.card.vendor, "LI-COR Biosciences")
    self.assertEqual(odyssey.card.model, "9120")
    self.assertEqual(odyssey.card.identity, {})
    self.assertTrue(odyssey.card.has("scanning"))
    self.assertTrue(odyssey.card.has("image_retrieval"))
    self.assertTrue(odyssey.card.has("instrument_status"))

  def test_instance_card_merges_identity(self):
    instance = DeviceCard.instance(identity={
      "pid": "http://hdl.handle.net/21.11157/test",
      "landing_page": "https://example.org/test",
      "name": "Test Lab Odyssey",
    })
    odyssey = OdysseyClassic(chatterbox=True, card=instance)
    self.assertEqual(odyssey.card.identity["pid"],
                     "http://hdl.handle.net/21.11157/test")
    self.assertEqual(odyssey.card.identity["name"], "Test Lab Odyssey")
    # model-base preserved
    self.assertEqual(odyssey.card.vendor, "LI-COR Biosciences")
    self.assertTrue(odyssey.card.has("scanning"))

  def test_instance_card_can_override_capability_specs(self):
    custom = DeviceCard.instance(capabilities={
      "scanning": {"resolutions_um": [42, 84]},  # subset
    })
    odyssey = OdysseyClassic(chatterbox=True, card=custom)
    self.assertEqual(
      odyssey.card.get("scanning", "resolutions_um"), [42, 84],
    )
    # other scanning specs from the base survive
    self.assertEqual(
      odyssey.card.get("scanning", "scan_area_cm"), [25, 25],
    )

  def test_implements_has_device_card_mixin(self):
    odyssey = OdysseyClassic(chatterbox=True)
    self.assertIsInstance(odyssey, HasDeviceCard)


class TestOdysseyLifecycle(unittest.IsolatedAsyncioTestCase):
  async def test_setup_finished_transitions(self):
    odyssey = OdysseyClassic(chatterbox=True)
    self.assertFalse(odyssey.setup_finished)
    self.assertFalse(odyssey.scanning.setup_finished)

    await odyssey.setup()
    self.assertTrue(odyssey.setup_finished)
    self.assertTrue(odyssey.scanning.setup_finished)
    self.assertTrue(odyssey.images.setup_finished)
    self.assertTrue(odyssey.status.setup_finished)

    await odyssey.stop()
    self.assertFalse(odyssey.setup_finished)
    self.assertFalse(odyssey.scanning.setup_finished)

  async def test_capability_methods_blocked_pre_setup(self):
    odyssey = OdysseyClassic(chatterbox=True)
    with self.assertRaises(RuntimeError):
      await odyssey.status.read_status()
    with self.assertRaises(RuntimeError):
      await odyssey.scanning.start()
    with self.assertRaises(RuntimeError):
      await odyssey.images.list_groups()


class TestOdysseyScanFlow(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.odyssey = OdysseyClassic(chatterbox=True)
    await self.odyssey.setup()

  async def asyncTearDown(self):
    await self.odyssey.stop()

  async def test_scan_helper_runs_to_completion(self):
    final = await self.odyssey.scan(
      backend_params=OdysseyScanningParams(name="demo", group="odyssey"),
    )
    self.assertEqual(final.state, "Completed")
    self.assertEqual(final.progress, 100.0)

  async def test_download_after_scan(self):
    await self.odyssey.scan(
      backend_params=OdysseyScanningParams(name="dl_demo", group="odyssey"),
    )
    scans = await self.odyssey.images.list_scans("odyssey")
    self.assertIn("dl_demo", scans)
    data = await self.odyssey.images.download("odyssey", "dl_demo")
    self.assertGreater(len(data), 0)

  async def test_start_without_configure_raises(self):
    with self.assertRaises(RuntimeError):
      await self.odyssey.scanning.start()

  async def test_default_group_seeded_in_chatterbox(self):
    groups = await self.odyssey.images.list_groups()
    self.assertIn("odyssey", groups)


class TestOdysseyStopAndSave(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.odyssey = OdysseyClassic(chatterbox=True)
    await self.odyssey.setup()

  async def asyncTearDown(self):
    await self.odyssey.stop()

  async def test_stop_and_save_without_configure_returns_empty(self):
    result = await self.odyssey.stop_and_save()
    self.assertIsInstance(result, StopResult)
    self.assertEqual(result.state, "Stopped")
    self.assertFalse(result.partial)
    self.assertEqual(result.channels_available, [])

  async def test_stop_and_save_after_scan_lists_channels(self):
    # Run a full scan, then stop_and_save — both channels recorded.
    await self.odyssey.scan(
      backend_params=OdysseyScanningParams(name="full", group="odyssey"),
    )
    result = await self.odyssey.stop_and_save()
    self.assertEqual(result.state, "Stopped")
    # The chatterbox returns the same blob for any channel,
    # so both 700 and 800 register as available.
    self.assertEqual(sorted(result.channels_available), [700, 800])


if __name__ == "__main__":
  unittest.main()
