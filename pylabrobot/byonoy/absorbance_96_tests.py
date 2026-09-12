import struct
import unittest
from typing import Sequence, cast

import anyio
import pytest

from pylabrobot.byonoy import (
  ByonoyMeasurementCancelledError,
  byonoy_a96a_illumination_unit,
  byonoy_a96a_parking_unit,
)
from pylabrobot.byonoy.absorbance_96 import ByonoyAbsorbance96, ByonoyAbsorbanceBaseUnit
from pylabrobot.byonoy.driver_tests import FakeHID, ReadEffect, report_ids, status_report
from pylabrobot.concurrency import HasLifespan
from pylabrobot.io.hid import HID
from pylabrobot.resources import Coordinate, Lid, Plate, Well, cellvis_96_wellplate_350uL_Fb
from pylabrobot.testing.concurrency import AnyioTestBase


def absorbance_chunk(
  seq: int,
  seq_len: int,
  values: list[float],
  *,
  flags: int = 0,
) -> bytes:
  return struct.pack("<HBBhhI12fBB", 0x0500, seq, seq_len, 450, 0, 25, *values, flags, 100).ljust(
    64, b"\x00"
  )


def wavelengths_report(*wavelengths: int) -> bytes:
  values = list(wavelengths) + [0] * (30 - len(wavelengths))
  return struct.pack("<H30h", 0x0330, *values).ljust(64, b"\x00")


def make_reader(replies: Sequence[ReadEffect] | None = None) -> tuple[ByonoyAbsorbance96, FakeHID]:
  reader = ByonoyAbsorbance96()
  fake = FakeHID(replies=replies)
  reader.io = cast(HID, fake)
  return reader, fake


def complete_absorbance(values_by_row: list[list[float]], *, flags: int = 0) -> list[bytes]:
  return [
    absorbance_chunk(seq, len(values_by_row), values, flags=flags)
    for seq, values in enumerate(values_by_row)
  ]


class TestByonoyAbsorbanceProtocol(AnyioTestBase):
  def test_passive_base_and_active_reader_keep_resource_ownership(self) -> None:
    base = ByonoyAbsorbanceBaseUnit("base")
    reader, _ = make_reader()

    assert not isinstance(base, HasLifespan)
    assert isinstance(reader, ByonoyAbsorbanceBaseUnit)
    assert isinstance(reader, HasLifespan)
    assert reader.name == "byonoy_absorbance_96"
    assert reader.plate_holder.parent is reader
    assert reader.illumination_unit_holder.parent is reader

  async def test_entry_runs_reference_then_discovers_wavelengths_and_clears_them(self) -> None:
    rows = [[float(row * 12 + col) for col in range(12)] for row in range(8)]
    reader, fake = make_reader(
      complete_absorbance(rows) + [status_report(), wavelengths_report(450, 600)]
    )

    async with reader:
      assert reader.available_wavelengths == [450, 600]
      assert report_ids(fake.writes) == [0x0010, 0x0200, 0x0320, 0x0300, 0x0330]
      trigger = fake.writes[2]
      assert trigger[2:8] == struct.pack("<hhBB", 660, 0, 1, 0)
      assert trigger[-2:] == b"\x00\x40"

    assert reader.available_wavelengths == []

  async def test_out_of_order_chunks_are_ordered_and_unselected_wells_are_masked(self) -> None:
    rows = [[float(row * 12 + col) for col in range(12)] for row in range(8)]
    replies = [absorbance_chunk(seq, 8, rows[seq]) for seq in [7, 0, 2, 1, 3, 5, 4, 6]]
    replies.append(status_report())
    reader, fake = make_reader(replies)
    reader.available_wavelengths = [450]
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")

    result = await reader.read_absorbance(plate, plate["A1"] + plate["H12"], 450)

    assert result[0].data[0][0] == 0.0
    assert result[0].data[7][11] == 95.0
    assert result[0].data[0][1] is None
    assert report_ids(fake.writes) == [0x0010, 0x0200, 0x0320, 0x0300]
    assert all(timeout is None for _, timeout in fake.reads)

  async def test_nonzero_chunk_flags_are_warned(self, caplog: pytest.LogCaptureFixture) -> None:
    rows = [[0.0] * 12 for _ in range(8)]
    reader, _ = make_reader(complete_absorbance(rows, flags=0x12) + [status_report()])
    reader.available_wavelengths = [450]
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")

    await reader.read_absorbance(plate, plate.get_all_items(), 450)

    assert "0x12" in caplog.text

  @pytest.mark.parametrize(
    "chunks, message",
    [
      ([absorbance_chunk(0, 0, [0.0] * 12)], "seq_len=0"),
      ([absorbance_chunk(8, 8, [0.0] * 12)], "sent seq=8"),
    ],
  )
  async def test_malformed_chunks_raise(self, chunks: list[bytes], message: str) -> None:
    reader, _ = make_reader(chunks)

    with pytest.raises(RuntimeError, match=message):
      await reader._run_abs_measurement(450, 0, False)

  @pytest.mark.parametrize("seq_len", [7, 9])
  @pytest.mark.parametrize("is_reference", [False, True])
  async def test_measurements_require_exactly_eight_chunks(
    self, seq_len: int, is_reference: bool
  ) -> None:
    chunks = [absorbance_chunk(seq, seq_len, [0.0] * 12) for seq in range(seq_len)]
    reader, _ = make_reader(chunks + [status_report()])

    with pytest.raises(RuntimeError, match="expected exactly 8 chunks"):
      await reader._run_abs_measurement(450, 0, is_reference)

  async def test_firmware_error_uses_absorbance_name_and_flags(self) -> None:
    rows = [[0.0] * 12 for _ in range(8)]
    reader, _ = make_reader(complete_absorbance(rows, flags=3) + [status_report(1)])

    with pytest.raises(RuntimeError, match="ERROR_CALIB.*0x03"):
      await reader._run_abs_measurement(450, 0, False)

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
    cancelled = anyio.Event()

    async def measure() -> None:
      with pytest.raises(ByonoyMeasurementCancelledError):
        await reader._run_abs_measurement(450, 0, False)
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
    assert fake.writes[-1][2:4] == b"\x20\x03"

  async def test_measurement_deadline_aborts_incomplete_read(self) -> None:
    reader, fake = make_reader()
    reader._measurement_read_timeout = 0.01

    with anyio.fail_after(1):
      with pytest.raises(TimeoutError, match="Measurement timeout"):
        await reader._run_abs_measurement(450, 0, False)

    assert report_ids(fake.writes) == [0x0010, 0x0200, 0x0320, 0x0060]

  async def test_pre_trigger_cancel_does_not_trigger_or_abort(self) -> None:
    preflight_started = anyio.Event()
    release_preflight = anyio.Event()

    async def block_preflight(data: bytes) -> None:
      if int.from_bytes(data[:2], "little") == 0x0010:
        preflight_started.set()
        await release_preflight.wait()

    reader, fake = make_reader()
    fake.write_effect = block_preflight
    cancelled = anyio.Event()

    async def measure() -> None:
      with pytest.raises(ByonoyMeasurementCancelledError):
        await reader._run_abs_measurement(450, 0, False)
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

    rows = [[0.0] * 12 for _ in range(8)]
    reader, fake = make_reader(complete_absorbance(rows) + [blocked_status])

    async def blocked_abort(data: bytes) -> None:
      if int.from_bytes(data[:2], "little") == 0x0060:
        abort_started.set()
        await release_abort.wait()

    fake.write_effect = blocked_abort
    finished = anyio.Event()

    async def measure() -> None:
      with pytest.raises(ByonoyMeasurementCancelledError):
        await reader._run_abs_measurement(450, 0, False)
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
    setup_rows = [[0.0] * 12 for _ in range(8)]
    reader, fake = make_reader(
      complete_absorbance(setup_rows) + [status_report(), wavelengths_report(450)]
    )
    trigger_committed = anyio.Event()
    release_trigger = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    trigger_count = 0

    async def block_second_trigger(data: bytes) -> None:
      nonlocal trigger_count
      if int.from_bytes(data[:2], "little") == 0x0320:
        trigger_count += 1
        if trigger_count == 2:
          trigger_committed.set()
          await release_trigger.wait()

    fake.write_effect = block_second_trigger
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")

    async with reader:

      async def measure() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await reader.read_absorbance(plate, plate.get_all_items(), 450)

      async with anyio.create_task_group() as task_group:
        task_group.start_soon(measure)
        await trigger_committed.wait()
        scopes[0].cancel()
        release_trigger.set()

    assert report_ids(fake.writes)[-1] == 0x0060
    assert fake.events.index("write.0060") < fake.events.index("hid.exit")


def _plate(size_z: float) -> Plate:
  well = Well(name="well", size_x=8, size_y=8, size_z=size_z - 1)
  well.location = Coordinate(x=10, y=70, z=1)
  return Plate(name="plate", size_x=127.76, size_y=85.48, size_z=size_z, ordered_items={"A1": well})


class IlluminationUnitHeightCheckTests(unittest.TestCase):
  def _check_with(self, plate: Plate) -> None:
    base = byonoy_a96a_parking_unit(name="base")
    base.plate_holder.assign_child_resource(plate)
    base.illumination_unit_holder.check_can_drop_resource_here(
      byonoy_a96a_illumination_unit(name="illumination_unit")
    )

  def test_the_tallest_plate_that_clears_is_allowed_and_the_next_is_not(self):
    self._check_with(_plate(16.0))
    with self.assertRaises(RuntimeError):
      self._check_with(_plate(16.01))

  def test_a_lid_counts_towards_the_height(self):
    plate = _plate(14.0)
    plate.assign_child_resource(
      Lid(name="lid", size_x=127.76, size_y=85.48, size_z=4.0, nesting_z_height=1.0)
    )
    with self.assertRaises(RuntimeError):
      self._check_with(plate)
