from typing import Any, Dict, List, Optional, Union
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.arms.backend import AccessPattern, SCARABackend
from pylabrobot.legacy.arms.precise_flex.coords import PreciseFlexCartesianCoords
from pylabrobot.legacy.arms.scara import ExperimentalSCARA
from pylabrobot.resources import Coordinate, Rotation
from pylabrobot.testing.concurrency import AnyioTestBase


class RecordingSCARABackend(SCARABackend):
  """Exercise SCARA ownership without communicating with an arm."""

  def __init__(self) -> None:
    super().__init__()
    self.events: List[str] = []
    self.start_error: Optional[BaseException] = None
    self.end_error: Optional[BaseException] = None

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **backend_kwargs: Any,
  ) -> None:
    assert backend_kwargs == {}
    self.events.append("backend.setup")
    stack.push_shielded_async_callback(self._stop)

  async def _stop(self) -> None:
    self.events.append("backend.stop")

  async def move_to(
    self, position: Union[PreciseFlexCartesianCoords, Dict[int, float]], **kwargs: Any
  ) -> None:
    self.events.append("move")

  async def get_joint_position(self, **kwargs: Any) -> Dict[int, float]:
    return {}

  async def get_cartesian_position(self, **kwargs: Any) -> PreciseFlexCartesianCoords:
    raise NotImplementedError

  async def open_gripper(self, gripper_width: float, **kwargs: Any) -> None:
    self.events.append("open")

  async def close_gripper(self, gripper_width: float, **kwargs: Any) -> None:
    self.events.append("close")

  async def is_gripper_closed(self, **kwargs: Any) -> bool:
    return False

  async def halt(self, **kwargs: Any) -> None:
    self.events.append("halt")

  async def home(self, **kwargs: Any) -> None:
    self.events.append("home")

  async def move_to_safe(self, **kwargs: Any) -> None:
    self.events.append("safe")

  async def approach(
    self,
    position: Union[PreciseFlexCartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
    **kwargs: Any,
  ) -> None:
    self.events.append("approach")

  async def pick_up_resource(
    self,
    position: Union[PreciseFlexCartesianCoords, Dict[int, float]],
    plate_width: float,
    access: Optional[AccessPattern] = None,
    **kwargs: Any,
  ) -> None:
    self.events.append("pick")

  async def drop_resource(
    self,
    position: Union[PreciseFlexCartesianCoords, Dict[int, float]],
    access: Optional[AccessPattern] = None,
    **kwargs: Any,
  ) -> None:
    self.events.append("drop")

  async def freedrive_mode(self, free_axes: List[int], **kwargs: Any) -> None:
    self.events.append(f"freedrive:{free_axes}:{kwargs}")
    if self.start_error is not None:
      raise self.start_error

  async def end_freedrive_mode(self, **kwargs: Any) -> None:
    self.events.append(f"end:{kwargs}")
    if self.end_error is not None:
      raise self.end_error


class TestExperimentalSCARA(AnyioTestBase):
  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    self.mock_backend = MagicMock(spec=SCARABackend)
    self.mock_backend.move_to = AsyncMock()
    self.mock_backend.get_joint_position = AsyncMock()
    self.mock_backend.get_cartesian_position = AsyncMock()
    self.mock_backend.open_gripper = AsyncMock()
    self.mock_backend.close_gripper = AsyncMock()
    self.mock_backend.is_gripper_closed = AsyncMock()
    self.mock_backend.halt = AsyncMock()
    self.mock_backend.home = AsyncMock()
    self.mock_backend.move_to_safe = AsyncMock()
    self.mock_backend.approach = AsyncMock()
    self.mock_backend.pick_up_resource = AsyncMock()
    self.mock_backend.drop_resource = AsyncMock()
    self.scara = ExperimentalSCARA(backend=self.mock_backend)

  async def test_move_to(self):
    position = PreciseFlexCartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    await self.scara.move_to(position)
    self.mock_backend.move_to.assert_called_once_with(position)

  async def test_get_joint_position(self):
    await self.scara.get_joint_position()
    self.mock_backend.get_joint_position.assert_called_once()

  async def test_get_cartesian_position(self):
    await self.scara.get_cartesian_position()
    self.mock_backend.get_cartesian_position.assert_called_once()

  async def test_open_gripper(self):
    gripper_width = 50.0
    await self.scara.open_gripper(gripper_width=gripper_width)
    self.mock_backend.open_gripper.assert_called_once_with(gripper_width=gripper_width)

  async def test_close_gripper(self):
    gripper_width = 50.0
    await self.scara.close_gripper(gripper_width=gripper_width)
    self.mock_backend.close_gripper.assert_called_once_with(gripper_width=gripper_width)

  async def test_is_gripper_closed(self):
    await self.scara.is_gripper_closed()
    self.mock_backend.is_gripper_closed.assert_called_once()

  async def test_halt(self):
    await self.scara.halt()
    self.mock_backend.halt.assert_called_once()

  async def test_home(self):
    await self.scara.home()
    self.mock_backend.home.assert_called_once()

  async def test_move_to_safe(self):
    await self.scara.move_to_safe()
    self.mock_backend.move_to_safe.assert_called_once()

  async def test_approach(self):
    position = PreciseFlexCartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    await self.scara.approach(position)
    self.mock_backend.approach.assert_called_once_with(position, access=None)

  async def test_pick_up_resource(self):
    position = PreciseFlexCartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    plate_width = 10.0
    await self.scara.pick_up_resource(position, plate_width)
    self.mock_backend.pick_up_resource.assert_called_once_with(
      plate_width=plate_width, position=position, access=None
    )

  async def test_drop_resource(self):
    position = PreciseFlexCartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    await self.scara.drop_resource(position)
    self.mock_backend.drop_resource.assert_called_once_with(position, access=None)


class TestExperimentalSCARAFreedrive(AnyioTestBase):
  async def test_scope_orders_entry_body_cleanup_and_backend_stop(self) -> None:
    backend = RecordingSCARABackend()
    scara = ExperimentalSCARA(backend)

    async with scara:
      async with scara.freedrive([1, 3], compliance=2):
        backend.events.append("body")

    assert backend.events == [
      "backend.setup",
      "freedrive:[1, 3]:{'compliance': 2}",
      "body",
      "end:{}",
      "backend.stop",
    ]

  async def test_body_cancellation_finishes_freedrive_cleanup_first(self) -> None:
    backend = RecordingSCARABackend()
    scara = ExperimentalSCARA(backend)
    entered = anyio.Event()
    finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def use_scope() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with scara.freedrive([0]):
            entered.set()
            await anyio.sleep_forever()
      finally:
        backend.events.append("cancelled")
        finished.set()

    async with scara:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_scope)
        await entered.wait()
        scopes[0].cancel()
        await finished.wait()

    assert backend.events.index("end:{}") < backend.events.index("cancelled")

  async def test_scope_rejects_motion_and_direct_transitions_before_mutation(self) -> None:
    backend = RecordingSCARABackend()
    scara = ExperimentalSCARA(backend)

    async with scara:
      async with scara.freedrive([0]):
        with pytest.raises(RuntimeError, match="scope"):
          await scara.move_to({})
        with pytest.raises(RuntimeError, match="scope"):
          await scara.open_gripper(10)
        with pytest.raises(RuntimeError, match="scope"):
          await scara.close_gripper(10)
        with pytest.raises(RuntimeError, match="scope"):
          await scara.home()
        with pytest.raises(RuntimeError, match="scope"):
          await scara.move_to_safe()
        with pytest.raises(RuntimeError, match="scope"):
          await scara.approach({})
        with pytest.raises(RuntimeError, match="scope"):
          await scara.pick_up_resource({}, 10)
        with pytest.raises(RuntimeError, match="scope"):
          await scara.drop_resource({})
        with pytest.raises(RuntimeError, match="scope"):
          await scara.freedrive_mode([1])
        with pytest.raises(RuntimeError, match="scope"):
          await scara.end_freedrive_mode()

    assert backend.events == ["backend.setup", "freedrive:[0]:{}", "end:{}", "backend.stop"]

  async def test_halt_and_raw_backend_halt_remain_emergency_callable(self) -> None:
    backend = RecordingSCARABackend()
    scara = ExperimentalSCARA(backend)

    async with scara:
      async with scara.freedrive([0]):
        await scara.halt()
        await backend.halt()
        assert scara._in_freedrive
        assert scara._freedrive_scope_token is not None

    assert backend.events == [
      "backend.setup",
      "freedrive:[0]:{}",
      "halt",
      "halt",
      "end:{}",
      "backend.stop",
    ]

  async def test_durable_freedrive_auto_exits_before_motion_and_can_end_directly(self) -> None:
    backend = RecordingSCARABackend()
    scara = ExperimentalSCARA(backend)

    await scara.freedrive_mode([1])
    await scara.move_to({})
    await scara.freedrive_mode([2])
    await scara.end_freedrive_mode(reason="done")

    assert backend.events == [
      "freedrive:[1]:{}",
      "end:{}",
      "move",
      "freedrive:[2]:{}",
      "end:{'reason': 'done'}",
    ]
    assert not scara._in_freedrive

  async def test_scope_rejects_preexisting_durable_freedrive(self) -> None:
    backend = RecordingSCARABackend()
    scara = ExperimentalSCARA(backend)

    async with scara:
      await scara.freedrive_mode([1])
      with pytest.raises(RuntimeError, match="already active"):
        async with scara.freedrive([2]):
          pytest.fail("scope adopted durable freedrive")
      await scara.end_freedrive_mode()

    assert backend.events == [
      "backend.setup",
      "freedrive:[1]:{}",
      "end:{}",
      "backend.stop",
    ]

  async def test_failed_scope_entry_force_exits_before_propagating(self) -> None:
    backend = RecordingSCARABackend()
    backend.start_error = RuntimeError("entry failed")
    scara = ExperimentalSCARA(backend)

    async with scara:
      with pytest.raises(RuntimeError, match="entry failed"):
        async with scara.freedrive([1]):
          pytest.fail("failed entry reached body")

    assert backend.events == [
      "backend.setup",
      "freedrive:[1]:{}",
      "end:{}",
      "backend.stop",
    ]

  async def test_parent_cleanup_exits_and_invalidates_live_scope(self) -> None:
    backend = RecordingSCARABackend()
    scara = ExperimentalSCARA(backend)
    lifespan = scara.lifespan()
    scope = scara.freedrive([0])

    await lifespan.__aenter__()
    await scope.__aenter__()
    await lifespan.__aexit__(None, None, None)
    assert scara._freedrive_scope_token is None
    end_count = backend.events.count("end:{}")
    await scope.__aexit__(None, None, None)

    assert backend.events == [
      "backend.setup",
      "freedrive:[0]:{}",
      "end:{}",
      "backend.stop",
    ]
    assert backend.events.count("end:{}") == end_count

  async def test_sequential_scope_reuse_runs_complete_cycles(self) -> None:
    backend = RecordingSCARABackend()
    scara = ExperimentalSCARA(backend)

    async with scara:
      async with scara.freedrive([1]):
        pass
      async with scara.freedrive([2]):
        pass

    assert backend.events == [
      "backend.setup",
      "freedrive:[1]:{}",
      "end:{}",
      "freedrive:[2]:{}",
      "end:{}",
      "backend.stop",
    ]
