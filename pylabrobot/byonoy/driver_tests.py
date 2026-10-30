import struct
import unittest
from collections import deque
from typing import Any, Awaitable, Callable, Optional, Sequence, cast
from unittest.mock import AsyncMock, patch

import anyio
import pytest

from pylabrobot.byonoy.driver import (
  _GENERIC_ERROR_NAMES,
  _LED_EFFECT_CODES,
  ABS1_ERROR_NAMES,
  ABS96_ERROR_NAMES,
  LUM96_PRESET_S,
  Abs1StatusError,
  Abs96StatusError,
  ByonoyDevice,
  ByonoyDriver,
  ByonoyMeasurementCancelledError,
  ByonoySlotState,
  encode_well_bitmask,
)
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.binary import Writer
from pylabrobot.io.boundary import BoundaryRead
from pylabrobot.io.hid import HID
from pylabrobot.testing.concurrency import AnyioTestBase

ReadEffect = bytes | BoundaryRead | Callable[[], Awaitable[bytes]]
WriteEffect = Callable[[bytes], Awaitable[None]]


class FakeHID(HasLifespan):
  """Software-only HID transport with observable native ownership."""

  def __init__(
    self,
    *,
    events: Optional[list[str]] = None,
    replies: Optional[Sequence[ReadEffect]] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    write_effect: Optional[WriteEffect] = None,
  ) -> None:
    super().__init__()
    self.human_readable_device_name = "Fake Byonoy"
    self.events = events if events is not None else []
    self.replies = deque(replies or [])
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.write_effect = write_effect
    self.writes: list[bytes] = []
    self.reads: list[tuple[int, Optional[float]]] = []

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeHID does not accept lifespan options")
    await super()._enter_lifespan(stack)
    self.events.append("hid.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("hid.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def write(self, data: bytes, report_id: bytes = b"\x00") -> None:
    assert report_id == b"\x00"
    self.writes.append(data)
    report = int.from_bytes(data[:2], "little")
    self.events.append(f"write.{report:04x}")
    if self.write_effect is not None:
      await self.write_effect(data)
    else:
      await anyio.lowlevel.checkpoint()

  async def read(self, size: int, timeout: Optional[float] = None) -> bytes:
    self.reads.append((size, timeout))
    await anyio.lowlevel.checkpoint()
    if not self.replies:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")
    effect = self.replies.popleft()
    result = await effect() if callable(effect) else effect
    return result.data if isinstance(result, BoundaryRead) else result

  async def read_to_boundary(self) -> BoundaryRead:
    """Return the next scripted complete input report."""
    self.reads.append((64, None))
    await anyio.lowlevel.checkpoint()
    if not self.replies:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")
    effect = self.replies.popleft()
    result = await effect() if callable(effect) else effect
    return result if isinstance(result, BoundaryRead) else BoundaryRead(result, True, True, False)


def make_driver(**fake_kwargs: Any) -> tuple[ByonoyDriver, FakeHID]:
  driver = ByonoyDriver(pid=1, device_type=ByonoyDevice.ABSORBANCE_96, name="Byonoy test")
  fake = FakeHID(**fake_kwargs)
  driver.io = cast(HID, fake)
  return driver, fake


def report_ids(writes: list[bytes]) -> list[int]:
  return [int.from_bytes(write[:2], "little") for write in writes]


def status_report(error_code: int = 0) -> bytes:
  return struct.pack("<HBBBIBB", 0x0300, 1, 2, error_code, 42, 0, 1).ljust(64, b"\x00")


def supported_reports_chunk(seq: int, seq_len: int, ids: list[int]) -> bytes:
  values = (ids + [0] * 29)[:29]
  return struct.pack("<HBB29H", 0x0010, seq, seq_len, *values).ljust(64, b"\x00")


class TestStatus(AnyioTestBase):
  """Verify status decoding with mocked device responses."""

  async def test_firmware_slot_codes_are_translated_to_public_states(self) -> None:
    """Translate firmware slot codes without disturbing adjacent status fields."""
    for code, expected in (
      (0, ByonoySlotState.EMPTY),
      (1, ByonoySlotState.OCCUPIED),
      (2, ByonoySlotState.UNDETERMINED),
      (3, ByonoySlotState.UNKNOWN),
      (255, ByonoySlotState.UNKNOWN),
    ):
      response = Writer().u16(0x0300).u8(0).u8(code).u8(1).u32(232).u8(0).u8(1).finish()
      driver, _ = make_driver()
      with patch.object(driver, "_send_command_locked", new=AsyncMock(return_value=response)):
        status = await driver.request_status()
      assert status.slot_state == expected
      assert not status.is_initialized
      assert status.error_code == 1
      assert status.uptime_s == 232
      assert not status.is_measuring
      assert status.boot_completed


class EncodeWellBitmaskTests(unittest.TestCase):
  def test_all_false_is_zero_filled(self):
    self.assertEqual(encode_well_bitmask([False] * 96), b"\x00" * 12)

  def test_all_true_is_all_ones(self):
    self.assertEqual(encode_well_bitmask([True] * 96), b"\xff" * 12)

  def test_a1_only_sets_byte0_bit0(self):
    bools = [False] * 96
    bools[0] = True
    self.assertEqual(encode_well_bitmask(bools), b"\x01" + b"\x00" * 11)

  def test_h12_only_sets_byte11_bit7(self):
    bools = [False] * 96
    bools[95] = True
    self.assertEqual(encode_well_bitmask(bools), b"\x00" * 11 + b"\x80")

  def test_bits_7_and_8_cross_byte_boundary(self):
    bools = [False] * 96
    bools[7] = True  # byte 0, bit 7 → 0x80
    bools[8] = True  # byte 1, bit 0 → 0x01
    self.assertEqual(encode_well_bitmask(bools), b"\x80\x01" + b"\x00" * 10)

  def test_first_column_has_8_bits_set(self):
    bools = [False] * 96
    for r in range(8):
      bools[r * 12] = True
    result = encode_well_bitmask(bools)
    self.assertEqual(sum(bin(b).count("1") for b in result), 8)

  def test_custom_n_size(self):
    # bit 0 + bit 2 → 0x05
    self.assertEqual(encode_well_bitmask([True, False, True], n=3), b"\x05")

  def test_length_mismatch_raises(self):
    with self.assertRaises(ValueError):
      encode_well_bitmask([True] * 95)
    with self.assertRaises(ValueError):
      encode_well_bitmask([True] * 96, n=24)


class IntegrationModePresetTests(unittest.TestCase):
  def test_preset_values_match_vendor(self):
    self.assertEqual(LUM96_PRESET_S["rapid"], 0.1)
    self.assertEqual(LUM96_PRESET_S["sensitive"], 2.0)
    self.assertEqual(LUM96_PRESET_S["ultra_sensitive"], 20.0)

  def test_custom_is_not_a_preset(self):
    # "custom" is a Literal value but has no preset — read_luminescence
    # requires the caller to set integration_time explicitly.
    self.assertNotIn("custom", LUM96_PRESET_S)


class ErrorTableTests(unittest.TestCase):
  def test_generic_table_has_only_no_error(self):
    self.assertEqual(_GENERIC_ERROR_NAMES, {0: "NO_ERROR"})

  def test_abs96_table_round_trips(self):
    self.assertEqual(ABS96_ERROR_NAMES[0], "NO_ERROR")
    self.assertEqual(ABS96_ERROR_NAMES[1], "ERROR_CALIB")
    self.assertEqual(ABS96_ERROR_NAMES[Abs96StatusError.ERROR_NO_ACK], "ERROR_NO_ACK")

  def test_abs1_flag_bit_values(self):
    # AbsOne is a bit-flag enum; verify bit positions match the vendor header.
    self.assertEqual(Abs1StatusError.AMBIENT_LIGHT.value, 1)
    self.assertEqual(Abs1StatusError.MIN_LIGHT.value, 2)
    self.assertEqual(Abs1StatusError.USB.value, 4)
    self.assertEqual(Abs1StatusError.HARDWARE.value, 8)
    self.assertEqual(Abs1StatusError.NOISE_LIMIT.value, 128)

  def test_abs1_supports_combined_flags(self):
    combined = Abs1StatusError.AMBIENT_LIGHT | Abs1StatusError.HARDWARE
    self.assertEqual(combined.value, 9)

  def test_abs1_table_includes_all_enum_members(self):
    for member in Abs1StatusError:
      self.assertIn(member.value, ABS1_ERROR_NAMES)


class DescribeErrorCodeTests(unittest.TestCase):
  """describe_error_code() is pure — bypass __init__ to avoid HID setup."""

  def _make_driver(self, error_names):
    drv = ByonoyDriver.__new__(ByonoyDriver)
    drv._ERROR_NAMES = error_names
    return drv

  def test_known_code_returns_name(self):
    drv = self._make_driver(ABS96_ERROR_NAMES)
    self.assertEqual(drv.describe_error_code(1), "ERROR_CALIB")
    self.assertEqual(drv.describe_error_code(0), "NO_ERROR")

  def test_unknown_code_falls_back_to_hex(self):
    drv = self._make_driver(ABS96_ERROR_NAMES)
    self.assertEqual(drv.describe_error_code(0xAB), "errorCode=0xab")

  def test_generic_table_only_knows_no_error(self):
    # Lum96 uses the generic table — anything non-zero is the hex sentinel.
    drv = self._make_driver(_GENERIC_ERROR_NAMES)
    self.assertEqual(drv.describe_error_code(0), "NO_ERROR")
    self.assertEqual(drv.describe_error_code(7), "errorCode=0x07")
    self.assertEqual(drv.describe_error_code(255), "errorCode=0xff")


class LedEffectCodeTests(unittest.TestCase):
  def test_codes_cover_all_effect_literals(self):
    self.assertEqual(
      set(_LED_EFFECT_CODES),
      {"solid", "progress", "cylon", "rainbow", "blinking", "breathing"},
    )

  def test_solid_is_zero(self):
    self.assertEqual(_LED_EFFECT_CODES["solid"], 0x00)

  def test_codes_are_unique(self):
    self.assertEqual(len(set(_LED_EFFECT_CODES.values())), len(_LED_EFFECT_CODES))


class ByonoyDeviceEnumTests(unittest.TestCase):
  def test_distinct_values(self):
    self.assertNotEqual(ByonoyDevice.ABSORBANCE_96, ByonoyDevice.LUMINESCENCE_96)


class TestByonoyDriverProtocol(AnyioTestBase):
  async def test_short_complete_status_report_contains_only_required_fields(self) -> None:
    report = struct.pack("<HBBBIBB", 0x0300, 1, 2, 0, 42, 0, 1)
    driver, _ = make_driver(replies=[report])
    async with driver:
      status = await driver.request_status()
    assert status.uptime_s == 42
    assert status.boot_completed

  async def test_incomplete_status_report_raises_device_specific_error(self) -> None:
    driver, _ = make_driver(replies=[struct.pack("<H", 0x0300) + b"\x01"])
    async with driver:
      with pytest.raises(ValueError, match="incomplete status report"):
        await driver.request_status()

  async def test_incomplete_report_is_rejected_before_parsing(self) -> None:
    driver, _ = make_driver(replies=[BoundaryRead(b"\x00\x03", True, False, True)])
    async with driver:
      with pytest.raises(RuntimeError, match="Incomplete Byonoy HID report"):
        await driver.request_status()

  @pytest.mark.parametrize(
    ("bad_report", "error_type", "message"),
    [
      (BoundaryRead(b"\x00\x03", True, False, True), RuntimeError, "Incomplete Byonoy HID report"),
      (b"\x00", ValueError, "Not enough data for u16"),
    ],
  )
  async def test_ordinary_reply_error_blocks_successor_before_dispatch(
    self, bad_report: bytes | BoundaryRead, error_type: type[Exception], message: str
  ) -> None:
    driver, fake = make_driver(replies=[bad_report, status_report(), status_report()])

    with pytest.raises(error_type, match=message):
      await driver.send_command(0x0300, b"\x00" * 60)
    with pytest.raises(RuntimeError, match="reply boundary unknown"):
      await driver.send_command(0x0300, b"\x00" * 60)

    assert report_ids(fake.writes) == [0x0300]
    assert len(fake.reads) == 1

  async def test_command_reads_without_per_report_timeout(self) -> None:
    """The command owns its entire reply deadline across report reads."""
    reply = bytes([0x00, 0x03]) + bytes(62)
    driver, fake = make_driver(replies=[reply])
    assert await driver.send_command(0x0300, bytes(60)) == reply
    assert fake.reads == [(64, None)]

  async def test_command_response_deadline_interrupts_empty_reads(self) -> None:
    driver, fake = make_driver()
    driver._command_response_timeout = 0.01

    with anyio.fail_after(1):
      with pytest.raises(TimeoutError, match="Reading data timed out after 2 minutes"):
        await driver.send_command(0x0300, b"\x00" * 60)

    assert report_ids(fake.writes) == [0x0300]
    with pytest.raises(RuntimeError, match="reply boundary unknown"):
      await driver.send_command(0x0300, b"\x00" * 60)
    assert report_ids(fake.writes) == [0x0300]

  async def test_supported_report_deadline_interrupts_empty_reads(self) -> None:
    driver, fake = make_driver()
    driver._supported_reports_timeout = 0.01

    with anyio.fail_after(1):
      with pytest.raises(TimeoutError, match="Timed out reading supported reports"):
        await driver.request_supported_reports()

    assert report_ids(fake.writes) == [0x0010]
    fake.replies.append(status_report())
    with pytest.raises(RuntimeError, match="reply boundary unknown"):
      await driver.request_status()
    assert report_ids(fake.writes) == [0x0010]

  async def test_supported_reports_write_error_after_dispatch_blocks_successor(self) -> None:
    failure = OSError("write may have committed")

    async def uncertain_write(data: bytes) -> None:
      if int.from_bytes(data[:2], "little") == 0x0010:
        raise failure

    driver, fake = make_driver(
      write_effect=uncertain_write,
      replies=[supported_reports_chunk(0, 1, [0x0300]), status_report()],
    )
    with pytest.raises(OSError) as error:
      await driver.request_supported_reports()
    assert error.value is failure

    with pytest.raises(RuntimeError, match="reply boundary unknown"):
      await driver.request_status()
    assert report_ids(fake.writes) == [0x0010]
    assert fake.reads == []

  async def test_cancelled_supported_reports_owns_remaining_chunks_before_successor(self) -> None:
    second_chunk_started = anyio.Event()
    release_second_chunk = anyio.Event()

    async def delayed_second_chunk() -> bytes:
      second_chunk_started.set()
      try:
        await release_second_chunk.wait()
      except anyio.get_cancelled_exc_class():
        fake.replies.appendleft(supported_reports_chunk(1, 2, [0x0310]))
        raise
      return supported_reports_chunk(1, 2, [0x0310])

    driver, fake = make_driver(
      replies=[supported_reports_chunk(0, 2, [0x0300]), delayed_second_chunk, status_report()]
    )
    scopes: list[anyio.CancelScope] = []
    cancelled: list[bool] = []
    uptimes: list[int] = []

    async def first() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await driver.request_supported_reports()
      cancelled.append(scope.cancelled_caught)

    async def successor() -> None:
      uptimes.append((await driver.request_status()).uptime_s)

    async with anyio.create_task_group() as group:
      group.start_soon(first)
      await second_chunk_started.wait()
      scopes[0].cancel()
      group.start_soon(successor)
      await anyio.lowlevel.checkpoint()
      assert report_ids(fake.writes) == [0x0010]
      release_second_chunk.set()

    assert cancelled == [True]
    assert uptimes == [42]
    assert report_ids(fake.writes) == [0x0010, 0x0300]
    assert len(fake.reads) == 3

  async def test_pre_cancelled_command_writes_nothing(self) -> None:
    driver, fake = make_driver()

    with anyio.CancelScope() as scope:
      scope.cancel()
      await driver.send_command(0x0300, b"\x00" * 60)

    assert fake.writes == []

  @pytest.mark.parametrize("wait_for_response", [True, False])
  async def test_ordinary_write_error_after_dispatch_blocks_successor(
    self, wait_for_response: bool
  ) -> None:
    failure = OSError("write may have committed")

    async def uncertain_write(data: bytes) -> None:
      if int.from_bytes(data[:2], "little") == 0x0050:
        raise failure

    driver, fake = make_driver(
      write_effect=uncertain_write,
      replies=[struct.pack("<HI", 0x0050, 111), status_report()],
    )
    with pytest.raises(OSError) as error:
      await driver.send_command(0x0050, b"\x00" * 60, wait_for_response=wait_for_response)
    assert error.value is failure

    with pytest.raises(RuntimeError, match="reply boundary unknown"):
      await driver.request_status()
    assert report_ids(fake.writes) == [0x0050]
    assert fake.reads == []

  async def test_cancelled_command_consumes_its_reply_before_next_same_report(self) -> None:
    old_read_started = anyio.Event()
    release_old_reply = anyio.Event()
    old_reply = struct.pack("<HI", 0x0050, 111)
    new_reply = struct.pack("<HI", 0x0050, 222)

    async def delayed_old_reply() -> bytes:
      old_read_started.set()
      try:
        await release_old_reply.wait()
      except anyio.get_cancelled_exc_class():
        fake.replies.appendleft(old_reply)
        raise
      return old_reply

    driver, fake = make_driver(replies=[delayed_old_reply, new_reply])
    scopes: list[anyio.CancelScope] = []
    cancelled: list[bool] = []
    successor: list[bytes] = []

    async def first() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await driver.send_command(0x0050, b"\x00" * 60)
      cancelled.append(scope.cancelled_caught)

    async def second() -> None:
      result = await driver.send_command(0x0050, b"\x00" * 60)
      assert result is not None
      successor.append(result)

    async with anyio.create_task_group() as group:
      group.start_soon(first)
      await old_read_started.wait()
      scopes[0].cancel()
      group.start_soon(second)
      await anyio.lowlevel.checkpoint()
      assert report_ids(fake.writes) == [0x0050]
      release_old_reply.set()

    assert cancelled == [True]
    assert successor == [new_reply]
    assert report_ids(fake.writes) == [0x0050, 0x0050]

  async def test_supported_report_chunks_are_ordered_deduplicated_and_serialized(self) -> None:
    read_started = anyio.Event()
    release_status = anyio.Event()

    async def blocked_status() -> bytes:
      read_started.set()
      await release_status.wait()
      return status_report()

    driver, fake = make_driver(
      replies=[
        blocked_status,
        supported_reports_chunk(1, 2, [0x0310, 0x0340]),
        supported_reports_chunk(0, 2, [0x0300, 0x0310]),
      ]
    )
    reports: list[int] = []

    async def get_status() -> None:
      await driver.request_status()

    async def get_reports() -> None:
      reports.extend(await driver.request_supported_reports())

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(get_status)
      await read_started.wait()
      task_group.start_soon(get_reports)
      await anyio.sleep(0)
      assert report_ids(fake.writes) == [0x0300]
      release_status.set()

    assert reports == [0x0300, 0x0310, 0x0340]
    assert report_ids(fake.writes) == [0x0300, 0x0010]

  @pytest.mark.parametrize(
    "chunks, message",
    [
      (
        [supported_reports_chunk(0, 0, []), supported_reports_chunk(0, 1, [0x0300])],
        "seq_len=0",
      ),
      (
        [supported_reports_chunk(2, 2, [0x0300]), supported_reports_chunk(1, 2, [0x0310])],
        "seq=2",
      ),
      (
        [
          supported_reports_chunk(0, 2, [0x0300]),
          supported_reports_chunk(0, 2, [0x0310]),
          supported_reports_chunk(1, 2, [0x0340]),
        ],
        "duplicate seq=0",
      ),
      (
        [
          supported_reports_chunk(0, 2, [0x0300]),
          supported_reports_chunk(1, 3, [0x0310]),
          supported_reports_chunk(2, 3, [0x0340]),
        ],
        "changed seq_len",
      ),
      (
        [
          supported_reports_chunk(0, 3, [0x0300]),
          supported_reports_chunk(2, 3, [0x0340]),
        ],
        "missing sequence indices.*1",
      ),
    ],
  )
  async def test_malformed_supported_report_chunks_raise_finite_protocol_error(
    self, chunks: list[bytes], message: str
  ) -> None:
    driver, fake = make_driver(replies=chunks)
    driver._supported_reports_timeout = 0.01

    with pytest.raises(RuntimeError, match=message):
      await driver.request_supported_reports()
    fake.replies.append(status_report())
    with pytest.raises(RuntimeError, match="reply boundary unknown"):
      await driver.request_status()
    assert report_ids(fake.writes) == [0x0010]

  @pytest.mark.parametrize("trigger_report", [0x0320, 0x0340])
  async def test_cancel_waiting_for_transaction_lock_does_not_trigger(
    self, trigger_report: int
  ) -> None:
    driver, fake = make_driver()
    await driver._transaction_lock.acquire()
    cancelled = anyio.Event()

    async def measure() -> None:
      with pytest.raises(ByonoyMeasurementCancelledError):
        async with driver._measurement_transaction(trigger_report):
          await driver._send_command_locked(trigger_report, b"\x00" * 60, wait_for_response=False)

    async def cancel() -> None:
      await driver.cancel()
      cancelled.set()

    writes_before_release: list[int] = []
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(measure)
      while driver._in_flight_trigger is None:
        await anyio.lowlevel.checkpoint()
      task_group.start_soon(cancel)
      await cancelled.wait()
      writes_before_release.extend(report_ids(fake.writes))
      driver._transaction_lock.release()

    assert writes_before_release == []
    assert report_ids(fake.writes) == []

  @pytest.mark.parametrize("trigger_report", [0x0320, 0x0340])
  async def test_cancel_during_trigger_write_aborts_after_commit(self, trigger_report: int) -> None:
    trigger_started = anyio.Event()
    release_trigger = anyio.Event()
    finished = anyio.Event()

    async def blocked_trigger(data: bytes) -> None:
      if int.from_bytes(data[:2], "little") == trigger_report:
        trigger_started.set()
        await release_trigger.wait()

    driver, fake = make_driver(write_effect=blocked_trigger)

    async def measure() -> None:
      with pytest.raises(ByonoyMeasurementCancelledError):
        async with driver._measurement_transaction(trigger_report):
          await driver._send_command_locked(trigger_report, b"\x00" * 60, wait_for_response=False)
          await anyio.sleep_forever()
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(measure)
      await trigger_started.wait()
      await driver.cancel()
      assert report_ids(fake.writes) == [trigger_report]
      release_trigger.set()
      with anyio.fail_after(1):
        await finished.wait()

    assert report_ids(fake.writes) == [trigger_report, 0x0060]

  async def test_concurrent_cancel_emits_abort_once(self) -> None:
    abort_started = anyio.Event()
    release_abort = anyio.Event()

    async def block_abort(data: bytes) -> None:
      if int.from_bytes(data[:2], "little") == 0x0060:
        abort_started.set()
        await release_abort.wait()

    driver, fake = make_driver(write_effect=block_abort)
    driver._in_flight_trigger = 0x0340
    driver._measurement_trigger_committed = True

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(driver.cancel)
      await abort_started.wait()
      task_group.start_soon(driver.cancel)
      await anyio.sleep(0)
      release_abort.set()

    assert report_ids(fake.writes) == [0x0060]

  async def test_cancel_joins_abort_before_next_same_report_measurement(self) -> None:
    abort_started = anyio.Event()
    release_old_abort = anyio.Event()
    abort_count = 0

    async def block_first_abort(data: bytes) -> None:
      nonlocal abort_count
      if int.from_bytes(data[:2], "little") == 0x0060:
        abort_count += 1
        if abort_count == 1:
          abort_started.set()
          await release_old_abort.wait()

    driver, fake = make_driver(write_effect=block_first_abort)
    a_ready = anyio.Event()
    a_finished = anyio.Event()
    cancel_a_finished = anyio.Event()
    b_started = anyio.Event()

    async def measurement_a() -> None:
      with pytest.raises(ByonoyMeasurementCancelledError):
        async with driver._measurement_transaction(0x0340):
          await driver._send_command_locked(0x0340, b"\x00" * 60, wait_for_response=False)
          a_ready.set()
          await anyio.sleep_forever()
      a_finished.set()

    async def cancel_a() -> None:
      await driver.cancel()
      cancel_a_finished.set()

    async def measurement_b() -> None:
      with pytest.raises(ByonoyMeasurementCancelledError):
        async with driver._measurement_transaction(0x0340):
          await driver._send_command_locked(0x0340, b"\x00" * 60, wait_for_response=False)
          b_started.set()
          await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(measurement_a)
      await a_ready.wait()
      task_group.start_soon(cancel_a)
      await abort_started.wait()
      assert not a_finished.is_set()
      assert report_ids(fake.writes) == [0x0340, 0x0060]
      release_old_abort.set()
      await a_finished.wait()
      await cancel_a_finished.wait()
      task_group.start_soon(measurement_b)
      await b_started.wait()
      scope = driver._measurement_scope
      assert scope is not None
      b_state_before_cancel = (scope.cancel_called, driver._abort_sent_for_trigger)
      await driver.cancel()

    assert b_state_before_cancel == (False, False)
    assert report_ids(fake.writes) == [0x0340, 0x0060, 0x0340, 0x0060]

  async def test_lifespan_abort_waiter_cannot_contaminate_next_generation(self) -> None:
    abort_started = anyio.Event()
    release_old_abort = anyio.Event()
    abort_count = 0

    async def block_abort(data: bytes) -> None:
      nonlocal abort_count
      if int.from_bytes(data[:2], "little") == 0x0060:
        abort_count += 1
        if abort_count == 1:
          abort_started.set()
          await release_old_abort.wait()

    driver, fake = make_driver(write_effect=block_abort)
    lifespan_entered = anyio.Event()
    exit_lifespan = anyio.Event()
    lifespan_exited = anyio.Event()
    a_ready = anyio.Event()
    a_finished = anyio.Event()
    cancel_a_finished = anyio.Event()
    b_started = anyio.Event()

    async def own_lifespan() -> None:
      async with driver:
        lifespan_entered.set()
        await exit_lifespan.wait()
      lifespan_exited.set()

    async def measurement_a() -> None:
      with pytest.raises(ByonoyMeasurementCancelledError):
        async with driver._measurement_transaction(0x0340):
          await driver._send_command_locked(0x0340, b"\x00" * 60, wait_for_response=False)
          a_ready.set()
          await anyio.sleep_forever()
      a_finished.set()

    async def cancel_a() -> None:
      await driver.cancel()
      cancel_a_finished.set()

    async def measurement_b() -> None:
      with pytest.raises(ByonoyMeasurementCancelledError):
        async with driver._measurement_transaction(0x0340):
          await driver._send_command_locked(0x0340, b"\x00" * 60, wait_for_response=False)
          b_started.set()
          await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(own_lifespan)
      await lifespan_entered.wait()
      task_group.start_soon(measurement_a)
      await a_ready.wait()
      task_group.start_soon(cancel_a)
      await abort_started.wait()
      exit_lifespan.set()
      while driver._abort_lock.statistics().tasks_waiting == 0 and not lifespan_exited.is_set():
        await anyio.lowlevel.checkpoint()
      cleanup_waited_for_abort = driver._abort_lock.statistics().tasks_waiting > 0
      assert not a_finished.is_set()
      hid_closed_before_old_abort_finished = "hid.exit" in fake.events
      release_old_abort.set()
      await a_finished.wait()
      await lifespan_exited.wait()
      task_group.start_soon(measurement_b)
      await b_started.wait()
      await cancel_a_finished.wait()
      scope = driver._measurement_scope
      assert scope is not None
      b_state_before_cancel = (scope.cancel_called, driver._abort_sent_for_trigger)
      await driver.cancel()

    assert cleanup_waited_for_abort
    assert not hid_closed_before_old_abort_finished
    assert b_state_before_cancel == (False, False)
    assert report_ids(fake.writes) == [0x0340, 0x0060, 0x0340, 0x0060]
    assert fake.events.index("hid.exit") < fake.events.index("write.0340", 2)


class TestByonoyDriverLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    assert ByonoyDriver.setup is HasLifespan.setup
    assert ByonoyDriver.stop is HasLifespan.stop
    assert ByonoyDriver.__aenter__ is HasLifespan.__aenter__
    assert ByonoyDriver.__aexit__ is HasLifespan.__aexit__

  async def test_hid_entry_failure_does_not_exit(self) -> None:
    failure = RuntimeError("hid entry failed")
    driver, fake = make_driver(entry_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with driver:
        pytest.fail("failed HID entry reached body")

    assert error_info.value is failure
    assert fake.events == ["hid.enter"]

  async def test_body_cancellation_closes_hid(self) -> None:
    events: list[str] = []
    entered = anyio.Event()
    driver, _ = make_driver(events=events)

    async def use_driver() -> None:
      try:
        async with driver:
          entered.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_driver)
      await entered.wait()
      task_group.cancel_scope.cancel()

    assert events == ["hid.enter", "hid.exit", "cancelled"]

  async def test_active_measurement_is_aborted_before_hid_close(self) -> None:
    events: list[str] = []
    driver, fake = make_driver(events=events)

    async with driver:
      driver._in_flight_trigger = 0x0340
      driver._measurement_trigger_committed = True

    assert report_ids(fake.writes) == [0x0060]
    assert events == ["hid.enter", "write.0060", "hid.exit"]

  async def test_sequential_reuse(self) -> None:
    driver, fake = make_driver()

    async with driver:
      pass
    async with driver:
      pass

    assert fake.events == ["hid.enter", "hid.exit", "hid.enter", "hid.exit"]

  async def test_hid_close_failure_propagates(self) -> None:
    failure = RuntimeError("hid close failed")
    driver, fake = make_driver(exit_error=failure)

    with pytest.raises(RuntimeError) as error_info:
      async with driver:
        pass

    assert error_info.value is failure
    assert fake.events == ["hid.enter", "hid.exit"]

  async def test_rejects_lifespan_options_before_hid_entry(self) -> None:
    driver, fake = make_driver()

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with driver.lifespan(unknown=True):
        pytest.fail("invalid lifespan reached body")

    assert fake.events == []


if __name__ == "__main__":
  unittest.main()
