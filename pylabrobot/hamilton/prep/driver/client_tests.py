import asyncio
import json
import logging
from typing import List
from unittest.mock import AsyncMock

import pytest

from pylabrobot.hamilton.prep import PrepDriver, PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.configuration import read_configuration, to_jsonable
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.protocol import Hoi2Action
from pylabrobot.resources.hamilton import PrepDeck, STARLetDeck


def _index_of(sent: list, command_type) -> "int | None":
  """Where the first command of `command_type` is in `sent`, or None."""
  return next((i for i, c in enumerate(sent) if isinstance(c, command_type)), None)


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


def test_simulation_setup_detects_v2_pipetting_and_keeps_the_configuration_after_stop():
  """V1.2.2 carries the v2 aspirate/dispense commands, and the configuration outlives the link."""

  async def _run() -> None:
    p = PrepSimulationDriver(deck=STARLetDeck())
    await p.setup()
    assert p.pipettes is not None
    assert p.pipettes.configuration.supports_v2_pipetting is True
    await p.stop()
    assert p.configuration is not None and p.configuration.num_channels == 2

  asyncio.run(_run())


def test_simulation_use_v1_skips_v2_probe():
  async def _run() -> None:
    p = PrepSimulationDriver(deck=STARLetDeck())
    await p.setup(use_v1_aspirate_dispense=True)
    assert p.pipettes is not None
    assert p.pipettes.configuration.supports_v2_pipetting is False
    await p.stop()

  asyncio.run(_run())


def test_prep_device_motion_method_and_power_commands():
  async def _run() -> None:
    deck = STARLetDeck()
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    await p.park_device()
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


def test_mounted_picks_up_the_tools_and_returns_them():
  async def _run() -> None:
    p = PrepSimulationDriver(deck=PrepDeck(with_core_grippers=True))
    await p.setup()
    assert p.core_grippers is not None
    assert p.core_grippers is not None
    async with p.core_grippers.mounted() as grippers:
      assert p.core_grippers_mounted
      assert grippers is p.core_grippers
    assert not p.core_grippers_mounted
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


class _Records(logging.Handler):
  """What a logger emits, collected whatever pytest's caplog does with it."""

  def __init__(self) -> None:
    super().__init__()
    self.records: List[logging.LogRecord] = []

  def emit(self, record: logging.LogRecord) -> None:
    self.records.append(record)


def test_setup_logs_one_summary_of_what_was_found(caplog):
  """Setup ends with one INFO block describing the device, as the STAR driver's does."""

  async def _run() -> None:
    p = PrepSimulationDriver(deck=STARLetDeck())
    assert p.format_setup_summary() == "[Hamilton Prep] not discovered yet"
    prep_logger = logging.getLogger("pylabrobot.hamilton.prep")
    records = _Records()
    prep_logger.addHandler(records)
    try:
      with caplog.at_level(logging.DEBUG, logger="pylabrobot.hamilton.prep"):
        await p.setup()
    finally:
      prep_logger.removeHandler(records)
    summary = p.format_setup_summary()
    assert summary.startswith("[Hamilton Prep] Connected on simulation (no link)")
    assert "  Pipettes: 2, v2 aspirate/dispense" in summary
    assert "    channel 0 (rear):" in summary
    assert "    channel 1 (front):" in summary
    assert "  8-channel head: none" in summary
    infos = [r.getMessage() for r in records.records if r.levelno == logging.INFO]
    assert infos == ["tips held at setup: none", summary]
    await p.stop()

  asyncio.run(_run())


def test_stop_raises_the_channels_to_z_safety_and_reads_them_back():
  """stop() sends MoveZUpToSafe for every channel, then finds nothing low, then closes the link."""

  async def _run() -> None:
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None and p.configuration is not None
    await p.pipettes.move_tool_bottom_to_z_positions({0: 120.0, 1: 130.0})
    assert len(await p.features_below_safe_z()) == 2
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    found: list = []
    check = p.features_below_safe_z

    async def recorded_check(*args, **kwargs):
      found.append(await check(*args, **kwargs))
      return found[-1]

    p.features_below_safe_z = recorded_check  # type: ignore[method-assign]
    await p.stop()
    up = [c for c in sent if isinstance(c, PrepCmd.PrepMoveZUpToSafe)]
    assert len(up) == 1 and len(up[0].channels) == 2
    assert found == [[]]
    assert p._setup_finished is False

  asyncio.run(_run())


def test_setup_reads_what_is_held_and_raises_the_channels():
  """A device left low or holding a tip is read and raised before anything moves laterally."""

  async def _run() -> None:
    p = PrepSimulationDriver(deck=PrepDeck())
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.setup()
    assert p.pipettes is not None
    tip_reads = _index_of(sent, PrepCmd.PrepProbeRequest)
    up = _index_of(sent, PrepCmd.PrepMoveZUpToSafe)
    assert tip_reads is not None and up is not None and tip_reads < up
    await p.stop()

  asyncio.run(_run())


def test_setup_raises_a_device_that_was_left_low():
  """A device left with a channel low is raised at setup, and nothing is low afterwards."""

  async def _run() -> None:
    deck = PrepDeck()
    first = PrepSimulationDriver(deck=deck)
    await first.setup()
    assert first.pipettes is not None
    await first.pipettes.move_tool_bottom_to_z_positions({0: 120.0})
    assert len(await first.features_below_safe_z()) == 1
    await first.stop(skip_raise_to_z_safety=True)

    second = PrepSimulationDriver(deck=deck)
    sent: list = []
    send = second.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await send(command, *args, **kwargs)

    second.send_command = record  # type: ignore[method-assign]
    await second.setup()
    assert _index_of(sent, PrepCmd.PrepMoveZUpToSafe) is not None
    assert await second.features_below_safe_z() == []
    await second.stop()

  asyncio.run(_run())


def test_stop_can_leave_the_channels_where_they_stand():
  """skip_raise_to_z_safety closes the link without raising anything, and says what is low."""

  async def _run() -> None:
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_tool_bottom_to_z_positions({0: 120.0})
    sent: list = []
    send = p.send_command

    async def record(command, *args, **kwargs):
      sent.append(command)
      return await send(command, *args, **kwargs)

    p.send_command = record  # type: ignore[method-assign]
    await p.stop(skip_raise_to_z_safety=True)
    assert not any(isinstance(c, PrepCmd.PrepMoveZUpToSafe) for c in sent)
    assert p._setup_finished is False

  asyncio.run(_run())


def test_stop_closes_the_link_when_the_channels_do_not_go_up():
  """A retract that fails is logged with what is still low, and the link closes anyway."""

  async def _run() -> None:
    p = PrepSimulationDriver(deck=PrepDeck())
    await p.setup()
    assert p.pipettes is not None
    await p.pipettes.move_tool_bottom_to_z_positions({0: 120.0})
    p.pipettes.move_to_safe_z = AsyncMock(side_effect=RuntimeError("stuck"))  # type: ignore[method-assign]
    records: list = []
    capture = logging.Handler(logging.WARNING)
    capture.emit = records.append  # type: ignore[method-assign,assignment]
    driver_logger = logging.getLogger("pylabrobot.hamilton.prep.driver.master")
    driver_logger.addHandler(capture)
    try:
      await p.stop()
    finally:
      driver_logger.removeHandler(capture)
    messages = [record.getMessage() for record in records]
    assert "could not move the channels to Z safety" in messages
    assert any(
      m.startswith("not everything is at Z safety: channel 0 at 120.0 mm") for m in messages
    )
    assert p._setup_finished is False

  asyncio.run(_run())
