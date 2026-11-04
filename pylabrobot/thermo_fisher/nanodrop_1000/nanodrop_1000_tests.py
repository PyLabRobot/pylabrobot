"""Software-only NanoDrop USB lifecycle and protocol regressions."""

from typing import Any, List, Optional, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.usb import USB
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.thermo_fisher.nanodrop_1000 import ThermoFisherNanoDrop1000


class FakeUSB(HasLifespan):
  """Record wire activity and expose deterministic write/read interruption points."""

  def __init__(self) -> None:
    super().__init__()
    self.events: List[object] = []
    self.open = False
    self.fail_write: Optional[bytes] = None
    self.fail_write_after = 0
    self.fail_close = False
    self.block_write: Optional[bytes] = None
    self.written = anyio.Event()
    self.release = anyio.Event()
    self.block_read = False
    self.reading = anyio.Event()
    self.heavy_delay = 0.0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.open = True
    self.events.append("open")
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    self.events.append("close")
    self.open = False
    if self.fail_close:
      raise ValueError("close failed")

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    await anyio.lowlevel.checkpoint()
    assert self.open
    self.events.append(data)
    if data == self.fail_write:
      if self.fail_write_after == 0:
        raise ValueError("write failed")
      self.fail_write_after -= 1
    if data == self.block_write:
      self.written.set()
      await self.release.wait()

  async def read(
    self, timeout: Optional[int] = None, size: Optional[int] = None, endpoint: Optional[int] = None
  ) -> bytes:
    await anyio.lowlevel.checkpoint()
    assert self.open
    self.events.append(("read", endpoint, size, timeout))
    if self.block_read:
      self.reading.set()
      await self.release.wait()
    if endpoint == 0x82:
      await anyio.sleep(self.heavy_delay)
      return bytes(64)
    return b"\x00\x00" + b"1.25\x00"

  async def drain(
    self,
    endpoint: Optional[int] = None,
    timeout: float = 0.05,
    size: int = 512,
    max_duration: float = 1,
  ) -> None:
    await anyio.lowlevel.checkpoint()
    assert self.open
    self.events.append(("drain", endpoint, size, timeout))


def make_device() -> tuple[ThermoFisherNanoDrop1000, FakeUSB]:
  """Supply a software transport without opening a physical USB device."""
  device = ThermoFisherNanoDrop1000()
  fake = FakeUSB()
  device.io = cast(USB, fake)
  return device, fake


class TestNanoDrop(AnyioTestBase):
  """Run driver behavior on both supported async runtimes."""

  async def test_initialization_measurement_and_shutdown_bytes(self) -> None:
    device, io = make_device()
    async with device:
      assert device.coefficients[1] == 1.25
      assert len(device.coefficients) == 13
      assert len(await device.get_raw_spectrum()) == 2048
    assert io.events[:4] == ["open", b"\x08", b"\x01", ("drain", 0x87, 64, 0.05)]
    assert io.events.count(("read", None, 64, None)) == 13
    assert io.events.count(("read", 0x82, 64, None)) == 64
    assert io.events[-3:] == [b"\x03\x00", b"\x0f\x00", "close"]
    assert device.coefficients == {}

  async def test_spectrum_allows_packets_with_total_read_time_over_one_second(self) -> None:
    device, io = make_device()
    async with device:
      io.heavy_delay = 0.025
      assert await device.get_raw_spectrum() == [0.0] * 2048
      assert io.events.count(("read", 0x82, 64, None)) == 64

  async def test_partial_entry_and_reuse_close_transport(self) -> None:
    device, io = make_device()
    io.fail_write = b"\x01"
    with pytest.raises(ValueError, match="write failed"):
      async with device:
        pass
    assert io.events[-3:] == [b"\x03\x00", b"\x0f\x00", "close"]
    io.fail_write = None
    async with device:
      assert device.coefficients[14] == 1.25
    assert io.events.count("open") == io.events.count("close") == 2

  async def test_reentry_requires_new_blank_before_measurement(self) -> None:
    device, io = make_device()
    async with device:
      await device.take_blank()
    async with device:
      before = list(io.events)
      with pytest.raises(ValueError, match="take_blank"):
        await device.measure_absorbance()
      assert io.events == before

  async def test_failed_measurement_powers_down_while_lifespan_remains_open(self) -> None:
    device, io = make_device()
    async with device:
      await device.take_blank()
      io.fail_write = b"\x09"
      with pytest.raises(ValueError, match="write failed"):
        await device.measure_absorbance()
      assert io.open
      assert io.events[-2:] == [b"\x03\x00", b"\x0f\x00"]

  async def test_measurement_attempts_magnet_off_when_lamp_off_fails(self) -> None:
    device, io = make_device()
    async with device:
      await device.take_blank()
      io.fail_write = b"\x03\x00"
      try:
        with pytest.raises(ValueError, match="write failed"):
          await device.measure_absorbance()
        assert io.open
        assert io.events[-2:] == [b"\x03\x00", b"\x0f\x00"]
      finally:
        io.fail_write = None

  async def test_failed_blank_powers_down_while_lifespan_remains_open(self) -> None:
    device, io = make_device()
    async with device:
      io.fail_write = b"\x03\xff"
      with pytest.raises(ValueError, match="write failed"):
        await device.take_blank()
      assert io.open
      assert io.events[-2:] == [b"\x03\x00", b"\x0f\x00"]

  async def test_cancelled_blank_powers_down_while_lifespan_remains_open(self) -> None:
    device, io = make_device()
    async with device:
      io.block_write = b"\x03\xff"
      completed = []
      async with anyio.create_task_group() as group:
        scope = anyio.CancelScope()

        async def blank() -> None:
          with scope:
            await device.take_blank()
            completed.append("returned")

        group.start_soon(blank)
        await io.written.wait()
        scope.cancel()
        io.release.set()
      assert completed == []
      assert io.open
      assert io.events[-2:] == [b"\x03\x00", b"\x0f\x00"]

  async def test_blank_attempts_magnet_off_when_lamp_off_fails(self) -> None:
    device, io = make_device()
    async with device:
      io.fail_write = b"\x03\x00"
      io.fail_write_after = 1
      try:
        with pytest.raises(ValueError, match="write failed"):
          await device.take_blank()
        assert io.open
        assert io.events[-2:] == [b"\x03\x00", b"\x0f\x00"]
      finally:
        io.fail_write = None

  async def test_cleanup_attempts_both_commands_and_close_on_failure(self) -> None:
    device, io = make_device()
    with pytest.raises(ValueError, match="close failed"):
      async with device:
        io.fail_write = b"\x03\x00"
        io.fail_close = True
    assert io.events[-3:] == [b"\x03\x00", b"\x0f\x00", "close"]

  async def test_cancelled_committed_read_finishes_before_close(self) -> None:
    device, io = make_device()
    async with device:
      io.block_read = True
      completed = []
      async with anyio.create_task_group() as group:
        scope = anyio.CancelScope()

        async def request() -> None:
          with scope:
            await device.get_raw_spectrum()
            completed.append("returned")

        group.start_soon(request)
        await io.reading.wait()
        scope.cancel()
        await anyio.sleep(0)
        assert ("read", 0x82, 64, None) in io.events
        io.release.set()
      assert io.events.count(("read", 0x82, 64, None)) == 64
      assert completed == []

  async def test_cancelled_write_reconciles_before_next_command(self) -> None:
    device, io = make_device()
    async with device:
      io.block_write = b"\x03\xff"
      completed = []
      async with anyio.create_task_group() as group:
        scope = anyio.CancelScope()

        async def turn_on() -> None:
          with scope:
            await device.set_lamp(True)
            completed.append("returned")

        group.start_soon(turn_on)
        await io.written.wait()
        scope.cancel()
        io.release.set()
      assert b"\x03\xff" in io.events
      assert completed == []

  async def test_cancel_before_dispatch_does_not_write(self) -> None:
    device, io = make_device()
    async with device:
      before = list(io.events)
      with anyio.CancelScope() as scope:
        scope.cancel()
        await device.set_lamp(True)
      assert io.events == before

  async def test_command_waits_for_complete_spectrum_reply(self) -> None:
    device, io = make_device()
    async with device:
      io.block_read = True
      async with anyio.create_task_group() as group:
        group.start_soon(device.get_raw_spectrum)
        await io.reading.wait()
        group.start_soon(device.set_lamp, True)
        await anyio.sleep(0)
        assert b"\x03\xff" not in io.events
        io.release.set()
      assert io.events.index(b"\x03\xff") > max(
        index for index, event in enumerate(io.events) if event == ("read", 0x82, 64, None)
      )

  async def test_body_error_still_powers_down_and_closes(self) -> None:
    device, io = make_device()
    with pytest.raises(RuntimeError, match="body failed"):
      async with device:
        raise RuntimeError("body failed")
    assert io.events[-3:] == [b"\x03\x00", b"\x0f\x00", "close"]
