"""Lifecycle tests for the legacy BioTek EL406 backend."""

from typing import Any, Optional, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.ftdi import FTDI
from pylabrobot.legacy.plate_washing.biotek.el406 import ExperimentalBioTekEL406Backend
from pylabrobot.legacy.plate_washing.biotek.el406.mock_tests import MockFTDI
from pylabrobot.testing.concurrency import AnyioTestBase


class RecordingFTDI(MockFTDI):
  """Record EL406 setup and cleanup while supporting failures and cancellation."""

  def __init__(
    self,
    events: list[str],
    *,
    fail_at: Optional[str] = None,
    block_at: Optional[str] = None,
    blocked: Optional[anyio.Event] = None,
  ) -> None:
    super().__init__()
    self.events = events
    self.fail_at = fail_at
    self.block_at = block_at
    self.blocked = blocked

  async def _record(self, event: str) -> None:
    self.events.append(event)
    if event == self.fail_at:
      raise RuntimeError(f"failed at {event}")
    if event == self.block_at:
      if self.blocked is not None:
        self.blocked.set()
      await anyio.sleep_forever()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.events.append("ftdi.enter")
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("ftdi.exit")

  async def set_baudrate(self, baudrate: int) -> None:
    await self._record(f"baud:{baudrate}")

  async def set_line_property(self, bits: int, stopbits: int, parity: int) -> None:
    await self._record(f"line:{bits},{stopbits},{parity}")

  async def set_flowctrl(self, flowctrl: int) -> None:
    await self._record(f"flow:{flowctrl}")

  async def set_rts(self, level: bool) -> None:
    await self._record(f"rts:{level}")

  async def set_dtr(self, level: bool) -> None:
    await self._record(f"dtr:{level}")

  async def usb_purge_rx_buffer(self) -> None:
    await self._record("purge.rx")

  async def usb_purge_tx_buffer(self) -> None:
    await self._record("purge.tx")

  async def write(self, data: bytes) -> int:
    if len(data) == 11:
      command = int.from_bytes(data[2:4], "little")
      if command == 0x73:
        await self._record("communication")
      elif command == 0x70:
        await self._record("reset")
    return await super().write(data)


class RecordingBackend(ExperimentalBioTekEL406Backend):
  """Expose command-lock cleanup ordering through a test event transcript."""

  def __init__(self, events: list[str], **kwargs: Any) -> None:
    super().__init__(**kwargs)
    self._events = events

  def _clear_command_lock(self) -> None:
    self._events.append("lock.clear")
    super()._clear_command_lock()


def make_backend(
  *,
  fail_at: Optional[str] = None,
  block_at: Optional[str] = None,
  blocked: Optional[anyio.Event] = None,
) -> tuple[ExperimentalBioTekEL406Backend, RecordingFTDI, list[str]]:
  """Construct an EL406 whose external FTDI dependency is a software double."""
  events: list[str] = []
  backend = ExperimentalBioTekEL406Backend(timeout=0.01)
  io = RecordingFTDI(events, fail_at=fail_at, block_at=block_at, blocked=blocked)
  backend.io = cast(FTDI, io)
  return backend, io, events


class TestEL406Lifecycle(AnyioTestBase):
  def test_inherits_managed_methods(self) -> None:
    assert ExperimentalBioTekEL406Backend.setup is HasLifespan.setup
    assert ExperimentalBioTekEL406Backend.stop is HasLifespan.stop
    assert ExperimentalBioTekEL406Backend.__aenter__ is HasLifespan.__aenter__
    assert ExperimentalBioTekEL406Backend.__aexit__ is HasLifespan.__aexit__

  async def test_rejects_unknown_options_before_ftdi_entry(self) -> None:
    backend, io, events = make_backend()

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid lifespan entered body")

    assert events == []
    assert not io.is_active

  async def test_rejects_non_boolean_skip_reset(self) -> None:
    backend, io, events = make_backend()

    with pytest.raises(TypeError, match="skip_reset must be a bool"):
      async with backend.lifespan(skip_reset=1):
        pytest.fail("invalid lifespan entered body")

    assert events == []
    assert not io.is_active

  async def test_configuration_failure_closes_ftdi(self) -> None:
    backend, io, events = make_backend(fail_at="baud:38400")

    with pytest.raises(Exception, match="failed at baud:38400"):
      async with backend.lifespan(skip_reset=True):
        pytest.fail("failed configuration entered body")

    assert events == ["ftdi.enter", "baud:38400", "ftdi.exit"]
    assert not io.is_active

  async def test_communication_failure_closes_ftdi(self) -> None:
    backend, io, events = make_backend(fail_at="communication")

    with pytest.raises(Exception, match="failed at communication"):
      async with backend.lifespan(skip_reset=True):
        pytest.fail("failed communication entered body")

    assert events[-2:] == ["communication", "ftdi.exit"]
    assert not io.is_active

  async def test_reset_failure_closes_ftdi(self) -> None:
    backend, io, events = make_backend(fail_at="reset")

    with pytest.raises(Exception, match="failed at reset"):
      async with backend:
        pytest.fail("failed reset entered body")

    assert events.index("communication") < events.index("reset")
    assert events[-2:] == ["reset", "ftdi.exit"]
    assert not io.is_active

  async def test_cancelled_entry_closes_ftdi(self) -> None:
    blocked = anyio.Event()
    backend, io, events = make_backend(block_at="baud:38400", blocked=blocked)
    scopes: list[anyio.CancelScope] = []

    async def enter_backend() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend.lifespan(skip_reset=True):
          pytest.fail("cancelled entry reached body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_backend)
      await blocked.wait()
      scopes[0].cancel()

    assert events[-2:] == ["baud:38400", "ftdi.exit"]
    assert not backend.is_active
    assert not io.is_active

  async def test_body_cancellation_closes_ftdi(self) -> None:
    backend, io, events = make_backend()

    with anyio.CancelScope() as scope:
      async with backend.lifespan(skip_reset=True):
        scope.cancel()
        await anyio.lowlevel.checkpoint()

    assert events[-1] == "ftdi.exit"
    assert not backend.is_active
    assert not io.is_active

  async def test_command_lock_cleared_before_ftdi_close(self) -> None:
    events: list[str] = []
    backend = RecordingBackend(events, timeout=0.01)
    io = RecordingFTDI(events)
    backend.io = cast(FTDI, io)

    async with backend.lifespan(skip_reset=True):
      assert events[:2] == ["ftdi.enter", "baud:38400"]
      assert events[:6] == [
        "ftdi.enter",
        "baud:38400",
        "line:8,2,0",
        "flow:0",
        "rts:True",
        "dtr:True",
      ]
      assert events.index("purge.rx") < events.index("communication")
      backend._in_batch = True

    assert events[-2:] == ["lock.clear", "ftdi.exit"]
    assert backend._command_lock is None
    assert not backend._in_batch

  async def test_sequential_reuse_reinitializes_lock(self) -> None:
    backend, io, _ = make_backend()

    async with backend.lifespan(skip_reset=True):
      first_lock = backend._command_lock
      assert first_lock is not None

    assert backend._command_lock is None
    async with backend.lifespan(skip_reset=True):
      second_lock = backend._command_lock
      assert second_lock is not None

    assert first_lock is not second_lock
    assert backend._command_lock is None
    assert not io.is_active
