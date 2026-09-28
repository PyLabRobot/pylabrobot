from typing import Any, Dict, Optional

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.liquid_handling.backends.serializing_backend import (
  SerializingBackend,
)
from pylabrobot.legacy.tip_tracker import TipTracker
from pylabrobot.resources import Deck
from pylabrobot.testing.concurrency import AnyioTestBase


class RecordingSerializingBackend(SerializingBackend):
  def __init__(self, events: list[str], fail_setup: bool = False, fail_stop: bool = False) -> None:
    super().__init__(num_channels=2)
    self.events = events
    self.fail_setup = fail_setup
    self.fail_stop = fail_stop

  async def send_command(
    self,
    command: str,
    data: Optional[Dict[str, Any]] = None,
  ) -> Optional[dict]:
    self.events.append(command)
    if command == "setup" and self.fail_setup:
      raise RuntimeError("setup failed")
    if command == "stop" and self.fail_stop:
      raise RuntimeError("stop failed")
    return None


class CheckpointingStopBackend(RecordingSerializingBackend):
  def __init__(self, events: list[str], stop_started: anyio.Event) -> None:
    super().__init__(events)
    self.stop_started = stop_started

  async def send_command(
    self,
    command: str,
    data: Optional[Dict[str, Any]] = None,
  ) -> Optional[dict]:
    if command != "stop":
      return await super().send_command(command, data)
    self.events.append("stop.started")
    self.stop_started.set()
    await anyio.sleep(0)
    self.events.append("stop.completed")
    return None


class FailingEntryBackend(RecordingSerializingBackend):
  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    await super()._enter_lifespan(stack, **backend_kwargs)
    raise RuntimeError("entry failed")


def configured_backend(
  events: list[str],
  *,
  fail_setup: bool = False,
  fail_stop: bool = False,
) -> RecordingSerializingBackend:
  backend = RecordingSerializingBackend(
    events,
    fail_setup=fail_setup,
    fail_stop=fail_stop,
  )
  backend.set_deck(Deck(size_x=1, size_y=1, size_z=1))
  backend.set_heads(head={channel: TipTracker(thing=f"Channel {channel}") for channel in range(2)})
  return backend


def configure_subclass(backend: RecordingSerializingBackend) -> RecordingSerializingBackend:
  backend.set_deck(Deck(size_x=1, size_y=1, size_z=1))
  backend.set_heads(head={channel: TipTracker(thing=f"Channel {channel}") for channel in range(2)})
  return backend


class TestSerializingBackendLifecycle(AnyioTestBase):
  async def test_managed_methods_and_exact_normal_commands(self) -> None:
    assert RecordingSerializingBackend.setup is HasLifespan.setup
    assert RecordingSerializingBackend.stop is HasLifespan.stop
    assert CheckpointingStopBackend.setup is HasLifespan.setup
    assert CheckpointingStopBackend.stop is HasLifespan.stop
    assert FailingEntryBackend.setup is HasLifespan.setup
    assert FailingEntryBackend.stop is HasLifespan.stop
    events: list[str] = []
    backend = configured_backend(events)

    async with backend:
      events.append("body")

    assert events == ["setup", "body", "stop"]
    assert not backend.is_active

  async def test_setup_failure_has_no_cleanup_to_run(self) -> None:
    events: list[str] = []
    backend = configured_backend(events, fail_setup=True)

    with pytest.raises(RuntimeError, match="setup failed"):
      async with backend:
        pytest.fail("failed setup must not reach the body")

    assert events == ["setup"]
    assert not backend.is_active

  async def test_failed_subclass_entry_runs_registered_cleanup(self) -> None:
    events: list[str] = []
    backend = configure_subclass(FailingEntryBackend(events))

    with pytest.raises(RuntimeError, match="entry failed"):
      async with backend:
        pytest.fail("failed entry must not reach the body")

    assert events == ["setup", "stop"]
    assert not backend.is_active

  async def test_body_cancellation_shields_stop_until_its_checkpoint_completes(self) -> None:
    events: list[str] = []
    body_started = anyio.Event()
    stop_started = anyio.Event()
    backend = configure_subclass(CheckpointingStopBackend(events, stop_started))

    async def run_backend() -> None:
      try:
        async with backend:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()
      await stop_started.wait()

    assert events == ["setup", "stop.started", "stop.completed", "cancelled"]
    assert not backend.is_active

  async def test_stop_failure_propagates_and_leaves_backend_inactive(self) -> None:
    events: list[str] = []
    backend = configured_backend(events, fail_stop=True)

    with pytest.raises(RuntimeError, match="stop failed"):
      async with backend:
        pass

    assert events == ["setup", "stop"]
    assert not backend.is_active

  async def test_sequential_reuse_runs_cleanup_once_per_scope(self) -> None:
    events: list[str] = []
    backend = configured_backend(events)

    async with backend:
      pass
    assert not backend.is_active
    async with backend:
      pass

    assert events == ["setup", "stop", "setup", "stop"]
    assert not backend.is_active

  @pytest.mark.parametrize(
    ("missing", "message"),
    [("deck", "Deck not set"), ("head", "Head not set")],
  )
  async def test_missing_context_is_rejected_without_commands(
    self,
    missing: str,
    message: str,
  ) -> None:
    events: list[str] = []
    backend = RecordingSerializingBackend(events)
    if missing == "head":
      backend.set_deck(Deck(size_x=1, size_y=1, size_z=1))

    with pytest.raises(RuntimeError, match=message):
      async with backend:
        pytest.fail("missing context must not reach the body")

    assert events == []
    assert not backend.is_active

  async def test_lifespan_options_are_rejected_without_commands(self) -> None:
    events: list[str] = []
    backend = configured_backend(events)

    with pytest.raises(
      TypeError,
      match="unexpected keyword argument",
    ):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options must not reach the body")

    assert events == []
    assert not backend.is_active
