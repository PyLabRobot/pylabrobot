from typing import Any

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.centrifuge.chatterbox import (
  CentrifugeChatterboxBackend,
  LoaderChatterboxBackend,
)
from pylabrobot.testing.concurrency import AnyioTestBase


class FailingCentrifugeChatterbox(CentrifugeChatterboxBackend):
  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    await super()._enter_lifespan(stack, **backend_kwargs)
    raise RuntimeError("entry failed")


class CancellingCentrifugeChatterbox(CentrifugeChatterboxBackend):
  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    await super()._enter_lifespan(stack, **backend_kwargs)
    await anyio.sleep_forever()


class FailingStopCentrifugeChatterbox(CentrifugeChatterboxBackend):
  async def _print_stop_message(self) -> None:
    """Print this chatterbox's native lifespan cleanup message."""
    await super()._print_stop_message()
    raise RuntimeError("stop failed")


class TestChatterboxLifespans(AnyioTestBase):
  async def test_classes_use_managed_lifecycle_methods(self) -> None:
    assert CentrifugeChatterboxBackend.setup is HasLifespan.setup
    assert CentrifugeChatterboxBackend.stop is HasLifespan.stop
    assert LoaderChatterboxBackend.setup is HasLifespan.setup
    assert LoaderChatterboxBackend.stop is HasLifespan.stop

  @pytest.mark.parametrize("backend_type", [CentrifugeChatterboxBackend, LoaderChatterboxBackend])
  async def test_lifespan_options_are_rejected_without_side_effects(
    self,
    backend_type: type[CentrifugeChatterboxBackend] | type[LoaderChatterboxBackend],
    capsys: pytest.CaptureFixture[str],
  ) -> None:
    backend = backend_type()

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options must not reach the body")

    assert capsys.readouterr().out == ""
    assert not backend.is_active

  async def test_centrifuge_normal_exit(self, capsys: pytest.CaptureFixture[str]) -> None:
    async with CentrifugeChatterboxBackend():
      assert capsys.readouterr().out == "Setting up\n"
    assert capsys.readouterr().out == "Stopping\n"

  async def test_loader_normal_exit(self, capsys: pytest.CaptureFixture[str]) -> None:
    async with LoaderChatterboxBackend():
      assert capsys.readouterr().out == "Setting up\n"
    assert capsys.readouterr().out == "Stopping\n"

  async def test_registered_cleanup_rolls_back_failed_subclass_entry(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    with pytest.raises(RuntimeError, match="entry failed"):
      async with FailingCentrifugeChatterbox():
        pytest.fail("failed entry must not reach the body")
    assert capsys.readouterr().out == "Setting up\nStopping\n"

  @pytest.mark.parametrize("backend_type", [CentrifugeChatterboxBackend, LoaderChatterboxBackend])
  async def test_cancelled_exit_stops_backend(
    self,
    backend_type: type[CentrifugeChatterboxBackend] | type[LoaderChatterboxBackend],
    capsys: pytest.CaptureFixture[str],
  ) -> None:
    backend = backend_type()
    body_started = anyio.Event()

    async def run_backend() -> None:
      async with backend:
        body_started.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert capsys.readouterr().out == "Setting up\nStopping\n"
    assert not backend.is_active

  async def test_cancelled_entry_rolls_back(self, capsys: pytest.CaptureFixture[str]) -> None:
    backend = CancellingCentrifugeChatterbox()

    async def run_backend() -> None:
      async with backend:
        pytest.fail("cancelled entry must not reach the body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await anyio.wait_all_tasks_blocked()
      task_group.cancel_scope.cancel()

    assert capsys.readouterr().out == "Setting up\nStopping\n"
    assert not backend.is_active

  async def test_sequential_reuse_runs_cleanup_once_per_scope(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = CentrifugeChatterboxBackend()
    async with backend:
      pass
    async with backend:
      pass
    assert capsys.readouterr().out == "Setting up\nStopping\nSetting up\nStopping\n"

  async def test_stop_failure_propagates_and_leaves_backend_inactive(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = FailingStopCentrifugeChatterbox()
    with pytest.raises(RuntimeError, match="stop failed"):
      async with backend:
        pass
    assert capsys.readouterr().out == "Setting up\nStopping\n"
    assert not backend.is_active
