import anyio
import pytest

from pylabrobot.hamilton.star.lock import _FirmwareLock
from pylabrobot.testing.concurrency import AnyioTestBase


async def _assert_modules_serialize(first_module: str, second_module: str) -> None:
  """Assert that the second module cannot enter until the first module exits."""
  gate = _FirmwareLock()
  first_entered = anyio.Event()
  second_started = anyio.Event()
  second_entered = anyio.Event()
  release_first = anyio.Event()

  async def first() -> None:
    async with gate.slave_module(first_module):
      first_entered.set()
      await release_first.wait()

  async def second() -> None:
    second_started.set()
    async with gate.slave_module(second_module):
      second_entered.set()

  async with anyio.create_task_group() as task_group:
    task_group.start_soon(first)
    await first_entered.wait()
    task_group.start_soon(second)
    await second_started.wait()
    await anyio.lowlevel.checkpoint()
    assert not second_entered.is_set()
    release_first.set()
    await second_entered.wait()


async def _assert_c0_waits_for_slave(gate: _FirmwareLock) -> None:
  """Assert that a C0 command cannot overlap an active slave command."""
  slave_entered = anyio.Event()
  c0_started = anyio.Event()
  c0_entered = anyio.Event()
  release_slave = anyio.Event()

  async def slave() -> None:
    async with gate.slave_module("X0"):
      slave_entered.set()
      await release_slave.wait()

  async def c0() -> None:
    c0_started.set()
    async with gate.c0():
      c0_entered.set()

  async with anyio.create_task_group() as task_group:
    task_group.start_soon(slave)
    await slave_entered.wait()
    task_group.start_soon(c0)
    await c0_started.wait()
    await anyio.lowlevel.checkpoint()
    assert not c0_entered.is_set()
    release_slave.set()
    await c0_entered.wait()


class TestFirmwareLock(AnyioTestBase):
  """Verify Hamilton firmware synchronization under every supported AnyIO backend."""

  async def test_same_module_commands_serialize(self) -> None:
    await _assert_modules_serialize("H0", "H0")

  async def test_pipetting_channels_share_one_lock(self) -> None:
    await _assert_modules_serialize("P1", "PG")

  async def test_different_slave_modules_overlap(self) -> None:
    gate = _FirmwareLock()
    first_entered = anyio.Event()
    second_entered = anyio.Event()
    release_first = anyio.Event()

    async def first() -> None:
      async with gate.slave_module("H0"):
        first_entered.set()
        await release_first.wait()

    async def second() -> None:
      async with gate.slave_module("X0"):
        second_entered.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(first)
      await first_entered.wait()
      task_group.start_soon(second)
      with anyio.fail_after(1):
        await second_entered.wait()
      release_first.set()

  async def test_c0_excludes_all_slave_modules(self) -> None:
    gate = _FirmwareLock()
    slave_entered = [anyio.Event(), anyio.Event()]
    release_slaves = anyio.Event()
    c0_started = anyio.Event()
    c0_entered = anyio.Event()

    async def slave(module: str, entered: anyio.Event) -> None:
      async with gate.slave_module(module):
        entered.set()
        await release_slaves.wait()

    async def c0() -> None:
      c0_started.set()
      async with gate.c0():
        c0_entered.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(slave, "H0", slave_entered[0])
      task_group.start_soon(slave, "X0", slave_entered[1])
      await slave_entered[0].wait()
      await slave_entered[1].wait()
      task_group.start_soon(c0)
      await c0_started.wait()
      await anyio.lowlevel.checkpoint()
      assert not c0_entered.is_set()
      release_slaves.set()
      await c0_entered.wait()

  async def test_body_failure_releases_all_state(self) -> None:
    gate = _FirmwareLock()
    body_error = RuntimeError("command failed")

    with pytest.raises(RuntimeError) as exc_info:
      async with gate.slave_module("H0"):
        raise body_error

    assert exc_info.value is body_error
    with anyio.fail_after(1):
      async with gate.c0():
        pass
      async with gate.slave_module("H0"):
        pass

  async def test_cancelled_first_slave_waiter_rolls_back_count(self) -> None:
    gate = _FirmwareLock()
    scopes: list[anyio.CancelScope] = []
    waiter_started = anyio.Event()
    waiter_finished = anyio.Event()

    async def wait_for_slave() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        waiter_started.set()
        async with gate.slave_module("H0"):
          pytest.fail("cancelled slave waiter entered the gate")
      waiter_finished.set()

    async with gate.c0():
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(wait_for_slave)
        await waiter_started.wait()
        await anyio.lowlevel.checkpoint()
        scopes[0].cancel()
        await waiter_finished.wait()

    await _assert_c0_waits_for_slave(gate)

  async def test_cancelled_cleanup_does_not_poison_gate(self) -> None:
    gate = _FirmwareLock()
    scopes: list[anyio.CancelScope] = []
    slave_entered = anyio.Event()
    slave_finished = anyio.Event()

    async def slave() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with gate.slave_module("H0"):
          slave_entered.set()
          await anyio.sleep_forever()
      slave_finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(slave)
      await slave_entered.wait()
      await gate._slave_count_lock.acquire()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      gate._slave_count_lock.release()
      await slave_finished.wait()

    with anyio.fail_after(1):
      async with gate.c0():
        pass

  async def test_cancelled_c0_waiter_does_not_poison_gate(self) -> None:
    gate = _FirmwareLock()
    scopes: list[anyio.CancelScope] = []
    slave_entered = anyio.Event()
    release_slave = anyio.Event()
    c0_started = anyio.Event()
    c0_finished = anyio.Event()

    async def slave() -> None:
      async with gate.slave_module("H0"):
        slave_entered.set()
        await release_slave.wait()

    async def c0() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        c0_started.set()
        async with gate.c0():
          pytest.fail("cancelled C0 waiter entered the gate")
      c0_finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(slave)
      await slave_entered.wait()
      task_group.start_soon(c0)
      await c0_started.wait()
      await anyio.lowlevel.checkpoint()
      scopes[0].cancel()
      await c0_finished.wait()
      release_slave.set()

    with anyio.fail_after(1):
      async with gate.c0():
        pass

  async def test_gate_supports_sequential_reuse(self) -> None:
    gate = _FirmwareLock()
    entries: list[str] = []

    for _ in range(2):
      async with gate.slave_module("R0"):
        entries.append("R0")
      async with gate.c0():
        entries.append("C0")

    assert entries == ["R0", "C0", "R0", "C0"]
