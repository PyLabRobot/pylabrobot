import inspect
import unittest
from typing import Any, List, Optional

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.storage import Incubator, IncubatorChatterboxBackend
from pylabrobot.legacy.storage.backend import IncubatorBackend
from pylabrobot.resources import Plate, PlateHolder
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.testing.concurrency import AnyioTestBase


class IncubatorTests(unittest.TestCase):
  def test_serialization(self):
    i = Incubator(
      name="test_tc",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=IncubatorChatterboxBackend(),
      loading_tray_location=Coordinate(0, 0, 0),
      racks=[],
    )

    serialized = i.serialize()
    deserialized = Incubator.deserialize(serialized)
    assert i == deserialized

  def test_backend_durable_shaking_signatures(self) -> None:
    start = inspect.signature(IncubatorBackend.start_shaking).parameters
    assert tuple(start) == ("self", "frequency")
    assert start["frequency"].annotation is float
    assert start["frequency"].default is inspect.Parameter.empty
    assert tuple(inspect.signature(IncubatorBackend.stop_shaking).parameters) == ("self",)


class SoftwareIncubatorBackend(IncubatorBackend):
  """Incubator backend double with controllable shaking checkpoints."""

  def __init__(self) -> None:
    super().__init__()
    self.events: List[str] = []
    self.start_error: Optional[BaseException] = None
    self.stop_errors: List[BaseException] = []
    self.start_entered: Optional[anyio.Event] = None
    self.release_start: Optional[anyio.Event] = None

  async def _record_stop(self) -> None:
    self.events.append("backend.stop")

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    if backend_kwargs:
      raise TypeError("SoftwareIncubatorBackend does not accept setup options")
    self.events.append("setup")
    stack.push_shielded_async_callback(self._record_stop)

  async def open_door(self) -> None:
    pass

  async def close_door(self) -> None:
    pass

  async def fetch_plate_to_loading_tray(self, plate: Plate, **backend_kwargs: object) -> None:
    pass

  async def take_in_plate(self, plate: Plate, site: PlateHolder, **backend_kwargs: object) -> None:
    pass

  async def set_temperature(self, temperature: float) -> None:
    pass

  async def get_temperature(self) -> float:
    return 37.0

  async def _start_shaking_hardware(
    self, frequency: float, shakers: Optional[List[int]] = None
  ) -> None:
    self.events.append(f"start:{frequency}")
    if self.start_entered is not None:
      self.start_entered.set()
    if self.release_start is not None:
      await self.release_start.wait()
    if self.start_error is not None:
      raise self.start_error

  async def _stop_shaking_hardware(self) -> None:
    self.events.append("stop")
    if self.stop_errors:
      raise self.stop_errors.pop(0)


@pytest.mark.parametrize("backend_type", [SoftwareIncubatorBackend])
def test_backend_double_inherits_managed_methods(
  backend_type: type[HasLifespan],
) -> None:
  assert backend_type.setup is HasLifespan.setup
  assert backend_type.stop is HasLifespan.stop


def make_incubator(backend: IncubatorBackend) -> Incubator:
  return Incubator(
    name="incubator",
    size_x=10,
    size_y=10,
    size_z=10,
    backend=backend,
    loading_tray_location=Coordinate(0, 0, 0),
    racks=[],
  )


def exception_context_contains(error: BaseException, target: BaseException) -> bool:
  while error.__context__ is not None:
    error = error.__context__
    if error is target:
      return True
  return False


class TestIncubatorShakingScope(AnyioTestBase):
  async def test_normal_scope_and_body_error_stop(self) -> None:
    backend = SoftwareIncubatorBackend()
    incubator = make_incubator(backend)

    async with incubator:
      async with incubator.shaking(12.5):
        assert backend._shaking_active
      body_error = ValueError("body failed")
      with pytest.raises(ValueError, match="body failed") as error_info:
        async with incubator.shaking(8):
          raise body_error
      assert error_info.value is body_error

    assert backend.events == ["setup", "start:12.5", "stop", "start:8", "stop", "backend.stop"]

  async def test_body_cancellation_finishes_stop_before_task_finishes(self) -> None:
    backend = SoftwareIncubatorBackend()
    incubator = make_incubator(backend)
    finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def use_scope() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with incubator.shaking():
            await anyio.sleep_forever()
      finally:
        finished.set()

    async with incubator:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_scope)
        while not backend._shaking_active:
          await anyio.sleep(0)
        scopes[0].cancel()
        await finished.wait()
        assert backend.events[-1] == "stop"

  async def test_cancellation_after_start_commits_reconciles_before_propagating(self) -> None:
    backend = SoftwareIncubatorBackend()
    backend.start_entered = anyio.Event()
    backend.release_start = anyio.Event()
    incubator = make_incubator(backend)
    finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def use_scope() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with incubator.shaking():
            pytest.fail("cancelled start reached body")
      finally:
        finished.set()

    async with incubator:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_scope)
        await backend.start_entered.wait()
        scopes[0].cancel()
        await anyio.sleep(0)
        assert not finished.is_set()
        backend.release_start.set()
        await finished.wait()
      assert backend.events[-1] == "stop"

  async def test_start_failure_rolls_back_without_entering_body(self) -> None:
    backend = SoftwareIncubatorBackend()
    backend.start_error = RuntimeError("start failed")
    incubator = make_incubator(backend)

    async with incubator:
      with pytest.raises(RuntimeError, match="start failed"):
        async with incubator.shaking():
          pytest.fail("failed start reached body")
      assert not backend._shaking_active
      assert not backend._shaking_state_unknown

    assert backend.events == ["setup", "start:1.0", "stop", "backend.stop"]

  async def test_stop_failure_retains_retryable_state_and_body_context(self) -> None:
    backend = SoftwareIncubatorBackend()
    backend.stop_errors.append(RuntimeError("stop failed"))
    incubator = make_incubator(backend)
    body_error = ValueError("body failed")

    async with incubator:
      with pytest.raises(RuntimeError, match="stop failed") as error_info:
        async with incubator.shaking():
          raise body_error
      assert exception_context_contains(error_info.value, body_error)
      assert backend._shaking_active
      assert backend._shaking_state_unknown
      assert backend._shaking_scope_token is None
      await incubator.stop_shaking()
      assert not backend._shaking_active
      assert not backend._shaking_state_unknown

  async def test_overlap_and_direct_transitions_are_rejected(self) -> None:
    backend = SoftwareIncubatorBackend()
    incubator = make_incubator(backend)
    failures: List[RuntimeError] = []

    async def overlap() -> None:
      try:
        async with backend.shaking(2):
          pytest.fail("overlapping backend scope reached body")
      except RuntimeError as error:
        failures.append(error)

    async with incubator:
      async with incubator.shaking():
        with pytest.raises(RuntimeError, match="already scoped"):
          async with incubator.shaking():
            pytest.fail("nested scope reached body")
        with pytest.raises(RuntimeError, match="scope"):
          await incubator.start_shaking()
        with pytest.raises(RuntimeError, match="scope"):
          await incubator.stop_shaking()
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(overlap)

    assert len(failures) == 1
    assert "already scoped" in str(failures[0])

  async def test_durable_frontend_state_is_not_adopted_by_scope(self) -> None:
    backend = SoftwareIncubatorBackend()
    incubator = make_incubator(backend)

    async with incubator:
      await incubator.start_shaking(3)
      with pytest.raises(RuntimeError, match="already active"):
        async with backend.shaking():
          pytest.fail("scope adopted durable shaking")
      await incubator.stop_shaking()

    assert backend.events == ["setup", "start:3", "stop", "backend.stop"]

  async def test_parent_cleanup_stops_and_invalidates_live_scope(self) -> None:
    backend = SoftwareIncubatorBackend()
    incubator = make_incubator(backend)
    lifespan = incubator.lifespan()
    scope = incubator.shaking()

    await lifespan.__aenter__()
    await scope.__aenter__()
    await lifespan.__aexit__(None, None, None)
    assert backend._shaking_scope_token is None
    assert backend.events[-2:] == ["stop", "backend.stop"]
    stop_count = backend.events.count("stop")
    await scope.__aexit__(None, None, None)

    assert backend.events.count("stop") == stop_count

  async def test_frontend_and_backend_scopes_share_one_owner(self) -> None:
    backend = SoftwareIncubatorBackend()
    incubator = make_incubator(backend)

    async with incubator:
      async with backend.shaking():
        with pytest.raises(RuntimeError, match="already scoped"):
          async with incubator.shaking():
            pytest.fail("frontend created a second ownership token")


class TestIncubatorChatterboxShaking(AnyioTestBase):
  def test_preserves_public_signatures(self) -> None:
    start = inspect.signature(IncubatorChatterboxBackend.start_shaking).parameters
    assert tuple(start) == ("self", "frequency")
    assert start["frequency"].annotation is float
    assert start["frequency"].default is inspect.Parameter.empty
    assert tuple(inspect.signature(IncubatorChatterboxBackend.stop_shaking).parameters) == ("self",)

  async def test_durable_calls_and_scoped_guards_use_initialized_owner(self, capsys) -> None:
    backend = IncubatorChatterboxBackend()

    async with backend:
      await backend.start_shaking(3)
      assert backend._shaking_active
      await backend.stop_shaking()
      assert not backend._shaking_active

      async with backend.shaking(4):
        output_before_rejections = capsys.readouterr().out
        with pytest.raises(RuntimeError, match="scope"):
          await backend.start_shaking(5)
        with pytest.raises(RuntimeError, match="scope"):
          await backend.stop_shaking()
        assert capsys.readouterr().out == ""

    assert "Starting shaking at 3 Hz" in output_before_rejections

  async def test_direct_lifespan_cleanup_stops_and_invalidates_live_scope(self, capsys) -> None:
    backend = IncubatorChatterboxBackend()
    lifespan = backend.lifespan()
    scope = backend.shaking(4)

    await lifespan.__aenter__()
    await scope.__aenter__()
    capsys.readouterr()
    await lifespan.__aexit__(None, None, None)
    assert backend._shaking_scope_token is None
    assert capsys.readouterr().out == "Stopping shaking\nStopping incubator backend\n"
    await scope.__aexit__(None, None, None)
    assert capsys.readouterr().out == ""
