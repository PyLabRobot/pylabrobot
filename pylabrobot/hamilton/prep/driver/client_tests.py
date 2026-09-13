import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from pylabrobot.hamilton.prep import PrepDriver
from pylabrobot.hamilton.prep import PrepChatterboxClient
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.features.pipettes import Pipettes
from pylabrobot.hamilton.prep.driver.client import PrepClient
from pylabrobot.hamilton.prep.driver.configuration import read_configuration, to_jsonable
from pylabrobot.hamilton.prep.driver.features.core_grippers import CoreGrippers, CoreGripperArm
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.protocol import Hoi2Action
from pylabrobot.resources.hamilton import STARLetDeck


@pytest.mark.parametrize("command_id,interface_id", [(9, 3), (8, 3), (2, 2), (5, 3)])
def test_firmware_string_queries_send_status_requests(command_id, interface_id):
  """Identity queries send the requested method and decode its string response."""

  async def _run() -> None:
    client = PrepClient(host="127.0.0.1")
    address = Address(1, 1, 0x100)
    payload = b"\x0f\x00\x08\x00PREP123\x00"
    client.execute = AsyncMock(return_value=payload)  # type: ignore[method-assign]

    result = await client._query_firmware_string(address, command_id, interface_id)

    assert result == "PREP123"
    client.execute.assert_awaited_once()
    command = client.execute.call_args.args[0]
    assert command.dest == address
    assert command.command_id == command_id
    assert command.interface_id == interface_id
    assert command.action_code == Hoi2Action.STATUS_REQUEST

  asyncio.run(_run())


def test_chatterbox_sets_resolved_interfaces_and_channels():
  async def _run() -> None:
    deck = STARLetDeck()
    p = PrepDriver(deck=deck, chatterbox=True)
    await p.setup()

    assert isinstance(p.client.mlprep_address, Address)
    addr = await p.client.resolve_path("MLPrepRoot.PipettorRoot.Pipettor")
    assert isinstance(addr, Address)
    assert p.configuration.num_channels == 2
    assert p.pipettes is not None
    assert isinstance(p.pipettes, Pipettes)
    assert p.pipettes.num_channels == 2
    assert p.pipettes.setup_finished is True
    # Default setup: use_v1_aspirate_dispense=False → v2 probe passes (chatterbox stubs).
    assert p.pipettes._supports_v2_pipetting is True

    await p.stop()
    # Kept after the link closes, as the STAR driver keeps it, so a reading can still be saved.
    assert p.configuration is not None

  asyncio.run(_run())


def test_chatterbox_use_v1_skips_v2_probe():
  async def _run() -> None:
    deck = STARLetDeck()
    p = PrepDriver(deck=deck, chatterbox=True)
    await p.setup(use_v1_aspirate_dispense=True)
    assert p.pipettes is not None
    assert isinstance(p.pipettes, Pipettes)
    assert p.pipettes.setup_finished is True
    assert p.pipettes._supports_v2_pipetting is False

    await p.stop()

  asyncio.run(_run())


def test_prep_device_motion_method_and_power_commands():
  async def _run() -> None:
    deck = STARLetDeck()
    p = PrepDriver(deck=deck, chatterbox=True)
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
    p = PrepDriver(deck=deck, chatterbox=True)
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
    deck = STARLetDeck()
    p = PrepDriver(deck=deck, chatterbox=True)
    await p.setup()
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


def test_execute_surfaces_clear_error_for_unresolvable_path():
  async def _run() -> None:
    deck = STARLetDeck()
    p = PrepDriver(deck=deck, chatterbox=True)
    await p.setup()

    missing = "MLPrepRoot.MLPrep"
    orig_resolve = p.client.resolve_path

    async def _fake_resolve(path: str):
      if path == missing:
        raise KeyError(path)
      return await orig_resolve(path)

    p.client.resolve_path = _fake_resolve  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="firmware path"):
      await p.client.execute(PrepCmd.PrepPark())
    await p.stop()

  asyncio.run(_run())


def test_chatterbox_preregisters_diagnostic_paths():
  async def _run() -> None:
    d = PrepChatterboxClient()
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
    p = PrepDriver(deck=deck, chatterbox=True)
    p.request_initialization_status = AsyncMock(side_effect=AssertionError("should not be called"))  # type: ignore[method-assign]
    await p.setup(force_initialize=True)
    p.request_initialization_status.assert_not_called()
    await p.stop()

  asyncio.run(_run())


def test_saved_configuration_holds_channel_count_and_head8(tmp_path):
  """What a device reports it has fitted is saved, and a declaration of it drives the chatterbox."""

  async def _run() -> None:
    p = PrepDriver(deck=STARLetDeck(), chatterbox=True)
    await p.setup()
    path = str(tmp_path / "prep.json")
    p.save_configuration(path)
    await p.stop()
    saved = read_configuration(path)["device"]
    assert saved.num_channels == p.num_channels
    assert saved.head8_installed == p.head8_installed

    saved.num_channels, saved.head8_installed = 1, False
    declared = str(tmp_path / "declared.json")
    with open(declared, "w", encoding="utf-8") as f:
      json.dump({"device": to_jsonable(saved)}, f)
    q = PrepDriver(deck=STARLetDeck(), chatterbox=True, declared_configuration_json=declared)
    await q.setup()
    assert q.num_channels == 1
    assert q.head8_installed is False
    assert q.head8 is None
    await q.stop()

  asyncio.run(_run())
