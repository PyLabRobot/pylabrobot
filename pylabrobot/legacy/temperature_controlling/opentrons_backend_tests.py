"""Software-only tests for the Opentrons HTTP temperature module backend."""

import threading
from types import ModuleType
from typing import Any, Dict, List, Optional, Tuple, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.legacy.temperature_controlling import opentrons_backend as backend_module
from pylabrobot.legacy.temperature_controlling.opentrons_backend import (
  OpentronsTemperatureModuleBackend,
)
from pylabrobot.testing.concurrency import AnyioTestBase

Call = Tuple[str, Tuple[object, ...], Dict[str, object]]


class RecordingMapping(dict[str, Any]):
  """Record which thread reads an HTTP response mapping."""

  def __init__(
    self,
    values: Dict[str, Any],
    state_threads: List[Tuple[str, int]],
    name: str,
  ) -> None:
    super().__init__(values)
    self.state_threads = state_threads
    self.name = name

  def __getitem__(self, key: str) -> Any:
    self.state_threads.append((f"{self.name}.{key}", threading.get_ident()))
    return super().__getitem__(key)


class RecordingModules:
  """Typed synchronous stand-in for ``ot_api.modules``."""

  def __init__(self) -> None:
    self.calls: List[Call] = []
    self.call_threads: List[Tuple[str, int]] = []
    self.state_threads: List[Tuple[str, int]] = []
    self.failures: Dict[str, BaseException] = {}
    self.block_at: Optional[str] = None
    self.block_started = threading.Event()
    self.block_release = threading.Event()
    self.block_timed_out = False
    self.cancellation_requested = threading.Event()
    self.cleanup_cancel_states: List[bool] = []
    self.modules: List[Dict[str, Any]] = []

  def _record(self, name: str, args: Tuple[object, ...], kwargs: Dict[str, object]) -> None:
    self.calls.append((name, args, kwargs))
    self.call_threads.append((name, threading.get_ident()))
    if name == "temperature_module_deactivate":
      self.cleanup_cancel_states.append(self.cancellation_requested.is_set())
    if self.block_at == name:
      self.block_started.set()
      if not self.block_release.wait(timeout=1):
        self.block_timed_out = True
    failure = self.failures.get(name)
    if failure is not None:
      raise failure

  def temperature_module_set_temperature(self, *, celsius: float, module_id: str) -> object:
    self._record(
      "temperature_module_set_temperature",
      (),
      {"celsius": celsius, "module_id": module_id},
    )
    return object()

  def temperature_module_deactivate(self, *, module_id: str) -> object:
    self._record("temperature_module_deactivate", (), {"module_id": module_id})
    return object()

  def list_connected_modules(self) -> List[Dict[str, Any]]:
    self._record("list_connected_modules", (), {})
    return self.modules

  def module_response(self, module_id: str, temperature: float) -> Dict[str, Any]:
    """Build a complete response fragment with observable mapping reads."""
    data = RecordingMapping(
      {"currentTemperature": temperature},
      self.state_threads,
      "module.data",
    )
    return RecordingMapping(
      {"id": module_id, "data": data},
      self.state_threads,
      "module",
    )


class RecordingOTAPI:
  """Explicit namespace matching the production dependency boundary."""

  def __init__(self) -> None:
    self.modules = RecordingModules()


def restore_ot_api(original_use_ot: bool, original_ot_api: Optional[ModuleType]) -> None:
  """Restore module globals after one software-only test lifespan."""
  backend_module.USE_OT = original_use_ot
  if original_ot_api is not None:
    backend_module.ot_api = original_ot_api
  else:
    del backend_module.ot_api


class TestOpentronsTemperatureModuleBackend(AnyioTestBase):
  """Exercise native scope and worker behavior under asyncio and Trio."""

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    original_use_ot = backend_module.USE_OT
    original_ot_api = backend_module.ot_api if original_use_ot else None
    self.api = RecordingOTAPI()
    backend_module.USE_OT = True
    backend_module.ot_api = cast(Any, self.api)
    stack.callback(restore_ot_api, original_use_ot, original_ot_api)
    self.backend = OpentronsTemperatureModuleBackend(opentrons_id="temperature-id")
    self.async_thread = threading.get_ident()

  async def test_constructor_initializes_native_lifecycle_and_serializes_unchanged(self) -> None:
    assert not self.backend.is_active
    assert self.backend in MachineBackend.get_all_instances()
    assert self.backend.serialize() == {
      "type": "OpentronsTemperatureModuleBackend",
      "opentrons_id": "temperature-id",
    }
    assert OpentronsTemperatureModuleBackend.setup is HasLifespan.setup
    assert OpentronsTemperatureModuleBackend.stop is HasLifespan.stop

  @pytest.mark.parametrize(
    "blocked_call",
    [
      "temperature_module_set_temperature",
      "temperature_module_deactivate",
      "list_connected_modules",
    ],
  )
  async def test_blocking_external_call_keeps_async_thread_responsive(
    self, blocked_call: str
  ) -> None:
    self.api.modules.modules = [self.api.modules.module_response("temperature-id", 21.5)]
    self.api.modules.block_at = blocked_call
    progressed = anyio.Event()
    finished = anyio.Event()

    async def operate() -> None:
      if blocked_call == "temperature_module_set_temperature":
        await self.backend.set_temperature(37.25)
      elif blocked_call == "temperature_module_deactivate":
        await self.backend.deactivate()
      else:
        await self.backend.get_current_temperature()
      finished.set()

    async def progress() -> None:
      await anyio.lowlevel.checkpoint()
      progressed.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(operate)
      assert await anyio.to_thread.run_sync(self.api.modules.block_started.wait, 2)
      task_group.start_soon(progress)
      await progressed.wait()
      assert not self.api.modules.block_timed_out
      self.api.modules.block_release.set()
      await finished.wait()

    call_thread = next(
      thread_id for name, thread_id in self.api.modules.call_threads if name == blocked_call
    )
    assert call_thread != self.async_thread

  async def test_operations_preserve_arguments_returns_and_async_response_parsing(self) -> None:
    self.api.modules.modules = [
      self.api.modules.module_response("other-id", 19.0),
      self.api.modules.module_response("temperature-id", 23.75),
    ]

    assert await self.backend.set_temperature(37.25) is None
    assert await self.backend.deactivate() is None
    assert await self.backend.get_current_temperature() == 23.75

    assert self.api.modules.calls == [
      (
        "temperature_module_set_temperature",
        (),
        {"celsius": 37.25, "module_id": "temperature-id"},
      ),
      ("temperature_module_deactivate", (), {"module_id": "temperature-id"}),
      ("list_connected_modules", (), {}),
    ]
    assert all(thread_id != self.async_thread for _, thread_id in self.api.modules.call_threads)
    assert self.api.modules.state_threads == [
      ("module.id", self.async_thread),
      ("module.id", self.async_thread),
      ("module.data", self.async_thread),
      ("module.data.currentTemperature", self.async_thread),
    ]

  async def test_missing_module_preserves_error(self) -> None:
    self.api.modules.modules = [self.api.modules.module_response("other-id", 19.0)]

    with pytest.raises(RuntimeError, match="^Module with id 'temperature-id' not found$"):
      await self.backend.get_current_temperature()

  @pytest.mark.parametrize(
    "failed_call",
    [
      "temperature_module_set_temperature",
      "temperature_module_deactivate",
      "list_connected_modules",
    ],
  )
  async def test_external_exception_identity_is_preserved(self, failed_call: str) -> None:
    failure = RuntimeError(f"failed at {failed_call}")
    self.api.modules.failures[failed_call] = failure

    with pytest.raises(RuntimeError) as error_info:
      if failed_call == "temperature_module_set_temperature":
        await self.backend.set_temperature(37.25)
      elif failed_call == "temperature_module_deactivate":
        await self.backend.deactivate()
      else:
        await self.backend.get_current_temperature()

    assert error_info.value is failure

  async def test_normal_exit_deactivates(self) -> None:
    async with self.backend:
      assert self.backend.is_active

    assert self.api.modules.calls == [
      ("temperature_module_deactivate", (), {"module_id": "temperature-id"})
    ]
    assert not self.backend.is_active

  async def test_lifespan_rejects_setup_options(self) -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with self.backend.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")

    assert self.api.modules.calls == []
    assert not self.backend.is_active

  async def test_body_cancellation_waits_for_shielded_deactivation(self) -> None:
    self.api.modules.block_at = "temperature_module_deactivate"
    body_started = anyio.Event()
    finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    observed_cancellation: List[BaseException] = []

    async def run_body() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with self.backend:
            body_started.set()
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class() as error:
          observed_cancellation.append(error)
          raise
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_body)
      await body_started.wait()
      self.api.modules.cancellation_requested.set()
      scopes[0].cancel()
      assert await anyio.to_thread.run_sync(self.api.modules.block_started.wait, 2)
      assert not finished.is_set()
      self.api.modules.block_release.set()
      await finished.wait()

    assert len(observed_cancellation) == 1
    assert self.api.modules.cleanup_cancel_states == [True]
    assert not self.api.modules.block_timed_out
    assert not self.backend.is_active

  async def test_deactivation_failure_propagates_from_exit(self) -> None:
    failure = RuntimeError("deactivation failed")
    self.api.modules.failures["temperature_module_deactivate"] = failure

    with pytest.raises(RuntimeError) as error_info:
      async with self.backend:
        pass

    assert error_info.value is failure
    assert not self.backend.is_active

  async def test_backend_supports_sequential_reuse(self) -> None:
    async with self.backend:
      assert self.backend.is_active
    async with self.backend:
      assert self.backend.is_active

    assert self.api.modules.calls == [
      ("temperature_module_deactivate", (), {"module_id": "temperature-id"}),
      ("temperature_module_deactivate", (), {"module_id": "temperature-id"}),
    ]
    assert not self.backend.is_active
