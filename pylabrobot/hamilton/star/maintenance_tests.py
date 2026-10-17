"""Daily maintenance sequencing, pressure criteria, and motion failure boundaries."""

import asyncio
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, call, patch

from pylabrobot.hamilton.star.device import RECORDING_STAR
from pylabrobot.hamilton.star.driver.errors import check_fw_string_error
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.hamilton.star.maintenance import DailyMaintenance, PressureCheck
from pylabrobot.resources import Coordinate, STARDeck, TipRack
from pylabrobot.resources.tip_tracking import set_tip_tracking


class MaintenanceSimulationDriver(STARSimulationDriver):
  """Supply synthetic diagnostic readings while retaining real simulated motion and tracking."""

  def __init__(self, *args: Any, **kwargs: Any) -> None:
    """Set a conducting surface and stored channel parameters."""
    super().__init__(*args, **kwargs)
    self.surface = 100.0
    self.native_parameters: Dict[str, Dict[str, int]] = {}
    self.detected: Dict[str, int] = {}

  async def _send(
    self,
    module: str,
    command: str,
    auto_id: Any = True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    fmt: Any = None,
    **kwargs: Any,
  ) -> Any:
    """Supply diagnostic data absent from the general motion simulator."""
    pipettes = self.pipettes
    if pipettes is not None and module.startswith("P") and module != "PX":
      channel = pipettes.channel_from_module(module)
      if channel is not None:
        c = pipettes.configuration
        stored = self.native_parameters.setdefault(
          module,
          {
            "za": 31200,
            "zh": 9320,
            "zc": 31200,
            "zi": 0,
            "zj": 1,
            "zv": 12000,
            "zl": 321,
            "zr": 75,
            "zw": 3,
            "gt": 17,
            "gl": 23,
            "dp": 0,
            "dv": 4321,
            "dr": 137,
            "dw": 6,
          },
        )
        if command == "RA" and kwargs.get("ra") in stored:
          key = kwargs["ra"]
          return {key: stored[key]}
        if command in ("AA", "ZA", "ZL", "DS"):
          for key in stored:
            if key in kwargs:
              stored[key] = int(kwargs[key])
        if command == "RZ" and fmt is None:
          point = pipettes.get_reference_point_location(channel)
          if point is not None:
            counts = c.z_drive_mm_to_increments(point.z)
            return f"{module}RZid0001rz{counts:+06d} {counts:+06d}"
        if command == "RP":
          return f"{module}RPid0001rp+4000"
        if command == "RN":
          return {"rn": self.detected.get(module, 0)}
        if command in ("AL", "BP", "DS", "DI"):
          return f"{module}{command}id0001er00"
        if command == "ZL":
          tip = pipettes.get_mounted_tip(channel)
          overhang = 0 if tip is None else tip.get_size_z() - tip.fitting_depth
          end = int(kwargs["zh"]) * c.z_drive_mm_per_increment
          start = int(kwargs["zc"]) * c.z_drive_mm_per_increment
          stop = self.surface + overhang
          self.detected[module] = int(end < stop < start)
          if not self.detected[module]:
            pipettes.update_location_by_reference_point(channel, z=end)
            check_fw_string_error(f"{module}ZLid0001er70")
          retreat = int(kwargs.get("zi", 0)) * c.z_drive_mm_per_increment
          pipettes.update_location_by_reference_point(channel, z=stop + retreat)
          return f"{module}ZLid0001er00"
    return await super()._send(
      module,
      command,
      auto_id=auto_id,
      tip_pattern=tip_pattern,
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      fmt=fmt,
      **kwargs,
    )


class TestDailyMaintenance(unittest.IsolatedAsyncioTestCase):
  """Exercise real resource tracking with deterministic diagnostic replies and injected faults."""

  async def asyncSetUp(self) -> None:
    """Initialize a simulated STAR, keeping every native command available for assertions."""
    set_tip_tracking(True)
    deck = STARDeck()
    self.driver = MaintenanceSimulationDriver(deck=deck, declared_configuration_json=RECORDING_STAR)
    await self.driver.setup()
    rack = deck.get_resource(deck.get_component_name("teaching_tip_rack"))
    assert isinstance(rack, TipRack)
    self.rack = rack
    self.commands: List[Any] = []
    sleep_patch = patch("pylabrobot.hamilton.star.maintenance.sleep", new_callable=AsyncMock)
    self.mock_sleep = sleep_patch.start()
    self.addCleanup(sleep_patch.stop)
    self.original_send = self.driver.send_command
    self.driver.send_command = self.record_command  # type: ignore[method-assign,assignment]

  async def asyncTearDown(self) -> None:
    """Stop the simulated driver after each test."""
    await self.driver.stop()
    set_tip_tracking(False)

  async def record_command(self, module: str, command: str, **kwargs: Any) -> Any:
    """Record commands without bypassing the simulator's resource behavior."""
    self.commands.append((module, command, kwargs))
    return await self.original_send(module, command, **kwargs)

  def create_maintenance(
    self, channels: Optional[List[int]] = None, traverse_height: float = 245
  ) -> DailyMaintenance:
    """Use a single closed needle and a conducting surface at Z=100 mm."""
    maintenance = DailyMaintenance(
      self.driver,
      self.rack.get_all_items()[:1],
      Coordinate(696.2, 300, 100),
      channels=[0] if channels is None else channels,
      traverse_height=traverse_height,
    )
    self.report = maintenance.report
    return maintenance

  async def test_default_pad_and_explicit_override(self) -> None:
    """Omitting the pad targets the STAR rear pad; an explicit surface still takes precedence."""
    self.driver.surface = 220
    maintenance = DailyMaintenance(self.driver, [self.rack.get_item(0)], channels=[0])
    self.assertEqual(maintenance.verification_position, Coordinate(1338.9, 429.6, 220))
    self.assertEqual((await maintenance.run()).state, "passed")
    self.assertEqual(self.create_maintenance().verification_position, Coordinate(696.2, 300, 100))

  async def test_selected_channels_reuse_one_needle(self) -> None:
    """Only selected channels receive diagnostic commands, while all axes clear a taller traverse."""
    report = await self.create_maintenance([2, 3, 4], traverse_height=260).run()
    self.assertEqual(report.state, "passed")
    self.assertEqual({item.channel for item in report.pressure}, {2, 3, 4})
    self.assertEqual(len(report.pressure), 6)
    self.assertEqual(len(report.clld), 3)
    self.assertTrue(all(spot.has_tip() for spot in self.rack.get_all_items()))
    self.assertEqual(self.mock_sleep.await_args_list, [call(2), call(20)] * 6)
    for module, command, parameters in self.commands:
      if command in {"AA", "BP", "DS", "DI", "AL", "ZL"}:
        self.assertIn(module, {"P3", "P4", "P5"})
      if command in {"TP", "TR"}:
        self.assertEqual(sum(parameters["tm"]), 1)
        self.assertGreaterEqual(int(parameters["th"]), 2600)
    for channel, saved in report.original_parameters.items():
      assert self.driver.pipettes is not None
      self.assertEqual(await self.driver.pipettes.request_channel_parameters(channel), saved)

  async def test_vendor_strokes_and_search_parameters(self) -> None:
    """Pressure polarities, preload, return cycles, and cLLD bounds follow the daily method."""
    await self.create_maintenance().run()
    strokes = [p for _, cmd, p in self.commands if cmd == "DS"]
    self.assertEqual([(p["ds"], p["dt"]) for p in strokes], [("01250", "1"), ("00800", "0")])
    self.assertEqual([(p["dv"], p["dr"], p["dw"]) for p in strokes], [("04321", "137", "6")] * 2)
    preloads = [p["dp"] for _, cmd, p in self.commands if cmd == "AA" and p.keys() == {"dp"}]
    self.assertEqual(preloads, ["20000", "05760"])
    searches = [p for _, cmd, p in self.commands if cmd == "ZL"]
    self.assertEqual([p["zi"] for p in searches], ["0000", "0020"])
    self.assertEqual(searches[0]["zh"], searches[0]["zc"])
    self.assertEqual([p["zl"] for p in searches], ["00321", "00100"])
    self.assertEqual([(p["gt"], p["gl"]) for p in searches], [("0017", "0023")] * 2)
    pad_index = next(
      i for i, (_, cmd, p) in enumerate(self.commands) if cmd == "ZL" and p["zi"] == "0020"
    )
    self.assertEqual(self.commands[pad_index - 1], ("P1", "AA", {"zv": "04500"}))
    self.assertEqual(sum(cmd == "DI" for _, cmd, _ in self.commands), 2)
    self.assertEqual(sum(cmd == "TR" for _, cmd, _ in self.commands), 3)

  async def test_unreadable_piston_setting_prevents_pressure_motion(self) -> None:
    """A failed parameter read aborts before any pressure stroke, without guessing a default."""

    async def fault(module: str, command: str, **kwargs: Any) -> Any:
      """Fail the acceleration read after pickup."""
      if command == "RA" and kwargs.get("ra") == "dr":
        raise ValueError("Unreadable piston acceleration")
      return await self.record_command(module, command, **kwargs)

    self.driver.send_command = fault  # type: ignore[method-assign,assignment]
    with self.assertRaisesRegex(ValueError, "Unreadable piston acceleration"):
      await self.create_maintenance().run()
    self.assertEqual(self.report.state, "aborted")
    self.assertFalse(any(cmd == "DS" for _, cmd, _ in self.commands))
    self.assertTrue(any(cmd == "AA" and "za" in p for _, cmd, p in self.commands))

  async def test_invalid_selection_never_changes_parameters(self) -> None:
    """Invalid subsets fail before any motion or parameter writes."""
    for channels in ([], [0, 0], [1, 0], [8], [-1], [0.5], [True]):
      with self.subTest(channels=channels), self.assertRaisesRegex(ValueError, "Select distinct"):
        await self.create_maintenance(channels).run()  # type: ignore[arg-type]
    self.assertEqual(self.commands, [])

  async def test_low_hardware_counter_blocks_return(self) -> None:
    """An acknowledged retract cannot authorize XY if the hardware counter remains low."""

    async def fault(module: str, command: str, **kwargs: Any) -> Any:
      """Inject a controller/hardware disagreement when the first return begins."""
      if module == "P1" and command == "RZ" and kwargs.get("fmt") is None:
        if self.report.pressure:
          return "P1RZid0001rz+31200 +18000"
      return await self.record_command(module, command, **kwargs)

    self.driver.send_command = fault  # type: ignore[method-assign,assignment]
    with self.assertRaisesRegex(RuntimeError, "failed to reach traverse"):
      await self.create_maintenance().run()
    self.assertEqual(self.report.state, "aborted")
    self.assertFalse(any(cmd == "TR" for _, cmd, _ in self.commands))
    self.assertTrue(any(cmd == "AA" and "za" in p for _, cmd, p in self.commands))

  async def test_low_unselected_channel_blocks_pickup(self) -> None:
    """A channel outside the selected subset is never raised implicitly."""

    async def fault(module: str, command: str, **kwargs: Any) -> Any:
      """Report a low unselected hardware encoder while coarse readiness remains high."""
      if module == "P2" and command == "RZ" and kwargs.get("fmt") is None:
        return "P2RZid0001rz+31200 +18000"
      return await self.record_command(module, command, **kwargs)

    self.driver.send_command = fault  # type: ignore[method-assign,assignment]
    with self.assertRaisesRegex(RuntimeError, "Unselected channel"):
      await self.create_maintenance().run()
    self.assertFalse(any(cmd in {"TP", "TR", "ZA"} for _, cmd, _ in self.commands))

  async def test_cancellation_restores_without_return_motion(self) -> None:
    """Cancellation retains partial results and restores parameters without moving the tool."""

    async def cancel(module: str, command: str, **kwargs: Any) -> Any:
      """Stop after pickup at the first pressure stroke."""
      if command == "DS":
        raise asyncio.CancelledError()
      return await self.record_command(module, command, **kwargs)

    self.driver.send_command = cancel  # type: ignore[method-assign,assignment]
    with self.assertRaises(asyncio.CancelledError):
      await self.create_maintenance().run()
    self.assertEqual(self.report.state, "aborted")
    self.assertFalse(any(cmd == "TR" for _, cmd, _ in self.commands))
    assert self.driver.pipettes is not None
    self.assertEqual(
      await self.driver.pipettes.request_channel_parameters(0), self.report.original_parameters[0]
    )

  async def test_restore_readback_mismatch_aborts(self) -> None:
    """An acknowledged but ineffective restoration cannot produce a passing report."""

    async def fault(module: str, command: str, **kwargs: Any) -> Any:
      """Ignore the restoration write but acknowledge it like a faulty drive."""
      if command == "AA" and "za" in kwargs:
        return f"{module}AAid0001er00"
      return await self.record_command(module, command, **kwargs)

    self.driver.send_command = fault  # type: ignore[method-assign,assignment]
    with self.assertRaisesRegex(RuntimeError, "did not restore"):
      await self.create_maintenance().run()
    self.assertTrue(self.report.cleanup_errors)
    self.assertEqual(self.report.state, "aborted")

  async def test_air_detection_aborts_before_surface_search(self) -> None:
    """A false positive in air cannot trigger a subsequent downward search."""

    async def fault(module: str, command: str, **kwargs: Any) -> Any:
      """Override the detection latch only."""
      if command == "RN":
        return {"rn": 1}
      return await self.record_command(module, command, **kwargs)

    self.driver.send_command = fault  # type: ignore[method-assign,assignment]
    with self.assertRaisesRegex(RuntimeError, "detected in air"):
      await self.create_maintenance().run()
    self.assertEqual(sum(cmd == "ZL" for _, cmd, _ in self.commands), 1)
    self.assertEqual(sum(cmd == "TR" for _, cmd, _ in self.commands), 2)

  async def test_missing_surface_fails_measurement(self) -> None:
    """Expected no-detection errors produce a failed check, followed by a normal return."""
    self.driver.surface = 90
    report = await self.create_maintenance().run()
    self.assertEqual(report.state, "failed")
    self.assertFalse(report.clld[0].detected_on_pad)
    self.assertTrue(self.rack.get_item(0).has_tip())

  async def test_weak_pressure_limits_extra_strokes_and_fails(self) -> None:
    """Failure to build pressure cannot cause unlimited piston travel or pass the check."""

    async def weak(module: str, command: str, **kwargs: Any) -> Any:
      """Return insufficient pressure for both polarities."""
      if command == "RP":
        return f"{module}RPid0001rp+3000"
      return await self.record_command(module, command, **kwargs)

    self.driver.send_command = weak  # type: ignore[method-assign,assignment]
    report = await self.create_maintenance().run()
    self.assertEqual(report.state, "failed")
    strokes = [p for _, cmd, p in self.commands if cmd == "DS"]
    self.assertEqual(sum(p["ds"] == "00050" for p in strokes), 30)
    self.assertEqual(sum(p["ds"] == "00020" for p in strokes), 30)
    self.assertTrue(self.rack.get_item(0).has_tip())

  async def test_snapshot_failure_prevents_changes(self) -> None:
    """A failed parameter read cannot leave a drive changed without a saved value."""

    async def fault(module: str, command: str, **kwargs: Any) -> Any:
      """Lose the response during the initial snapshot."""
      if command == "RA" and kwargs.get("ra") == "zl":
        raise TimeoutError("lost parameter reply")
      return await self.record_command(module, command, **kwargs)

    self.driver.send_command = fault  # type: ignore[method-assign,assignment]
    with self.assertRaises(TimeoutError):
      await self.create_maintenance().run()
    self.assertFalse(
      any(cmd in {"AA", "TP", "TR", "DI", "DS", "ZL", "AL"} for _, cmd, _ in self.commands)
    )


class TestPressureCriteria(unittest.TestCase):
  """Check the vendor's inclusive pressure and loss thresholds."""

  def test_thresholds(self) -> None:
    """Both polarities use magnitude, allowing exactly 400 Pa loss from at least 3500 Pa."""
    for polarity in ("positive", "negative"):
      for start, end, passed in (
        (3500, 3100, True),
        (3499, 3499, False),
        (4000, 3599, False),
        (4000, 4200, True),
      ):
        with self.subTest(polarity=polarity, start=start, end=end):
          self.assertEqual(PressureCheck(0, polarity, start, end).passed, passed)
