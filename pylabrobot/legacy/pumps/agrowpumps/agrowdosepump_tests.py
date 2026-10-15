from typing import Any, Optional, Union, cast

import anyio
import pytest

from pylabrobot.agrowpumps import AgrowPumpArray as NativeAgrowPumpArray
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.pumps.agrowpumps import AgrowPumpArray, AgrowPumpArrayBackend
from pylabrobot.testing.concurrency import AnyioTestBase


class FakeNativeAgrowPumpArray(HasLifespan):
  """Software-only native Agrow owner used by the compatibility tests."""

  instances: list["FakeNativeAgrowPumpArray"] = []
  enter_error: Optional[BaseException] = None
  cleanup_error: Optional[BaseException] = None

  def __init__(self, port: str, address: Union[int, str]) -> None:
    super().__init__()
    self.port = port
    self.address = int(address)
    self.events: list[str] = []
    self.writes: list[tuple[int, int]] = []
    self._pump_index_to_address = {0: 100, 1: 101}
    self._num_channels = 2
    self.modbus = object()
    type(self).instances.append(self)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeNativeAgrowPumpArray does not accept lifespan options")
    self.events.append("native.enter")
    if self.enter_error is not None:
      raise self.enter_error
    stack.push_shielded_async_callback(self._zero_and_close)

  async def _zero_and_close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("native.zero")
    self.events.append("native.close")
    if self.cleanup_error is not None:
      raise self.cleanup_error

  @property
  def pump_index_to_address(self) -> dict[int, int]:
    return self._pump_index_to_address

  @property
  def num_channels(self) -> int:
    return self._num_channels

  async def write_speed(self, channel: int, speed: int) -> None:
    if speed not in range(101):
      raise ValueError("Pump speed out of range. Value should be between 0 and 100.")
    self.writes.append((channel, speed))


class BackendWithFakeNativeOwner(AgrowPumpArrayBackend):
  """Legacy backend wired to the software-only native owner."""

  def _create_driver(self, port: str, address: int) -> NativeAgrowPumpArray:
    return cast(NativeAgrowPumpArray, FakeNativeAgrowPumpArray(port=port, address=address))


class TestAgrowPumps(AnyioTestBase):
  """Compatibility and lifecycle tests for the legacy Agrow backend."""

  _anyio_backends = ["asyncio"]

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    FakeNativeAgrowPumpArray.instances.clear()
    FakeNativeAgrowPumpArray.enter_error = None
    FakeNativeAgrowPumpArray.cleanup_error = None

  def test_uses_managed_lifecycle_methods(self) -> None:
    assert AgrowPumpArrayBackend.setup is HasLifespan.setup
    assert AgrowPumpArrayBackend.stop is HasLifespan.stop

  async def test_legacy_backend_enters_exactly_one_native_owner(self) -> None:
    backend = BackendWithFakeNativeOwner(port="simulated", address=1)
    async with backend:
      assert backend.modbus is FakeNativeAgrowPumpArray.instances[0].modbus
      assert backend.num_channels == 2
      assert backend.pump_index_to_address == {0: 100, 1: 101}
    owner = FakeNativeAgrowPumpArray.instances[0]
    assert len(FakeNativeAgrowPumpArray.instances) == 1
    assert owner.events == ["native.enter", "native.zero", "native.close"]

  async def test_operations_delegate_without_duplicate_modbus_ownership(self) -> None:
    backend = BackendWithFakeNativeOwner(port="simulated", address=1)
    async with backend:
      await backend.run_continuously([1.9, 100], [0, 1])
      await backend.halt()
    owner = FakeNativeAgrowPumpArray.instances[-1]
    assert owner.writes == [(0, 1), (1, 100), (0, 0), (1, 0)]
    assert owner.events == ["native.enter", "native.zero", "native.close"]

  async def test_native_entry_failure_leaves_backend_inactive(self) -> None:
    failure = RuntimeError("native entry failed")
    FakeNativeAgrowPumpArray.enter_error = failure
    backend = BackendWithFakeNativeOwner(port="simulated", address=1)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed native entry reached the backend body")

    assert error_info.value is failure
    assert not backend.is_active
    assert not backend._driver.is_active
    assert FakeNativeAgrowPumpArray.instances[0].events == ["native.enter"]

  async def test_body_cancellation_exits_native_owner_before_propagating(self) -> None:
    backend = BackendWithFakeNativeOwner(port="simulated", address=1)
    body_started = anyio.Event()
    events = FakeNativeAgrowPumpArray.instances[0].events

    async def use_backend() -> None:
      try:
        async with backend:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_backend)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-3:] == ["native.zero", "native.close", "cancelled"]

  async def test_sequential_reuse_reenters_same_native_owner(self) -> None:
    backend = BackendWithFakeNativeOwner(port="simulated", address=1)

    async with backend:
      pass
    async with backend:
      pass

    assert len(FakeNativeAgrowPumpArray.instances) == 1
    assert FakeNativeAgrowPumpArray.instances[0].events == [
      "native.enter",
      "native.zero",
      "native.close",
      "native.enter",
      "native.zero",
      "native.close",
    ]

  async def test_native_cleanup_failure_propagates(self) -> None:
    failure = RuntimeError("native cleanup failed")
    FakeNativeAgrowPumpArray.cleanup_error = failure
    backend = BackendWithFakeNativeOwner(port="simulated", address=1)

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pass

    assert error_info.value is failure
    assert not backend.is_active
    assert FakeNativeAgrowPumpArray.instances[0].events[-2:] == ["native.zero", "native.close"]

  async def test_speed_validation_and_revolutions_behavior_are_preserved(self) -> None:
    backend = BackendWithFakeNativeOwner(port="simulated", address=1)

    async with backend:
      with pytest.raises(ValueError, match="between 0 and 100"):
        await backend.run_continuously([101], [0])
      with pytest.raises(ValueError, match="between 0 and 100"):
        await backend.run_continuously([-1], [0])
      with pytest.raises(NotImplementedError, match="not available for this pump array"):
        await backend.run_revolutions([1.0], [0])

  def test_serialization_shape_is_preserved(self) -> None:
    backend = AgrowPumpArrayBackend(port="simulated", address=1)

    assert backend.serialize() == {
      "type": "AgrowPumpArrayBackend",
      "port": "simulated",
      "address": 1,
    }
    with pytest.raises(RuntimeError, match="deprecated"):
      AgrowPumpArray()


class TestLegacyAgrowBackendGuard(AnyioTestBase):
  """The delegated native owner rejects unsupported async backends."""

  _anyio_backends = ["trio"]

  async def test_trio_is_rejected_by_native_owner_before_modbus_construction(self) -> None:
    backend = AgrowPumpArrayBackend(port="simulated", address=1)
    with pytest.raises(RuntimeError, match="requires the asyncio backend"):
      async with backend:
        pytest.fail("Trio reached the backend body")
    assert backend._driver._modbus is None
