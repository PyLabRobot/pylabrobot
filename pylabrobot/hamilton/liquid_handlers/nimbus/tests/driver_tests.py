import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from pylabrobot.hamilton.liquid_handlers.nimbus.chatterbox import NimbusChatterboxDriver
from pylabrobot.hamilton.liquid_handlers.nimbus.commands import (
  ChannelConfiguration,
  NimbusCommand,
  Park,
  _UNRESOLVED,
)
from pylabrobot.hamilton.liquid_handlers.nimbus.driver import NimbusDriver
from pylabrobot.hamilton.tcp.packets import Address


def test_chatterbox_setup_and_command_roundtrip():
  async def _run() -> None:
    driver = NimbusChatterboxDriver(num_channels=8)
    await driver.setup()

    assert driver.nimbus_core_address == Address(1, 1, 48896)

    response = await driver.send_command(ChannelConfiguration())
    assert len(response.configurations) == 8
    assert all(c.channel_type == 1 for c in response.configurations)

    await driver.stop()

  asyncio.run(_run())


def test_chatterbox_jit_resolves_dest_after_send():
  async def _run() -> None:
    driver = NimbusChatterboxDriver(num_channels=8)
    await driver.setup()
    cmd = ChannelConfiguration()
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


def test_nimbus_core_address_raises_before_setup():
  """Property requires setup() to have discovered and stored NimbusCore."""
  driver = NimbusDriver(host="127.0.0.1")
  with pytest.raises(RuntimeError, match="Nimbus root address not discovered"):
    _ = driver.nimbus_core_address


def test_chatterbox_channel_configuration_returns_correct_types():
  async def _run() -> None:
    driver = NimbusChatterboxDriver(num_channels=4)
    await driver.setup()
    response = await driver.send_command(ChannelConfiguration())
    assert len(response.configurations) == 4
    for i, cfg in enumerate(response.configurations):
      assert cfg.channel_type == 1  # Channel300uL
      assert cfg.rail == i % 2  # alternating Left/Right
    await driver.stop()

  asyncio.run(_run())
