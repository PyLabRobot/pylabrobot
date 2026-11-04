import gc
import weakref
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

import pylabrobot.conftest as collection_audit
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.machines.backend import MachineBackend


def test_machine_backend_uses_only_canonical_imports() -> None:
  package_root = Path(__file__).parents[2]
  forbidden = "legacy.machines.machine import " + "MachineBackend"
  offenders = sorted(
    str(path.relative_to(package_root.parent))
    for path in package_root.rglob("*.py")
    if forbidden in path.read_text(encoding="utf-8")
  )
  assert offenders == []


class RecordingBackend(MachineBackend):
  def __init__(self, value: str = "value", fail_setup: bool = False) -> None:
    super().__init__()
    self.value = value
    self.fail_setup = fail_setup
    self.events: list[str] = []
    self.options: dict[str, Any] = {}

  async def _record_stop(self) -> None:
    self.events.append("stop")

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    self.options = backend_kwargs
    self.events.append("setup-acquired")
    stack.push_shielded_async_callback(self._record_stop)
    if self.fail_setup:
      self.events.append("setup-failed")
      raise RuntimeError("setup failed")

  def serialize(self) -> dict:
    return {**super().serialize(), "value": self.value}


@pytest.mark.anyio
async def test_native_backend_lifespan_forwards_options_and_stops() -> None:
  backend = RecordingBackend()
  assert isinstance(backend, HasLifespan)
  async with backend.lifespan(port="mock"):
    assert backend.is_active
    assert backend.options == {"port": "mock"}
  assert backend.events == ["setup-acquired", "stop"]


@pytest.mark.anyio
async def test_native_backend_setup_failure_rolls_back() -> None:
  backend = RecordingBackend(fail_setup=True)
  with pytest.raises(RuntimeError, match="setup failed"):
    async with backend:
      pytest.fail("failed entry must not reach the body")
  assert backend.events == ["setup-acquired", "setup-failed", "stop"]
  assert not backend.is_active


def test_backend_serialization_shape_and_round_trip_are_preserved() -> None:
  backend = RecordingBackend(value="serialized")
  serialized = backend.serialize()
  assert serialized == {"type": "RecordingBackend", "value": "serialized"}
  restored = MachineBackend.deserialize(serialized.copy())
  assert isinstance(restored, RecordingBackend)
  assert restored.value == "serialized"


class NativeRecordingBackend(MachineBackend):
  def __init__(self) -> None:
    super().__init__()
    self.calls: list[str] = []

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    self.calls.append("native")


class RecursiveNativeRecordingBackend(NativeRecordingBackend):
  pass


def test_final_native_contract_audit_recurses_and_reports_zero() -> None:
  descendants = collection_audit._all_machine_backend_descendants()
  assert RecursiveNativeRecordingBackend in descendants
  assert collection_audit._final_native_contract_violations() == ([], [])


def test_machine_backend_inherits_managed_methods() -> None:
  assert MachineBackend.setup is HasLifespan.setup
  assert MachineBackend.stop is HasLifespan.stop


def test_native_backend_must_implement_a_lifespan_extension() -> None:
  with pytest.raises(TypeError, match="_enter_lifespan or lifespan"):

    class MissingEntryBackend(MachineBackend):
      pass


@pytest.mark.anyio
async def test_native_backend_can_implement_only_lifespan() -> None:
  """A custom lifespan satisfies the inherited HasLifespan contract."""
  events: list[str] = []

  class LifespanOnlyBackend(MachineBackend):
    """Override only the public context manager."""

    @asynccontextmanager
    async def lifespan(self, **kwargs: Any) -> AsyncIterator[HasLifespan]:
      """Own the backend's lexical activity without a separate entry hook."""
      events.append("enter")
      try:
        yield self
      finally:
        events.append("exit")

  async with LifespanOnlyBackend():
    events.append("body")
  assert events == ["enter", "body", "exit"]


@pytest.mark.anyio
async def test_machine_backend_abstract_hook_allows_cooperative_entry() -> None:
  """Abstractness enforces the override without preventing parent composition."""
  events: list[str] = []

  class ComposedBackend(MachineBackend):
    """Enter its abstract parent hook before registering cleanup."""

    async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
      """Compose the abstract parent and own this backend's resource."""
      await super()._enter_lifespan(stack)
      events.append("acquired")
      stack.callback(events.append, "released")

  async with ComposedBackend() as backend:
    assert backend.is_active
    events.append("body")

  assert events == ["acquired", "body", "released"]


def test_native_backend_resolves_managed_methods() -> None:
  assert NativeRecordingBackend.setup is HasLifespan.setup
  assert NativeRecordingBackend.stop is HasLifespan.stop


def test_native_machine_backend_cannot_define_setup() -> None:
  with pytest.raises(TypeError, match="setup"):

    class InvalidNativeBackend(MachineBackend):
      async def _enter_lifespan(
        self,
        stack: AsyncExitStackWithShielding,
        **backend_kwargs: Any,
      ) -> None:
        pass

      async def setup(self, **backend_kwargs: Any) -> None:
        pass


def test_recording_backend_inherits_managed_methods() -> None:
  assert RecordingBackend.setup is HasLifespan.setup
  assert RecordingBackend.stop is HasLifespan.stop


def test_backend_instances_use_a_weak_registry() -> None:
  backend = NativeRecordingBackend()
  reference = weakref.ref(backend)
  assert backend in MachineBackend.get_all_instances()
  del backend
  gc.collect()
  assert reference() is None
