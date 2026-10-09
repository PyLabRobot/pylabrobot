"""Software-only tests for the Opentrons HTTP thermocycler backend."""

import threading
from typing import Any, Dict, Iterator, List, Optional, Tuple, cast
from unittest.mock import patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.legacy.thermocycling.opentrons import OpentronsThermocyclerModuleV1
from pylabrobot.legacy.thermocycling.opentrons_backend import OpentronsThermocyclerBackend
from pylabrobot.legacy.thermocycling.standard import BlockStatus, LidStatus, Protocol, Stage, Step
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.testing.concurrency import AnyioTestBase

Call = Tuple[str, Tuple[object, ...], Dict[str, object]]


class RecordingMapping(dict[str, Any]):
  """Record which thread interprets an HTTP response mapping."""

  def __init__(self, values: Dict[str, Any], state_threads: List[int]) -> None:
    super().__init__(values)
    self.state_threads = state_threads

  def __getitem__(self, key: str) -> Any:
    self.state_threads.append(threading.get_ident())
    return super().__getitem__(key)

  def get(self, key: str, default: Any = None) -> Any:
    self.state_threads.append(threading.get_ident())
    return super().get(key, default)


class RecordingTemperatureList(list[float]):
  """Record profile-temperature inspection without changing its list contract."""

  def __init__(self, values: List[float], state_threads: List[int]) -> None:
    super().__init__(values)
    self.state_threads = state_threads

  def __iter__(self) -> Iterator[float]:
    self.state_threads.append(threading.get_ident())
    return super().__iter__()


class RecordingModules:
  """Typed synchronous stand-in for the imported ``ot_api.modules`` functions."""

  def __init__(self) -> None:
    self.calls: List[Call] = []
    self.call_threads: List[Tuple[str, int]] = []
    self.state_threads: List[int] = []
    self.failures: Dict[str, BaseException] = {}
    self.block_at: Optional[str] = None
    self.block_started = threading.Event()
    self.block_release = threading.Event()
    self.block_timed_out = False
    self.cancellation_requested = threading.Event()
    self.cleanup_cancel_states: List[Tuple[str, bool]] = []
    self.modules: List[Dict[str, Any]] = []
    self.results: Dict[str, object] = {
      "thermocycler_open_lid": object(),
      "thermocycler_close_lid": object(),
      "thermocycler_set_block_temperature": object(),
      "thermocycler_set_lid_temperature": object(),
      "thermocycler_deactivate_block": object(),
      "thermocycler_deactivate_lid": object(),
      "thermocycler_run_profile_no_wait": object(),
    }

  def _record(self, name: str, kwargs: Dict[str, object]) -> object:
    self.calls.append((name, (), kwargs))
    self.call_threads.append((name, threading.get_ident()))
    if name in ("thermocycler_deactivate_block", "thermocycler_deactivate_lid"):
      self.cleanup_cancel_states.append((name, self.cancellation_requested.is_set()))
    if self.block_at == name:
      self.block_started.set()
      if not self.block_release.wait(timeout=2):
        self.block_timed_out = True
    failure = self.failures.get(name)
    if failure is not None:
      raise failure
    return self.results.get(name, self.modules)

  def list_connected_modules(self) -> List[Dict[str, Any]]:
    return cast(List[Dict[str, Any]], self._record("list_connected_modules", {}))

  def thermocycler_open_lid(self, *, module_id: str) -> object:
    return self._record("thermocycler_open_lid", {"module_id": module_id})

  def thermocycler_close_lid(self, *, module_id: str) -> object:
    return self._record("thermocycler_close_lid", {"module_id": module_id})

  def thermocycler_set_block_temperature(self, *, celsius: float, module_id: str) -> object:
    return self._record(
      "thermocycler_set_block_temperature", {"celsius": celsius, "module_id": module_id}
    )

  def thermocycler_set_lid_temperature(self, *, celsius: float, module_id: str) -> object:
    return self._record(
      "thermocycler_set_lid_temperature", {"celsius": celsius, "module_id": module_id}
    )

  def thermocycler_deactivate_block(self, *, module_id: str) -> object:
    return self._record("thermocycler_deactivate_block", {"module_id": module_id})

  def thermocycler_deactivate_lid(self, *, module_id: str) -> object:
    return self._record("thermocycler_deactivate_lid", {"module_id": module_id})

  def thermocycler_run_profile_no_wait(
    self, *, profile: List[Dict[str, float]], block_max_volume: float, module_id: str
  ) -> object:
    return self._record(
      "thermocycler_run_profile_no_wait",
      {"profile": profile, "block_max_volume": block_max_volume, "module_id": module_id},
    )

  def module_response(self, module_id: str) -> Dict[str, Any]:
    """Build a complete response fragment with observable mapping reads."""
    data = RecordingMapping(
      {
        "currentTemperature": 25.5,
        "targetTemperature": 95.0,
        "lidTemperature": 37.1,
        "lidTargetTemperature": 105.0,
        "lidStatus": "open",
        "lidTemperatureStatus": "holding at target",
        "status": "holding at target",
        "holdTime": 12.0,
        "currentCycleIndex": 2,
        "totalCycleCount": 10,
        "currentStepIndex": 1,
        "totalStepCount": 3,
      },
      self.state_threads,
    )
    return RecordingMapping({"id": module_id, "data": data}, self.state_threads)


class RecordingProtocolBackend(OpentronsThermocyclerBackend):
  """Observe protocol publication while retaining the production implementation."""

  protocol_publication_threads: List[int]
  published_protocol: Optional[Protocol]

  def __init__(self, opentrons_id: str) -> None:
    self.protocol_publication_threads = []
    self.published_protocol = None
    super().__init__(opentrons_id)

  @property
  def _current_protocol(self) -> Optional[Protocol]:
    return self.published_protocol

  @_current_protocol.setter
  def _current_protocol(self, protocol: Optional[Protocol]) -> None:
    self.protocol_publication_threads.append(threading.get_ident())
    self.published_protocol = protocol


class TestOpentronsThermocyclerBackend(AnyioTestBase):
  """Exercise native scope and worker behavior under asyncio and Trio."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.api = RecordingModules()
    stack.enter_context(
      patch(
        "pylabrobot.legacy.thermocycling.opentrons_backend.list_connected_modules",
        new=self.api.list_connected_modules,
        create=True,
      )
    )
    stack.enter_context(
      patch(
        "pylabrobot.legacy.thermocycling.opentrons_backend.thermocycler_open_lid",
        new=self.api.thermocycler_open_lid,
        create=True,
      )
    )
    stack.enter_context(
      patch(
        "pylabrobot.legacy.thermocycling.opentrons_backend.thermocycler_close_lid",
        new=self.api.thermocycler_close_lid,
        create=True,
      )
    )
    stack.enter_context(
      patch(
        "pylabrobot.legacy.thermocycling.opentrons_backend.thermocycler_set_block_temperature",
        new=self.api.thermocycler_set_block_temperature,
        create=True,
      )
    )
    stack.enter_context(
      patch(
        "pylabrobot.legacy.thermocycling.opentrons_backend.thermocycler_set_lid_temperature",
        new=self.api.thermocycler_set_lid_temperature,
        create=True,
      )
    )
    stack.enter_context(
      patch(
        "pylabrobot.legacy.thermocycling.opentrons_backend.thermocycler_deactivate_block",
        new=self.api.thermocycler_deactivate_block,
        create=True,
      )
    )
    stack.enter_context(
      patch(
        "pylabrobot.legacy.thermocycling.opentrons_backend.thermocycler_deactivate_lid",
        new=self.api.thermocycler_deactivate_lid,
        create=True,
      )
    )
    stack.enter_context(
      patch(
        "pylabrobot.legacy.thermocycling.opentrons_backend.thermocycler_run_profile_no_wait",
        new=self.api.thermocycler_run_profile_no_wait,
        create=True,
      )
    )
    stack.enter_context(patch("pylabrobot.legacy.thermocycling.opentrons_backend.USE_OT", new=True))
    self.backend = RecordingProtocolBackend(opentrons_id="test_id")
    self.async_thread = threading.get_ident()

  def test_opentrons_v1_serialization(self) -> None:
    tc_model = OpentronsThermocyclerModuleV1(
      name="test_v1_tc",
      opentrons_id="test_id",
      child=ItemizedResource(name="plate", size_x=1, size_y=1, size_z=1, ordered_items={}),
    )
    serialized = tc_model.serialize()
    assert serialized["opentrons_id"] == "test_id"
    assert tc_model == OpentronsThermocyclerModuleV1.deserialize(serialized)

  def test_constructor_uses_native_lifecycle_and_serializes_unchanged(self) -> None:
    concrete_backend = OpentronsThermocyclerBackend(opentrons_id="test_id")
    assert not self.backend.is_active
    assert self.backend in MachineBackend.get_all_instances()
    assert concrete_backend.serialize() == {
      "type": "OpentronsThermocyclerBackend",
      "opentrons_id": "test_id",
    }
    assert OpentronsThermocyclerBackend.setup is HasLifespan.setup
    assert OpentronsThermocyclerBackend.stop is HasLifespan.stop

  async def test_operations_preserve_arguments_returns_and_response_interpretation(self) -> None:
    self.api.modules = [self.api.module_response("test_id")]

    assert await self.backend.open_lid() is self.api.results["thermocycler_open_lid"]
    assert await self.backend.close_lid() is self.api.results["thermocycler_close_lid"]
    assert (
      await self.backend.set_block_temperature([95.0])
      is self.api.results["thermocycler_set_block_temperature"]
    )
    assert (
      await self.backend.set_lid_temperature([105.0])
      is self.api.results["thermocycler_set_lid_temperature"]
    )
    assert (
      await self.backend.deactivate_block() is self.api.results["thermocycler_deactivate_block"]
    )
    assert await self.backend.deactivate_lid() is self.api.results["thermocycler_deactivate_lid"]
    assert await self.backend.get_block_current_temperature() == [25.5]
    assert await self.backend.get_block_target_temperature() == [95.0]
    assert await self.backend.get_lid_current_temperature() == [37.1]
    assert await self.backend.get_lid_target_temperature() == [105.0]
    assert await self.backend.get_lid_open() is True
    assert await self.backend.get_lid_status() == LidStatus.HOLDING_AT_TARGET
    assert await self.backend.get_block_status() == BlockStatus.HOLDING_AT_TARGET
    assert await self.backend.get_hold_time() == 12.0
    assert await self.backend.get_current_step_index() == 0
    assert await self.backend.get_total_step_count() == 3

    assert self.api.calls[:6] == [
      ("thermocycler_open_lid", (), {"module_id": "test_id"}),
      ("thermocycler_close_lid", (), {"module_id": "test_id"}),
      ("thermocycler_set_block_temperature", (), {"celsius": 95.0, "module_id": "test_id"}),
      ("thermocycler_set_lid_temperature", (), {"celsius": 105.0, "module_id": "test_id"}),
      ("thermocycler_deactivate_block", (), {"module_id": "test_id"}),
      ("thermocycler_deactivate_lid", (), {"module_id": "test_id"}),
    ]
    assert [name for name, _, _ in self.api.calls[6:]] == ["list_connected_modules"] * 10
    assert all(thread_id != self.async_thread for _, thread_id in self.api.call_threads)
    assert self.api.state_threads
    assert set(self.api.state_threads) == {self.async_thread}

  @pytest.mark.parametrize(
    "blocked_call",
    [
      "thermocycler_open_lid",
      "thermocycler_close_lid",
      "thermocycler_set_block_temperature",
      "thermocycler_set_lid_temperature",
      "thermocycler_deactivate_block",
      "thermocycler_deactivate_lid",
      "thermocycler_run_profile_no_wait",
      "list_connected_modules",
    ],
  )
  async def test_blocking_external_call_keeps_async_thread_responsive(
    self, blocked_call: str
  ) -> None:
    self.api.modules = [self.api.module_response("test_id")]
    self.api.block_at = blocked_call
    progressed = anyio.Event()
    finished = anyio.Event()

    async def operate() -> None:
      if blocked_call == "thermocycler_open_lid":
        await self.backend.open_lid()
      elif blocked_call == "thermocycler_close_lid":
        await self.backend.close_lid()
      elif blocked_call == "thermocycler_set_block_temperature":
        await self.backend.set_block_temperature([95.0])
      elif blocked_call == "thermocycler_set_lid_temperature":
        await self.backend.set_lid_temperature([105.0])
      elif blocked_call == "thermocycler_deactivate_block":
        await self.backend.deactivate_block()
      elif blocked_call == "thermocycler_deactivate_lid":
        await self.backend.deactivate_lid()
      elif blocked_call == "thermocycler_run_profile_no_wait":
        await self.backend.run_protocol(
          Protocol(stages=[Stage(steps=[Step([95.0], 10)], repeats=1)]), 50.0
        )
      else:
        await self.backend.get_lid_open()
      finished.set()

    async def progress() -> None:
      await anyio.lowlevel.checkpoint()
      progressed.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(operate)
      assert await anyio.to_thread.run_sync(self.api.block_started.wait, 3)
      task_group.start_soon(progress)
      await progressed.wait()
      assert not self.api.block_timed_out
      self.api.block_release.set()
      await finished.wait()

    call_thread = next(
      thread_id for name, thread_id in self.api.call_threads if name == blocked_call
    )
    assert call_thread != self.async_thread

  async def test_profile_is_flattened_and_published_on_async_thread_after_submission(self) -> None:
    state_threads: List[int] = []
    protocol = Protocol(
      stages=[
        Stage(
          steps=[
            Step(RecordingTemperatureList([95.0], state_threads), 10),
            Step(RecordingTemperatureList([55.0], state_threads), 20),
          ],
          repeats=2,
        ),
        Stage(steps=[Step(RecordingTemperatureList([72.0], state_threads), 30)], repeats=1),
      ]
    )

    result = await self.backend.run_protocol(protocol, 50.0)

    assert result is self.api.results["thermocycler_run_profile_no_wait"]
    assert self.api.calls == [
      (
        "thermocycler_run_profile_no_wait",
        (),
        {
          "profile": [
            {"celsius": 95.0, "holdSeconds": 10},
            {"celsius": 95.0, "holdSeconds": 10},
            {"celsius": 55.0, "holdSeconds": 20},
            {"celsius": 55.0, "holdSeconds": 20},
            {"celsius": 72.0, "holdSeconds": 30},
          ],
          "block_max_volume": 50.0,
          "module_id": "test_id",
        },
      )
    ]
    assert state_threads
    assert set(state_threads) == {self.async_thread}
    assert self.backend.published_protocol is protocol
    assert self.backend.protocol_publication_threads[-1] == self.async_thread
    assert self.backend.protocol_publication_threads[-1] != self.api.call_threads[-1][1]

  async def test_failed_profile_submission_leaves_prior_protocol_unchanged(self) -> None:
    prior = Protocol(stages=[])
    replacement = Protocol(stages=[Stage(steps=[Step([95.0], 10)], repeats=1)])
    await self.backend.run_protocol(prior, 50.0)
    failure = RuntimeError("profile submission failed")
    self.api.failures["thermocycler_run_profile_no_wait"] = failure

    with pytest.raises(RuntimeError) as error_info:
      await self.backend.run_protocol(replacement, 25.0)

    assert error_info.value is failure
    assert self.backend.published_protocol is prior

  async def test_find_module_and_status_errors_are_preserved(self) -> None:
    self.api.modules = [self.api.module_response("some_other_id")]
    with pytest.raises(RuntimeError, match="^Module 'test_id' not found$"):
      await self.backend.get_lid_open()

    self.api.modules = [RecordingMapping({"id": "test_id", "data": {}}, [])]
    with pytest.raises(
      RuntimeError, match=r"^Hold time is not available\. Is a profile running\?$"
    ):
      await self.backend.get_hold_time()

  async def test_normal_exit_deactivates_block_then_lid(self) -> None:
    async with self.backend:
      assert self.backend.is_active

    assert self.api.calls == [
      ("thermocycler_deactivate_block", (), {"module_id": "test_id"}),
      ("thermocycler_deactivate_lid", (), {"module_id": "test_id"}),
    ]
    assert not self.backend.is_active

  async def test_lifespan_rejects_setup_options(self) -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with self.backend.lifespan(unknown=True):
        pytest.fail("invalid entry reached the body")
    assert self.api.calls == []

  async def test_body_cancellation_waits_for_both_shielded_cleanups(self) -> None:
    self.api.block_at = "thermocycler_deactivate_block"
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
      self.api.cancellation_requested.set()
      scopes[0].cancel()
      assert await anyio.to_thread.run_sync(self.api.block_started.wait, 3)
      assert not finished.is_set()
      self.api.block_release.set()
      await finished.wait()

    assert len(observed_cancellation) == 1
    assert self.api.cleanup_cancel_states == [
      ("thermocycler_deactivate_block", True),
      ("thermocycler_deactivate_lid", True),
    ]
    assert not self.api.block_timed_out
    assert not self.backend.is_active

  @pytest.mark.parametrize(
    ("failed_calls", "propagated", "context"),
    [
      (["thermocycler_deactivate_block"], "block failed", None),
      (["thermocycler_deactivate_lid"], "lid failed", None),
      (
        ["thermocycler_deactivate_block", "thermocycler_deactivate_lid"],
        "lid failed",
        "block failed",
      ),
    ],
  )
  async def test_cleanup_failures_do_not_suppress_the_other_cleanup(
    self, failed_calls: List[str], propagated: str, context: Optional[str]
  ) -> None:
    failures = {
      "thermocycler_deactivate_block": RuntimeError("block failed"),
      "thermocycler_deactivate_lid": RuntimeError("lid failed"),
    }
    for failed_call in failed_calls:
      self.api.failures[failed_call] = failures[failed_call]

    with pytest.raises(RuntimeError) as error_info:
      async with self.backend:
        pass

    assert str(error_info.value) == propagated
    if context is None:
      assert error_info.value.__context__ is None
    else:
      assert error_info.value.__context__ is failures["thermocycler_deactivate_block"]
      assert str(error_info.value.__context__) == context
    assert [name for name, _, _ in self.api.calls] == [
      "thermocycler_deactivate_block",
      "thermocycler_deactivate_lid",
    ]
    assert not self.backend.is_active

  async def test_backend_supports_sequential_reuse(self) -> None:
    async with self.backend:
      assert self.backend.is_active
    async with self.backend:
      assert self.backend.is_active

    assert [name for name, _, _ in self.api.calls] == [
      "thermocycler_deactivate_block",
      "thermocycler_deactivate_lid",
      "thermocycler_deactivate_block",
      "thermocycler_deactivate_lid",
    ]
    assert not self.backend.is_active
