"""Opt-in live validation tests for Labcyte Echo 650 instruments.

These tests require real hardware and are skipped unless PYLABROBOT_ECHO_HOST is set.
They intentionally avoid plate motion unless PYLABROBOT_ECHO_VALIDATE_MOTION=1 is set.
"""

import os
import unittest

from pylabrobot.labcyte import Echo, EchoCommandError


ECHO_HOST = os.environ.get("PYLABROBOT_ECHO_HOST")
EXPECTED_MODEL = os.environ.get("PYLABROBOT_ECHO_EXPECTED_MODEL", "Echo 650")
VALIDATE_MOTION = os.environ.get("PYLABROBOT_ECHO_VALIDATE_MOTION") == "1"


@unittest.skipUnless(ECHO_HOST, "Set PYLABROBOT_ECHO_HOST to run Echo live validation.")
class TestEcho650LiveValidation(unittest.IsolatedAsyncioTestCase):
  """Live smoke tests for the Medman surface used by the Echo backend."""

  async def asyncSetUp(self) -> None:
    assert ECHO_HOST is not None
    self.echo = Echo(host=ECHO_HOST, timeout=10.0)
    await self.echo.setup()

  async def asyncTearDown(self) -> None:
    await self.echo.stop()

  async def test_identity_configuration_and_state(self) -> None:
    info = await self.echo.get_instrument_info()
    self.assertEqual(info.model, EXPECTED_MODEL)
    self.assertTrue(info.serial_number)

    config_xml = await self.echo.get_echo_configuration()
    self.assertIn("<", config_xml)

    state = await self.echo.get_access_state()
    self.assertIsInstance(state.raw, dict)

  async def test_plate_catalogs_and_protocol_catalog_are_readable(self) -> None:
    source_plate_types = await self.echo.get_all_source_plate_names()
    destination_plate_types = await self.echo.get_all_destination_plate_names()
    protocol_names = await self.echo.get_all_protocol_names()

    self.assertGreater(len(source_plate_types), 0)
    self.assertGreater(len(destination_plate_types), 0)
    self.assertIsInstance(protocol_names, list)

  async def test_event_channel_is_connectable(self) -> None:
    events = await self.echo.read_events(max_events=1, timeout=0.5)
    self.assertIsInstance(events, list)

  @unittest.skipUnless(
    VALIDATE_MOTION,
    "Set PYLABROBOT_ECHO_VALIDATE_MOTION=1 to run live door motion validation.",
  )
  async def test_lock_and_door_cycle(self) -> None:
    await self.echo.lock()
    try:
      await self.echo.open_door(timeout=10.0)
      opened = await self.echo.get_access_state()
      self.assertTrue(opened.door_open)

      await self.echo.close_door(timeout=10.0)
      closed = await self.echo.get_access_state()
      self.assertTrue(closed.door_closed)
    except EchoCommandError:
      raise
    finally:
      await self.echo.unlock()
