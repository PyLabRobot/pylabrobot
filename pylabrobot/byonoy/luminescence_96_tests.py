import struct
from typing import Sequence, cast

import anyio
import pytest

from pylabrobot.byonoy import ByonoyMeasurementCancelledError
from pylabrobot.byonoy.driver_tests import FakeHID, ReadEffect, report_ids, status_report
from pylabrobot.byonoy.luminescence_96 import (
  ByonoyLuminescence96,
  byonoy_l96_base_unit,
  byonoy_l96_reader_unit,
)
from pylabrobot.concurrency import HasLifespan
from pylabrobot.io.hid import HID
from pylabrobot.resources import cellvis_96_wellplate_350uL_Fb
from pylabrobot.testing.concurrency import AnyioTestBase


def luminescence_chunk(
  seq: int,
  seq_len: int,
  values: list[float],
  *,
  flags: int = 0,
) -> bytes:
  return struct.pack("<HBBII12fBB", 0x0600, seq, seq_len, 100000, 25, *values, flags, 100).ljust(
    64, b"\x00"
  )


def make_reader(
  replies: Sequence[ReadEffect] | None = None,
) -> tuple[ByonoyLuminescence96, FakeHID]:
  reader = byonoy_l96_reader_unit("reader")
  fake = FakeHID(replies=replies)
  reader.io = cast(HID, fake)
  return reader, fake


class TestByonoyLuminescenceProtocol(AnyioTestBase):
  def test_passive_base_and_active_reader_keep_resource_ownership(self) -> None:
    base = byonoy_l96_base_unit("base")
    reader, _ = make_reader()

    assert not isinstance(base, HasLifespan)
    assert isinstance(reader, HasLifespan)
    assert reader.name == "reader"
    assert reader.get_size_x() == 139.7
    assert reader.get_size_y() == 97.5
    assert reader.get_size_z() == 35

  async def test_modes_integration_mask_chunk_order_and_multiple_planes(self) -> None:
    rows = [[float(row * 12 + col) for col in range(12)] for row in range(16)]
    replies = [luminescence_chunk(seq, 16, rows[seq]) for seq in reversed(range(16))]
    replies.append(status_report())
    reader, fake = make_reader(replies)
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")

    result = await reader.read_luminescence(plate, plate["A1"] + plate["H12"], mode="rapid")

    trigger = fake.writes[2]
    assert report_ids(fake.writes) == [0x0010, 0x0200, 0x0340, 0x0300]
    assert all(timeout is None for _, timeout in fake.reads)
    assert trigger[2:6] == struct.pack("<i", 100000)
    assert trigger[6:18] == b"\x01" + b"\x00" * 10 + b"\x80"
    assert result[0].data[0][0] == 0.0
    assert result[0].data[7][11] == 95.0
    assert result[0].data[0][1] is None

  async def test_explicit_integration_forces_custom_mode(self) -> None:
    replies = [luminescence_chunk(seq, 8, [0.0] * 12) for seq in range(8)] + [status_report()]
    reader, fake = make_reader(replies)
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")

    await reader.read_luminescence(plate, plate.get_all_items(), integration_time=0.25)

    assert fake.writes[2][2:6] == struct.pack("<i", 250000)

  async def test_custom_mode_requires_integration_time(self) -> None:
    reader, fake = make_reader()
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")

    with pytest.raises(ValueError, match="requires integration_time"):
      await reader.read_luminescence(plate, plate.get_all_items(), mode="custom")

    assert fake.writes == []

  async def test_nonzero_flags_are_in_firmware_error(self) -> None:
    replies = [luminescence_chunk(seq, 8, [0.0] * 12, flags=0x44) for seq in range(8)]
    replies.append(status_report(7))
    reader, _ = make_reader(replies)
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")

    with pytest.raises(RuntimeError, match="errorCode=0x07.*0x44"):
      await reader.read_luminescence(plate, plate.get_all_items())

  @pytest.mark.parametrize(
    "chunks, message",
    [
      ([luminescence_chunk(0, 0, [0.0] * 12)], "seq_len=0"),
      (
        [luminescence_chunk(0, 2, [0.0] * 12), luminescence_chunk(1, 3, [0.0] * 12)],
        "changed seq_len",
      ),
      ([luminescence_chunk(2, 2, [0.0] * 12)], "sent seq=2"),
      ([luminescence_chunk(0, 1, [0.0] * 12)], "positive multiple of 96"),
    ],
  )
  async def test_malformed_results_raise(self, chunks: list[bytes], message: str) -> None:
    reader, _ = make_reader(chunks + ([status_report()] if "positive" in message else []))
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")

    with pytest.raises(RuntimeError, match=message):
      await reader.read_luminescence(plate, plate.get_all_items())

  async def test_explicit_cancel_interrupts_blocked_read_and_raises_measurement_cancelled_error(
    self,
  ) -> None:
    read_started = anyio.Event()
    release_read = anyio.Event()

    async def blocked_read() -> bytes:
      read_started.set()
      await release_read.wait()
      return b""

    reader, fake = make_reader()
    fake.replies.append(blocked_read)
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")
    cancelled = anyio.Event()

    async def measure() -> None:
      with pytest.raises(ByonoyMeasurementCancelledError):
        await reader.read_luminescence(plate, plate.get_all_items())
      cancelled.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(measure)
      await read_started.wait()
      try:
        await reader.cancel()
        with anyio.fail_after(1):
          await cancelled.wait()
      finally:
        release_read.set()

    assert report_ids(fake.writes)[-1] == 0x0060
    assert fake.writes[-1][2:4] == b"\x40\x03"

  async def test_measurement_deadline_aborts_incomplete_read(self) -> None:
    reader, fake = make_reader()
    reader._measurement_read_timeout = 0.01
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")

    with anyio.fail_after(1):
      with pytest.raises(TimeoutError, match="Reading luminescence data timed out"):
        await reader.read_luminescence(plate, plate.get_all_items())

    assert report_ids(fake.writes) == [0x0010, 0x0200, 0x0340, 0x0060]

  async def test_pre_trigger_cancel_does_not_trigger_or_abort(self) -> None:
    preflight_started = anyio.Event()
    release_preflight = anyio.Event()

    async def block_preflight(data: bytes) -> None:
      if int.from_bytes(data[:2], "little") == 0x0010:
        preflight_started.set()
        await release_preflight.wait()

    reader, fake = make_reader()
    fake.write_effect = block_preflight
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")
    cancelled = anyio.Event()

    async def measure() -> None:
      with pytest.raises(ByonoyMeasurementCancelledError):
        await reader.read_luminescence(plate, plate.get_all_items())
      cancelled.set()

    writes_before_release: list[int] = []
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(measure)
      await preflight_started.wait()
      await reader.cancel()
      writes_before_release.extend(report_ids(fake.writes))
      release_preflight.set()
      await cancelled.wait()

    assert writes_before_release == [0x0010]
    assert report_ids(fake.writes) == [0x0010]

  async def test_explicit_cancel_during_final_status_does_not_return_data(self) -> None:
    status_started = anyio.Event()
    release_status = anyio.Event()
    abort_started = anyio.Event()
    release_abort = anyio.Event()

    async def blocked_status() -> bytes:
      status_started.set()
      await release_status.wait()
      return status_report()

    chunks = [luminescence_chunk(seq, 8, [0.0] * 12) for seq in range(8)]
    reader, fake = make_reader(chunks + [blocked_status])

    async def blocked_abort(data: bytes) -> None:
      if int.from_bytes(data[:2], "little") == 0x0060:
        abort_started.set()
        await release_abort.wait()

    fake.write_effect = blocked_abort
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")
    finished = anyio.Event()

    async def measure() -> None:
      with pytest.raises(ByonoyMeasurementCancelledError):
        await reader.read_luminescence(plate, plate.get_all_items())
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(measure)
      await status_started.wait()
      task_group.start_soon(reader.cancel)
      await abort_started.wait()
      try:
        release_status.set()
        await anyio.lowlevel.checkpoint()
        assert not finished.is_set()
      finally:
        release_abort.set()
      with anyio.fail_after(1):
        await finished.wait()

    assert report_ids(fake.writes)[-1] == 0x0060

  async def test_external_cancellation_aborts_after_trigger_before_hid_close(self) -> None:
    reader, fake = make_reader()
    trigger_committed = anyio.Event()
    release_trigger = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def block_trigger(data: bytes) -> None:
      if int.from_bytes(data[:2], "little") == 0x0340:
        trigger_committed.set()
        await release_trigger.wait()

    fake.write_effect = block_trigger
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")

    async with reader:

      async def measure() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await reader.read_luminescence(plate, plate.get_all_items())

      async with anyio.create_task_group() as task_group:
        task_group.start_soon(measure)
        await trigger_committed.wait()
        scopes[0].cancel()
        release_trigger.set()

    assert report_ids(fake.writes)[-1] == 0x0060
    assert fake.events.index("write.0060") < fake.events.index("hid.exit")
