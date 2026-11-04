"""Software-only ownership checks for the two Gemini serial modes."""

from typing import Any

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.testing.concurrency import AnyioTestBase

from .file_transfer_control import _FileTransferControl
from .gemini_x2 import BTXGeminiX2
from .the_ghost_touch import _RSITransport


class _SerialDouble(HasLifespan):
  def __init__(self, events: list[str], *, fail_open: bool = False) -> None:
    super().__init__()
    self.events = events
    self.fail_open = fail_open
    self.port = "/dev/software-only"
    self.writing = anyio.Event()
    self.finish_write = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.events.append("open")
    stack.callback(self.events.append, "close")
    if self.fail_open:
      raise RuntimeError("open failed")

  async def write(self, data: bytes) -> None:
    self.events.append(data.decode("ascii").strip())
    self.writing.set()
    await self.finish_write.wait()

  async def read(self, num_bytes: int = 1) -> bytes:
    self.events.append("reply")
    return b"reply\n:"

  async def reset_input_buffer(self) -> None:
    pass


class TestGeminiOwnership(AnyioTestBase):
  async def test_pre_dispatch_cancellation_does_not_send_pm_command(self) -> None:
    events: list[str] = []
    serial = _SerialDouble(events)
    control = _FileTransferControl(port="/dev/software-only", serial_io=serial)
    async with control:
      with anyio.CancelScope() as scope:
        scope.cancel()
        await control.request_version()
    assert events == ["open", "close"]

  async def test_file_transfer_closes_on_failed_entry_and_reuses(self) -> None:
    events: list[str] = []
    serial = _SerialDouble(events, fail_open=True)
    control = _FileTransferControl(port="/dev/software-only", serial_io=serial)
    try:
      async with control:
        pass
    except RuntimeError as exc:
      assert str(exc) == "open failed"
    assert events == ["open", "close"]
    serial.fail_open = False
    async with control:
      assert control.port == "/dev/software-only"
    assert events == ["open", "close", "open", "close"]

  async def test_committed_pm_command_finishes_before_close_on_cancel(self) -> None:
    events: list[str] = []
    serial = _SerialDouble(events)
    control = _FileTransferControl(port="/dev/software-only", serial_io=serial)
    async with control:
      with anyio.CancelScope() as scope:
        async with anyio.create_task_group() as group:

          async def command() -> None:
            await control.request_version()

          group.start_soon(command)
          await serial.writing.wait()
          scope.cancel()
          serial.finish_write.set()
    assert events == ["open", "version", "reply", "close"]

  async def test_rsi_transport_closes_on_entry_failure(self) -> None:
    events: list[str] = []
    transport = _RSITransport(
      port="/dev/software-only",
      baud=115200,
      timeout=0.2,
      retries=1,
      serial_io=_SerialDouble(events, fail_open=True),
    )
    try:
      async with transport:
        pass
    except RuntimeError as exc:
      assert str(exc) == "open failed"
    assert events == ["open", "close"]

  async def test_parent_closes_file_transfer_after_validation_failure(self) -> None:
    events: list[str] = []
    serial = _SerialDouble(events)
    serial.finish_write.set()
    gemini = BTXGeminiX2(port="/dev/software-only")
    gemini._file_transfer_control = _FileTransferControl(port=serial.port, serial_io=serial)
    try:
      async with gemini:
        pass
    except RuntimeError as exc:
      assert "not safe" in str(exc) or "no value" in str(exc)
    assert events[-1] == "close"
