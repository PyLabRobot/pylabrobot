from typing import Any

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.liquid_handling.backends.chatterbox import (
  LiquidHandlerChatterboxBackend,
)
from pylabrobot.legacy.tip_tracker import TipTracker
from pylabrobot.resources import Deck
from pylabrobot.testing.concurrency import AnyioTestBase


def configured_chatterbox(
  *,
  num_channels: int = 2,
) -> LiquidHandlerChatterboxBackend:
  backend = LiquidHandlerChatterboxBackend(num_channels=num_channels)
  backend.set_deck(Deck(size_x=1, size_y=1, size_z=1))
  backend.set_heads(
    head={channel: TipTracker(thing=f"Channel {channel}") for channel in range(num_channels)}
  )
  return backend


class FailingChatterbox(LiquidHandlerChatterboxBackend):
  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    await super()._enter_lifespan(stack, **backend_kwargs)
    raise RuntimeError("entry failed")


class CancellingChatterbox(LiquidHandlerChatterboxBackend):
  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    await super()._enter_lifespan(stack, **backend_kwargs)
    await anyio.sleep_forever()


class FailingStopChatterbox(LiquidHandlerChatterboxBackend):
  async def _print_stop_message(self) -> None:
    """Print this chatterbox's native lifespan cleanup message."""
    await super()._print_stop_message()
    raise RuntimeError("stop failed")


def configure_subclass(
  backend: LiquidHandlerChatterboxBackend,
) -> LiquidHandlerChatterboxBackend:
  backend.set_deck(Deck(size_x=1, size_y=1, size_z=1))
  backend.set_heads(
    head={
      channel: TipTracker(thing=f"Channel {channel}") for channel in range(backend.num_channels)
    }
  )
  return backend


class TestChatterboxLifecycle(AnyioTestBase):
  async def test_managed_methods_and_exact_normal_output(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    assert LiquidHandlerChatterboxBackend.setup is HasLifespan.setup
    assert LiquidHandlerChatterboxBackend.stop is HasLifespan.stop
    backend = configured_chatterbox()

    async with backend:
      assert capsys.readouterr().out == "Setting up the liquid handler.\n"
    assert capsys.readouterr().out == "Stopping the liquid handler.\n"
    assert not backend.is_active

  async def test_failed_subclass_entry_runs_registered_cleanup(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = configure_subclass(FailingChatterbox(num_channels=2))

    with pytest.raises(RuntimeError, match="entry failed"):
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert capsys.readouterr().out == (
      "Setting up the liquid handler.\nStopping the liquid handler.\n"
    )
    assert not backend.is_active

  async def test_cancellation_during_subclass_entry_runs_registered_cleanup(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = configure_subclass(CancellingChatterbox(num_channels=2))
    entry_started = anyio.Event()

    async def run_backend() -> None:
      entry_started.set()
      async with backend:
        pytest.fail("cancelled entry must not reach the body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await entry_started.wait()
      task_group.cancel_scope.cancel()

    assert capsys.readouterr().out == (
      "Setting up the liquid handler.\nStopping the liquid handler.\n"
    )
    assert not backend.is_active

  async def test_body_cancellation_runs_registered_cleanup(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = configured_chatterbox()
    body_started = anyio.Event()

    async def run_backend() -> None:
      async with backend:
        body_started.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert capsys.readouterr().out == (
      "Setting up the liquid handler.\nStopping the liquid handler.\n"
    )
    assert not backend.is_active

  async def test_sequential_reuse_runs_cleanup_once_per_scope(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = configured_chatterbox()

    async with backend:
      pass
    assert not backend.is_active
    async with backend:
      pass

    assert capsys.readouterr().out == (
      "Setting up the liquid handler.\n"
      "Stopping the liquid handler.\n"
      "Setting up the liquid handler.\n"
      "Stopping the liquid handler.\n"
    )
    assert not backend.is_active

  async def test_lifespan_options_are_rejected_without_side_effects(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = configured_chatterbox()

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options must not reach the body")

    assert capsys.readouterr().out == ""
    assert not backend.is_active

  @pytest.mark.parametrize(
    ("missing", "message"),
    [("deck", "Deck not set"), ("head", "Head not set")],
  )
  async def test_missing_context_is_rejected_without_side_effects(
    self,
    missing: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
  ) -> None:
    backend = LiquidHandlerChatterboxBackend(num_channels=2)
    if missing == "head":
      backend.set_deck(Deck(size_x=1, size_y=1, size_z=1))

    with pytest.raises(RuntimeError, match=message):
      async with backend:
        pytest.fail("missing context must not reach the body")

    assert capsys.readouterr().out == ""
    assert not backend.is_active

  async def test_stop_failure_propagates_and_leaves_backend_inactive(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = configure_subclass(FailingStopChatterbox(num_channels=2))

    with pytest.raises(RuntimeError, match="stop failed"):
      async with backend:
        pass

    assert capsys.readouterr().out == (
      "Setting up the liquid handler.\nStopping the liquid handler.\n"
    )
    assert not backend.is_active
