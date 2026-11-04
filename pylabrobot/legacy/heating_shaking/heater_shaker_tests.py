import unittest
from typing import Any, Optional

import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.heating_shaking import HeaterShaker, HeaterShakerChatterboxBackend
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.testing import AnyioTestBase


class RecordingHeaterShakerBackend(HeaterShakerChatterboxBackend):
  def __init__(self) -> None:
    super().__init__()
    self.events: list[str] = []
    self.deactivate_error: Optional[BaseException] = None
    self.stop_shaking_error: Optional[BaseException] = None

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingHeaterShakerBackend does not accept lifespan options")
    self.events.append("setup")
    stack.push_shielded_async_callback(self._record_backend_stop)

  async def _record_backend_stop(self) -> None:
    self.events.append("backend_stop")

  async def lock_plate(self) -> None:
    self.events.append("lock")

  async def unlock_plate(self) -> None:
    assert self.is_active, "plate unlock must precede backend closure"
    self.events.append("unlock")

  async def start_shaking(self, speed: float) -> None:
    self.events.append("start_shaking")

  async def deactivate(self) -> None:
    self.events.append("deactivate")
    if self.deactivate_error is not None:
      raise self.deactivate_error

  async def stop_shaking(self) -> None:
    self.events.append("stop_shaking")
    if self.stop_shaking_error is not None:
      raise self.stop_shaking_error


def make_heater_shaker(backend: RecordingHeaterShakerBackend) -> HeaterShaker:
  return HeaterShaker(
    name="test_hs",
    size_x=10,
    size_y=10,
    size_z=10,
    backend=backend,
    child_location=Coordinate.zero(),
  )


class HeaterShakerTests(unittest.TestCase):
  def test_serialization(self):
    hs = HeaterShaker(
      name="test_hs",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=HeaterShakerChatterboxBackend(),
      child_location=Coordinate(0, 0, 0),
    )

    serialized = hs.serialize()
    deserialized = HeaterShaker.deserialize(serialized)
    self.assertEqual(hs, deserialized)


class TestHeaterShakerCleanup(AnyioTestBase):
  @pytest.mark.parametrize("stop_fails", [False, True])
  async def test_parent_exit_unlocks_inherited_live_scope_even_if_stop_fails(
    self, stop_fails: bool
  ) -> None:
    backend = RecordingHeaterShakerBackend()
    stop_error = RuntimeError("stop shaking failed")
    if stop_fails:
      backend.stop_shaking_error = stop_error
    heater_shaker = make_heater_shaker(backend)
    lifespan = heater_shaker.lifespan()
    await lifespan.__aenter__()
    scope = heater_shaker.shaking(900)
    await scope.__aenter__()

    if stop_fails:
      with pytest.raises(RuntimeError) as error_info:
        await lifespan.__aexit__(None, None, None)
      assert error_info.value is stop_error
      assert heater_shaker._shaking_state_unknown
    else:
      await lifespan.__aexit__(None, None, None)
    assert backend.events == [
      "setup",
      "lock",
      "start_shaking",
      "deactivate",
      "stop_shaking",
      "unlock",
      "backend_stop",
    ]
    await scope.__aexit__(None, None, None)
    assert backend.events.count("unlock") == 1
    assert backend.events.count("stop_shaking") == 1

  async def test_temperature_failure_still_attempts_shaking_cleanup(self) -> None:
    backend = RecordingHeaterShakerBackend()
    backend.deactivate_error = RuntimeError("deactivate failed")
    heater_shaker = make_heater_shaker(backend)

    with pytest.raises(RuntimeError, match="deactivate failed"):
      async with heater_shaker:
        await heater_shaker.shake(900)

    assert backend.events == [
      "setup",
      "lock",
      "start_shaking",
      "deactivate",
      "stop_shaking",
      "backend_stop",
    ]

  async def test_cleanup_failures_chain_in_attempt_order(self) -> None:
    deactivate_error = RuntimeError("deactivate failed")
    stop_shaking_error = RuntimeError("stop shaking failed")
    backend = RecordingHeaterShakerBackend()
    backend.deactivate_error = deactivate_error
    backend.stop_shaking_error = stop_shaking_error
    heater_shaker = make_heater_shaker(backend)

    with pytest.raises(RuntimeError, match="stop shaking failed") as error_info:
      async with heater_shaker:
        await heater_shaker.shake(900)

    assert error_info.value is stop_shaking_error
    assert error_info.value.__context__ is deactivate_error
    assert backend.events == [
      "setup",
      "lock",
      "start_shaking",
      "deactivate",
      "stop_shaking",
      "backend_stop",
    ]
