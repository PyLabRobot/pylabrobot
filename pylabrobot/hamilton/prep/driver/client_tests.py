import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from pylabrobot.hamilton.prep import PrepDriver, PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.configuration import read_configuration, to_jsonable
from pylabrobot.hamilton.prep.driver.features.core_grippers import CoreGripperArm, CoreGrippers
from pylabrobot.hamilton.prep.driver.features.pipettes import Pipettes
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.protocol import Hoi2Action
from pylabrobot.resources.hamilton import PrepDeck, STARLetDeck


@pytest.mark.parametrize("command_id,interface_id", [(9, 3), (8, 3), (2, 2), (5, 3)])
def test_firmware_string_queries_send_status_requests(command_id, interface_id):
  """Identity queries send the requested method and decode its string response."""

  async def _run() -> None:
    driver = PrepDriver(deck=STARLetDeck(), host="127.0.0.1")
    address = Address(1, 1, 0x100)
    payload = b"\x0f\x00\x08\x00PREP123\x00"
    driver.send_command = AsyncMock(return_value=payload)  # type: ignore[method-assign]

    result = await driver.request_firmware_string(address, command_id, interface_id)

    assert result == "PREP123"
    driver.send_command.assert_awaited_once()
    command = driver.send_command.call_args.args[0]
    assert command.dest == address
    assert command.command_id == command_id
    assert command.interface_id == interface_id
    assert command.action_code == Hoi2Action.STATUS_REQUEST

  asyncio.run(_run())


def test_simulation_sets_resolved_interfaces_and_channels():
  async def _run() -> None:
    deck = STARLetDeck()
    p = PrepSimulationDriver(deck=deck)
    await p.setup()

    assert isinstance(p.mlprep_address, Address)
    addr = await p.resolve_path("MLPrepRoot.PipettorRoot.Pipettor")
    assert isinstance(addr, Address)
    assert p.configuration is not None
    assert p.configuration.num_channels == 2
    assert p.pipettes is not None
    assert isinstance(p.pipettes, Pipettes)
    assert p.pipettes.num_channels == 2
    assert p.pipettes.setup_finished is True
    # Default setup: use_v1_aspirate_dispense=False → v2 probe passes (V1.2.2 carries cmd 38-43).
    assert p.pipettes.configuration.supports_v2_pipetting is True

    await p.stop()
    # Kept after the link closes, as the STAR driver keeps it, so a reading can still be saved.
    assert p.configuration is not None

  asyncio.run(_run())


def test_simulation_use_v1_skips_v2_probe():
  async def _run() -> None:
    deck = STARLetDeck()
    p = PrepSimulationDriver(deck=deck)
    await p.setup(use_v1_aspirate_dispense=True)
    assert p.pipettes is not None
    assert isinstance(p.pipettes, Pipettes)
    assert p.pipettes.setup_finished is True
    assert p.pipettes.configuration.supports_v2_pipetting is False

    await p.stop()

  asyncio.run(_run())


def test_prep_device_motion_method_and_power_commands():
  async def _run() -> None:
    deck = STARLetDeck()
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    await p.park()
    await p.spread()
    assert p.method is not None
    await p.method.begin(automatic_pause=False)
    await p.method.end()
    await p.cancel_power_down()
    await p.stop()

  asyncio.run(_run())


def test_prep_method_run_context_manager_aborts_on_exception():
  async def _run() -> None:
    deck = STARLetDeck()
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.method is not None

    calls: list[str] = []
    orig_begin = p.method.begin
    orig_end = p.method.end
    orig_abort = p.method.abort

    async def rec_begin(automatic_pause: bool = False) -> None:
      calls.append("begin")
      await orig_begin(automatic_pause=automatic_pause)

    async def rec_end() -> None:
      calls.append("end")
      await orig_end()

    async def rec_abort() -> None:
      calls.append("abort")
      await orig_abort()

    p.method.begin = rec_begin  # type: ignore[method-assign]
    p.method.end = rec_end  # type: ignore[method-assign]
    p.method.abort = rec_abort  # type: ignore[method-assign]

    # Clean exit: begin + end, no abort.
    async with p.method.run():
      pass
    assert calls == ["begin", "end"]

    # Exception inside: begin + abort, re-raised.
    calls.clear()
    with pytest.raises(RuntimeError, match="boom"):
      async with p.method.run():
        raise RuntimeError("boom")
    assert calls == ["begin", "abort"]

    await p.stop()

  asyncio.run(_run())


def test_prep_device_wires_calibration_after_setup():
  async def _run() -> None:
    deck = PrepDeck(with_core_grippers=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.configuration is not None
    assert p.pipettes is not None
    assert p.num_channels == p.configuration.num_channels == 2
    assert p.head8_installed == p.configuration.head8_installed
    assert p.pipettes.num_channels == p.configuration.num_channels
    assert p.pipettes.head8_installed == p.configuration.head8_installed
    assert p.calibration is not None
    assert p.calibration.num_channels == p.configuration.num_channels
    assert p.calibration.head8_installed == p.configuration.head8_installed
    assert isinstance(p.core_grippers, CoreGrippers)
    async with p.mounted_core_grippers() as arm:
      assert isinstance(arm, CoreGripperArm)
      assert isinstance(arm.backend, CoreGrippers)
    await p.stop()

  asyncio.run(_run())


def test_send_command_surfaces_clear_error_for_unresolvable_path():
  async def _run() -> None:
    deck = STARLetDeck()
    p = PrepSimulationDriver(deck=deck)
    await p.setup()

    missing = "MLPrepRoot.MLPrep"
    orig_resolve = p.resolve_path

    async def _fake_resolve(path: str):
      if path == missing:
        raise KeyError(path)
      return await orig_resolve(path)

    p.resolve_path = _fake_resolve  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="firmware path"):
      await p.send_command(PrepCmd.PrepPark())
    await p.stop()

  asyncio.run(_run())


def test_simulation_resolves_diagnostic_paths():
  async def _run() -> None:
    d = PrepSimulationDriver(deck=STARLetDeck())
    await d.setup()
    assert isinstance(await d.resolve_path("MLPrepRoot.MLPrepCpu"), Address)
    assert isinstance(await d.resolve_path("MLPrepRoot.PipettorRoot.ModuleInformation"), Address)
    await d.stop()

  asyncio.run(_run())


def test_force_initialize_skips_is_initialized_check():
  """When force_initialize=True, PrepDriver.setup() never queries is_initialized."""
  from unittest.mock import AsyncMock

  async def _run() -> None:
    deck = STARLetDeck()
    p = PrepSimulationDriver(deck=deck)
    p.request_initialization_status = AsyncMock(side_effect=AssertionError("should not be called"))  # type: ignore[method-assign]
    await p.setup(force_initialize=True)
    p.request_initialization_status.assert_not_called()
    await p.stop()

  asyncio.run(_run())


def test_saved_configuration_holds_channel_count_and_head8(tmp_path):
  """What a device reports it has fitted is saved, and a declaration of it drives the simulator."""

  async def _run() -> None:
    p = PrepSimulationDriver(deck=STARLetDeck())
    await p.setup()
    path = str(tmp_path / "prep.json")
    p.save_configuration(path)
    assert p.pipettes is not None
    pipettes = p.pipettes.configuration
    await p.stop()
    read = read_configuration(path)
    assert read["pipettes"] == pipettes
    saved = read["device"]
    assert saved.num_channels == p.num_channels
    assert saved.head8_installed == p.head8_installed

    saved.num_channels, saved.head8_installed = 1, False
    one_channel = read["pipettes"]
    one_channel.channels = one_channel.channels[:1]
    declared = str(tmp_path / "declared.json")
    with open(declared, "w", encoding="utf-8") as f:
      json.dump({"device": to_jsonable(saved), "pipettes": to_jsonable(one_channel)}, f)
    q = PrepSimulationDriver(deck=STARLetDeck(), declared_configuration_json=declared)
    await q.setup()
    assert q.num_channels == 1
    assert q.head8_installed is False
    assert q.head8 is None
    await q.stop()

  asyncio.run(_run())


def test_setup_logs_one_summary_of_what_was_found(caplog):
  """Setup ends with one INFO block describing the device, as the STAR driver's does."""

  async def _run() -> None:
    p = PrepSimulationDriver(deck=STARLetDeck())
    assert p.format_setup_summary() == "[Hamilton Prep] not discovered yet"
    # The pylabrobot logger does not propagate to the root, so caplog listens on it directly.
    prep_logger = logging.getLogger("pylabrobot.hamilton.prep")
    prep_logger.addHandler(caplog.handler)
    try:
      with caplog.at_level(logging.DEBUG, logger="pylabrobot.hamilton.prep"):
        await p.setup()
    finally:
      prep_logger.removeHandler(caplog.handler)
    summary = p.format_setup_summary()
    assert summary.startswith("[Hamilton Prep] Connected on simulation (no link)")
    assert "  Pipettes: 2, v2 aspirate/dispense" in summary
    assert "    channel 0 (rear):" in summary
    assert "    channel 1 (front):" in summary
    assert "  8-channel head: none" in summary
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert infos == [summary]
    await p.stop()

  asyncio.run(_run())
