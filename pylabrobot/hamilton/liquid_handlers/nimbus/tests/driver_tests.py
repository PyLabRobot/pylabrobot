import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from pylabrobot.hamilton.liquid_handlers.nimbus.chatterbox import NimbusChatterboxDriver
from pylabrobot.hamilton.liquid_handlers.nimbus.commands import (
  GetChannelConfiguration_1,
  NimbusCommand,
  Park,
  _UNRESOLVED,
)
from pylabrobot.hamilton.liquid_handlers.nimbus.driver import (
  NimbusDriver,
  NimbusResolvedInterfaces,
  nimbus_interface_specs_for_root,
)
from pylabrobot.hamilton.tcp.interface_bundle import InterfacePathSpec, resolve_interface_path_specs
from pylabrobot.hamilton.tcp.packets import Address


def test_chatterbox_setup_and_command_roundtrip():
  async def _run() -> None:
    driver = NimbusChatterboxDriver(num_channels=8)
    await driver.setup()

    assert driver.nimbus_core_address == Address(1, 1, 48896)
    assert driver.door is not None

    response = await driver.send_command(
      GetChannelConfiguration_1(),
      read_timeout=0.1,
    )
    assert response.channels == 8

    await driver.stop()

  asyncio.run(_run())


def test_chatterbox_jit_resolves_dest_after_send():
  async def _run() -> None:
    driver = NimbusChatterboxDriver(num_channels=8)
    await driver.setup()
    cmd = GetChannelConfiguration_1()
    assert cmd.dest_address == _UNRESOLVED
    await driver.send_command(cmd)
    assert cmd.dest == driver.nimbus_core_address
    assert cmd.dest_address == driver.nimbus_core_address
    await driver.stop()

  asyncio.run(_run())


def test_send_command_surfaces_clear_error_for_unresolvable_nimbus_path():
  async def _run() -> None:
    driver = NimbusDriver(host="127.0.0.1")
    driver.resolve_path = AsyncMock(side_effect=KeyError("NimbusCORE"))  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="firmware path"):
      await driver.send_command(Park())

  asyncio.run(_run())


def test_chatterbox_allows_explicit_dest_override_when_firmware_path_none():
  @dataclass
  class _ExplicitDestCommand(NimbusCommand):
    command_id = 999
    firmware_path = None

  async def _run() -> None:
    driver = NimbusChatterboxDriver(num_channels=8)
    await driver.setup()
    cmd = _ExplicitDestCommand(dest=Address(1, 1, 48896))
    await driver.send_command(cmd)
    assert cmd.dest == Address(1, 1, 48896)
    assert cmd.dest_address == Address(1, 1, 48896)
    await driver.stop()

  asyncio.run(_run())


def test_assert_required_methods_missing_raises():
  async def _run() -> None:
    driver = NimbusDriver(host="127.0.0.1")

    class _Method:
      def __init__(self, method_id: int):
        self.method_id = method_id

    class _StubIntro:
      async def methods_for_interface(self, address, interface_id):  # noqa: ARG002
        return [_Method(3)]

    with patch.object(
      driver.introspection,
      "methods_for_interface",
      AsyncMock(return_value=[_Method(3)]),
    ):
      with pytest.raises(RuntimeError, match="missing required interface-1 methods"):
        await driver._assert_required_methods(
          Address(1, 1, 48896),
          object_name="NimbusCore",
          required_method_ids={3, 15},
        )

  asyncio.run(_run())


def test_nimbus_core_address_raises_before_setup():
  """Property requires setup() to have discovered and stored NimbusCore."""
  driver = NimbusDriver(host="127.0.0.1")
  with pytest.raises(RuntimeError, match="Nimbus root address not discovered"):
    _ = driver.nimbus_core_address


def test_nimbus_interface_specs_for_root_paths():
  """Root-relative dot-paths match firmware tree naming (e.g. NimbusCORE.Pipette)."""
  s = nimbus_interface_specs_for_root("NimbusCORE")
  assert s["nimbus_core"].path == "NimbusCORE"
  assert s["pipette"].path == "NimbusCORE.Pipette"
  assert s["door_lock"].path == "NimbusCORE.DoorLock"
  assert s["door_lock"].required is False


def test_nimbus_resolved_interfaces_from_map_optional_door():
  core = Address(1, 1, 100)
  pip = Address(1, 1, 200)
  r = NimbusResolvedInterfaces.from_resolution_map(
    {"nimbus_core": core, "pipette": pip, "door_lock": None}
  )
  assert r.nimbus_core == core
  assert r.pipette == pip
  assert r.door_lock is None


def test_resolve_interface_path_specs_required_missing_raises():
  async def _run() -> None:
    from unittest.mock import AsyncMock

    from pylabrobot.hamilton.tcp.client import HamiltonTCPClient

    tcp = HamiltonTCPClient(host="127.0.0.1", port=2000)
    tcp.resolve_path = AsyncMock(side_effect=KeyError)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="nimbus_core"):
      await resolve_interface_path_specs(
        tcp,
        {"nimbus_core": InterfacePathSpec("NimbusCORE", True)},
        instrument_label="Nimbus",
      )

  asyncio.run(_run())


def test_assert_required_methods_succeeds_when_all_present():
  """Complements test_assert_required_methods_missing_raises: no false positive when the set is satisfied."""

  async def _run() -> None:
    driver = NimbusDriver(host="127.0.0.1")

    class _Method:
      def __init__(self, method_id: int):
        self.method_id = method_id

    methods = [_Method(3), _Method(15)]
    with patch.object(
      driver.introspection,
      "methods_for_interface",
      AsyncMock(return_value=methods),
    ):
      await driver._assert_required_methods(
        Address(1, 1, 48896),
        object_name="NimbusCore",
        required_method_ids={3, 15},
      )

  asyncio.run(_run())
