import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from pylabrobot.hamilton.liquid_handlers.nimbus.chatterbox import NimbusChatterboxDriver
from pylabrobot.hamilton.liquid_handlers.nimbus.commands import GetChannelConfiguration_1
from pylabrobot.hamilton.liquid_handlers.nimbus.driver import (
  NimbusDriver,
  NimbusResolvedInterfaces,
  nimbus_interface_specs_for_root,
)
from pylabrobot.hamilton.tcp.interface_bundle import InterfacePathSpec, resolve_interface_path_specs
from pylabrobot.hamilton.tcp.error_tables import NIMBUS_ERROR_CODES
from pylabrobot.hamilton.tcp.packets import Address

# Stable key from NIMBUS_ERROR_CODES for merge-override tests (must exist in table).
_NIMBUS_OVERRIDE_KEY = (0x0001, 0x0001, 0x0101, 1, 0x0F01)
_NIMBUS_OTHER_KEY = (0x0001, 0x0001, 0x0101, 1, 0x0F02)


def test_chatterbox_setup_and_command_roundtrip():
  async def _run() -> None:
    driver = NimbusChatterboxDriver(num_channels=8)
    await driver.setup()

    assert driver.nimbus_core_address == Address(1, 1, 48896)
    assert driver.door is not None

    response = await driver.send_command(
      GetChannelConfiguration_1(driver.nimbus_core_address),
      ensure_connection=False,
      return_raw=False,
      raise_on_error=False,
      read_timeout=0.1,
    )
    assert response == {"channels": 8}

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


def test_nimbus_driver_error_codes_user_values_override_table():
  """NimbusDriver merges NIMBUS_ERROR_CODES with caller dict; same-key entries use the caller.

  Covers the __init__ merge policy used for instrument-specific error enrichment, not
  exercised elsewhere (tcp_tests do not assert Nimbus defaults).
  """
  driver = NimbusDriver(
    host="127.0.0.1",
    error_codes={_NIMBUS_OVERRIDE_KEY: "custom text for tests"},
  )
  assert driver._error_codes[_NIMBUS_OVERRIDE_KEY] == "custom text for tests"
  assert driver._error_codes[_NIMBUS_OTHER_KEY] == NIMBUS_ERROR_CODES[_NIMBUS_OTHER_KEY]


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
