"""End-to-end tests driving the full ODTC stack against :class:`MockODTCServer`.

Unlike ``sila_interface_tests`` (which unit-tests event handlers with fakes),
these exercise the real path over a TCP socket:
``ODTC`` → ``Thermocycler``/``LoadingTray`` → ``ODTCThermocyclerBackend`` →
``ODTCDriver`` → SOAP-over-HTTP → ``MockODTCServer`` (+ async ResponseEvents).
"""

from __future__ import annotations

import unittest

from pylabrobot.capabilities.thermocycling.standard import Protocol, Stage, Step
from pylabrobot.inheco.odtc import ODTC
from pylabrobot.inheco.odtc.door import DoorStateUnknownError
from pylabrobot.inheco.odtc.mock_server import MockODTCServer
from pylabrobot.inheco.odtc.model import ODTCBackendParams, ODTCProtocol
from pylabrobot.inheco.sila import SiLAError, SiLAState


def _pcr_protocol(name: str = "PCR") -> Protocol:
  return Protocol(
    stages=[Stage(steps=[Step(95.0, 30.0), Step(60.0, 30.0), Step(72.0, 60.0)], repeats=3)],
    name=name,
  )


class ODTCMockServerTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.server = MockODTCServer().start()
    self.odtc = ODTC(
      odtc_ip="127.0.0.1",
      odtc_port=self.server.port,
      client_ip="127.0.0.1",
      name="odtc",
    )

  async def asyncTearDown(self) -> None:
    try:
      await self.odtc.stop()
    finally:
      self.server.stop()

  def _commands(self) -> list:
    return [c for c, _ in self.server.received_commands]

  # ---- setup lifecycle ----------------------------------------------------

  async def test_setup_runs_reset_then_initialize_to_idle(self):
    await self.odtc.setup()
    self.assertEqual(self.server.state, "idle")
    cmds = self._commands()
    self.assertIn("Reset", cmds)
    self.assertIn("Initialize", cmds)
    # Reset must be first and carry the event-receiver URI.
    self.assertEqual(cmds[0], "Reset")
    self.assertIsNotNone(self.server.event_receiver_uri)

  async def test_setup_simulation_mode_forwarded(self):
    await self.odtc.setup(simulation_mode=True)
    self.assertTrue(self.server.simulation_mode)

  # ---- door ---------------------------------------------------------------

  async def test_door_open_then_close_tracks_state(self):
    await self.odtc.setup()
    await self.odtc.door.open()
    self.assertTrue(self.server.door_open)
    self.assertTrue(self.odtc.door.backend.is_open)
    await self.odtc.door.close()
    self.assertFalse(self.server.door_open)
    self.assertFalse(self.odtc.door.backend.is_open)

  async def test_door_state_unknown_before_first_move(self):
    await self.odtc.setup()
    with self.assertRaises(DoorStateUnknownError):
      _ = self.odtc.door.backend.is_open

  # ---- temperatures -------------------------------------------------------

  async def test_request_block_and_lid_temperature(self):
    await self.odtc.setup()
    self.server.set_temperatures(Mount=95.0, Lid=105.0)
    self.assertAlmostEqual(await self.odtc.tc.request_block_temperature(), 95.0)
    self.assertAlmostEqual(await self.odtc.tc.request_lid_temperature(), 105.0)

  # ---- protocol execution -------------------------------------------------

  async def test_run_protocol_uploads_and_completes(self):
    await self.odtc.setup()
    await self.odtc.tc.run_protocol(_pcr_protocol(), volume_ul=20.0)
    await self.odtc.tc.wait_for_completion(timeout=5, report_interval=0)
    self.assertEqual(self.server.state, "idle")
    cmds = self._commands()
    self.assertIn("SetParameters", cmds)
    self.assertIn("ExecuteMethod", cmds)

  async def test_run_protocol_stores_method_xml_on_device(self):
    await self.odtc.setup()
    await self.odtc.tc.run_protocol(_pcr_protocol(), volume_ul=20.0)
    self.assertIsNotNone(self.server.methods_xml)
    self.assertIn("<Method", self.server.methods_xml)

  async def test_set_block_temperature_uploads_premethod(self):
    await self.odtc.setup()
    await self.odtc.tc.set_block_temperature(50.0)
    await self.odtc.tc.wait_for_completion(timeout=5, report_interval=0)
    self.assertIsNotNone(self.server.methods_xml)
    self.assertIn("<PreMethod", self.server.methods_xml)

  async def test_stop_protocol_sends_stop_method(self):
    await self.odtc.setup()
    await self.odtc.tc.run_protocol(_pcr_protocol(), volume_ul=20.0)
    await self.odtc.tc.stop_protocol()
    self.assertIn("StopMethod", self._commands())

  async def test_run_stored_protocol_roundtrips_through_device(self):
    """Upload a named method, then run it by name — exercises GetParameters +
    the full MethodSet XML round-trip (serialize → device → parse)."""
    await self.odtc.setup()
    odtc_p = ODTCProtocol.from_protocol(
      _pcr_protocol("Stored"), variant=96, params=ODTCBackendParams(name="Stored")
    )
    await self.odtc.tc.backend.upload_protocol(odtc_p)
    await self.odtc.tc.backend.run_stored_protocol("Stored")
    await self.odtc.tc.wait_for_completion(timeout=5, report_interval=0)
    self.assertEqual(self.server.state, "idle")
    self.assertIn("GetParameters", self._commands())

  async def test_request_status_returns_idle(self):
    await self.odtc.setup()
    self.assertEqual(await self.odtc.tc.backend.request_status(), SiLAState.IDLE)

  # ---- error handling -----------------------------------------------------

  async def test_execute_method_device_error_raises_sila_error(self):
    await self.odtc.setup()
    self.server.error_responses["ExecuteMethod"] = (9, "Invalid state")
    with self.assertRaises(SiLAError):
      await self.odtc.tc.run_protocol(_pcr_protocol(), volume_ul=20.0)


if __name__ == "__main__":
  unittest.main()
