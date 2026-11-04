"""Synergy HT command sequences, measurement parsing, and protocol failure cases."""

from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
import json
import logging
import math
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, patch

import anyio
import pytest

from pylabrobot.agilent.biotek.plate_reader_base import AbsorbanceResult
from pylabrobot.agilent.biotek.synergy_ht import SynergyHT
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.ftdi import FTDI
from pylabrobot.testing.concurrency import AnyioTestBase

DATA = Path(__file__).parent / "test_data"
FIXTURES = json.loads((DATA / "synergy_ht.json").read_text())
ABSORBANCE_SETUPS = json.loads((DATA / "synergy_ht_setup.json").read_text())
SHAKING = json.loads((DATA / "synergy_ht_shaking.json").read_text())


class Replay:
  """Require the expected write stream and serve only the subsequent replies."""

  def __init__(self, events, chunk_size=4096):
    self.events = deque((e["direction"], bytes.fromhex(e["hex"])) for e in events)
    self.chunk_size = chunk_size
    self.idle_reads = 0

  def consume(self, direction, size):
    """Consume a bounded portion of the next event, preserving coalesced replies."""
    assert self.events, "Unexpected I/O after the transcript ended"
    actual_direction, data = self.events.popleft()
    assert actual_direction == direction, (direction, actual_direction, data)
    result, remaining = data[:size], data[size:]
    if remaining:
      self.events.appendleft((direction, remaining))
    return result

  async def write(self, data):
    """Compare outgoing bytes, including the ACK-only command parameters."""
    assert self.consume("tx", len(data)) == data
    return len(data)

  async def read(self, size):
    """Split incoming replies at arbitrary USB transfer boundaries.

    Like FTDI, return no data while the device owes nothing (the next event is a write).
    """
    if self.events and self.events[0][0] == "tx":
      self.idle_reads += 1
      assert self.idle_reads < 1000, "Driver kept reading while a write was expected"
      return b""
    self.idle_reads = 0
    return self.consume("rx", min(size, self.chunk_size))


def events(*pairs):
  """Build a transcript from direction/bytes pairs."""
  return [{"direction": d, "hex": b.hex()} for d, b in pairs]


def setup_events(source=FIXTURES):
  """Build the identity, capability, and status exchanges for initialization."""
  result = events(
    ("tx", b"e"),
    ("rx", bytes.fromhex(source["identities"]["e"])),
    ("tx", b"C"),
    ("rx", bytes.fromhex(source["identities"]["C"])),
  )
  for number in (2000, 1013, 1006, 1008, 1009, 1012, 1020, 1031):
    result.extend(
      events(
        ("tx", b"z"),
        ("rx", b"\x06"),
        ("tx", f"{number}\x03".encode()),
        ("rx", bytes.fromhex(source["queries"][str(number)])),
      )
    )
  result.extend(events(("tx", b"n"), ("rx", b"\x06"), ("tx", b"1"), ("rx", b"0000\x03")))
  return result


def attach(io, transcript, chunk_size=4096):
  """Attach a strict replay to a mocked PLR FTDI transport."""
  replay = Replay(transcript, chunk_size)

  async def connect():
    io.read.side_effect = replay.read
    io.write.side_effect = replay.write

  io.setup.side_effect = connect
  io.read.side_effect = replay.read
  io.write.side_effect = replay.write
  return replay


def result_frame(values, checksum=None, temperature=b"237"):
  """Construct distinguishable synthetic OD values, independent of the driver encoder."""
  body = (
    b"012:34:56.7"
    + b"".join(b"".join(b"," + value for value in row) + b"\r\n" for row in values)
    + temperature
    + b"\x1a"
  )
  if checksum is None:
    checksum = sum(body) & 255
  return b"\r" + body + bytes((checksum,)) + b"\x1a0000\x03"


class TestSynergyHT(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    plr_logger = logging.getLogger("pylabrobot")
    old_propagate = plr_logger.propagate
    plr_logger.propagate = True
    stack.callback(setattr, plr_logger, "propagate", old_propagate)
    transport = stack.enter_context(patch("pylabrobot.agilent.biotek._reader.FTDI", autospec=FTDI))
    self.device = SynergyHT()
    self.device._temperature_range = (22, 50)
    self.io = transport.return_value

    async def disconnect():
      """Model FTDI rejecting I/O once stop() closes its handle."""
      self.io.write.side_effect = RuntimeError("Device not initialized. Call setup() first.")
      self.io.read.side_effect = RuntimeError("Device not initialized. Call setup() first.")

    self.io.stop.side_effect = disconnect
    self.io._close = self.io.stop

    @asynccontextmanager
    async def io_lifespan():
      await self.io.setup()
      try:
        yield self.io
      finally:
        with anyio.CancelScope(shield=True):
          await self.io.stop()

    self.io.lifespan = io_lifespan
    stack.enter_context(
      patch("pylabrobot.agilent.biotek.synergy_ht.anyio.sleep", new_callable=AsyncMock)
    )
    stack.enter_context(
      patch("pylabrobot.agilent.biotek._reader.anyio.sleep", new_callable=AsyncMock)
    )

  @pytest.mark.parametrize("chunk_size", [1, 2, 61, 64, 4096])
  @pytest.mark.parametrize("read", FIXTURES["reads"], ids=lambda x: f"{x['rows']}x{x['columns']}")
  async def test_endpoint_sequence(self, read, chunk_size):
    device, io = self.device, self.io
    replay = attach(io, read["events"], chunk_size)
    plate_type: Literal["24-well", "96-well"] = "24-well" if read["rows"] == 4 else "96-well"
    started = datetime.now()
    results = await device.read_absorbance(600, plate_type)
    completed = datetime.now()
    assert len(results) == 1
    result = results[0]
    assert isinstance(result, AbsorbanceResult)
    assert result.data == [[0.0] * read["columns"] for _ in range(read["rows"])]
    assert result.wavelength == 600
    assert result.temperature == (29.0 if plate_type == "24-well" else 19.9)
    assert started <= result.timestamp <= completed
    assert not replay.events
    io.usb_purge_rx_buffer.assert_not_called()

  @pytest.mark.parametrize(
    "measurement", FIXTURES["measurements"], ids=lambda x: f"{x['rows']}x{x['columns']}"
  )
  def test_absorbance_measurements(self, measurement):
    data, _ = SynergyHT._parse_absorbance(
      bytes.fromhex(measurement["result"]), measurement["rows"], measurement["columns"]
    )
    assert data == [[0.0] * measurement["columns"] for _ in range(measurement["rows"])]

  @pytest.mark.parametrize("wavelength,expected", ABSORBANCE_SETUPS.items())
  def test_absorbance_setup_encoding(self, wavelength, expected):
    assert SynergyHT._absorbance_setup(int(wavelength)) == bytes.fromhex(expected)

  @pytest.mark.parametrize(
    "setup", SHAKING["setups"], ids=lambda x: f"{x['duration']}s-{x['intensity']}"
  )
  def test_shake_setup_encoding(self, setup):
    assert SynergyHT._absorbance_setup(600, setup["duration"], setup["intensity"]) == bytes.fromhex(
      setup["hex"]
    )

  @pytest.mark.parametrize(
    "setup",
    SHAKING["duration_setups"],
    ids=lambda x: f"{x['wavelength']}nm-{x['duration']}s-{x['intensity']}",
  )
  def test_shake_duration_encoding(self, setup):
    assert SynergyHT._absorbance_setup(
      setup["wavelength"], setup["duration"], setup["intensity"]
    ) == bytes.fromhex(setup["hex"])

  @pytest.mark.parametrize("chunk_size", [1, 2, 61, 64, 4096])
  async def test_shake_then_read_without_shaking(self, chunk_size):
    device, io = self.device, self.io
    read = SHAKING["read"]
    replay = attach(io, read["events"] + FIXTURES["reads"][0]["events"], chunk_size)
    with patch(
      "pylabrobot.agilent.biotek.synergy_ht.anyio.fail_after", wraps=anyio.fail_after
    ) as fail_after:
      (result,) = await device.read_absorbance(
        600,
        "96-well",
        shake_duration=read["duration"],
        shake_intensity=read["intensity"],
      )
      assert fail_after.call_args.args[0] == device.read_timeout + read["duration"]
      assert result.data == [[0.0] * 12 for _ in range(8)]
      assert result.temperature == 32.0
      (unshaken,) = await device.read_absorbance(600, "96-well")
      assert fail_after.call_args.args[0] == device.read_timeout
      assert unshaken.data == [[0.0] * 12 for _ in range(8)]
    assert not replay.events

  async def test_setup_and_idempotent_lifecycle(self, caplog: pytest.LogCaptureFixture):
    device, io = self.device, self.io
    with pytest.raises(RuntimeError, match="Firmware version is not set"):
      _ = device.version
    replay = attach(io, setup_events() * 2, 1)
    with caplog.at_level(logging.WARNING, logger=SynergyHT.__module__):
      async with device:
        assert device.version == "2.24"
      async with device:
        assert device.version == "2.24"
        assert device.serial_number == "196804"
        assert device.temperature_range == (22, 50)
        assert io.setup.await_count == 2
        io.set_baudrate.assert_awaited_with(9600)
        io.set_line_property.assert_awaited_with(8, 2, 0)
        io.set_flowctrl.assert_awaited_with(0x100)
        io.set_latency_timer.assert_awaited_with(16)
        io.set_rts.assert_awaited_with(True)
        io.set_dtr.assert_awaited_with(False)
    assert not caplog.records
    assert not replay.events
    assert io.stop.await_count == 2
    with pytest.raises(RuntimeError, match="Firmware version is not set"):
      _ = device.version
    assert device.serial_number is None
    assert device.temperature_range is None

  @pytest.mark.parametrize("failure", ["model", "rdop22", "capability", "status", "range"])
  async def test_setup_rejects_unsupported_or_malformed_responses(self, failure):
    device, io = self.device, self.io
    transcript = setup_events()
    if failure == "model":
      transcript[1]["hex"] = bytes.fromhex(transcript[1]["hex"]).replace(b"7090202", b"7310001").hex()
    elif failure in ("rdop22", "capability"):
      index = 7 if failure == "rdop22" else 11
      raw = bytes.fromhex(transcript[index]["hex"])
      transcript[index]["hex"] = ((b"1" if failure == "rdop22" else b"0") + raw[1:]).hex()
    elif failure == "status":
      transcript[7]["hex"] = (b"0" * 17 + b"\x1f000\x03").hex()
    else:
      transcript[-5]["hex"] = (b"122,XX,12,70,00000" + b"0000\x03").hex()
    attach(io, transcript)
    with pytest.raises((NotImplementedError, ValueError)):
      async with device:
        pass
    io.stop.assert_awaited_once()

  async def test_setup_after_power_on(self):
    """Initialize a reader whose power-on statuses carry the 0x1E flag until n 1."""
    device, io = self.device, self.io
    replay = attach(io, setup_events(FIXTURES["power_on"]), 1)
    async with device:
      assert not replay.events
      assert device.version == "2.24"
      assert device.serial_number == "196804"
      assert device.temperature_range == (22, 50)

  def test_power_on_status_still_reports_errors(self):
    SynergyHT._check_status(b"\x1e000\x03")
    with pytest.raises(RuntimeError, match="0x0001"):
      SynergyHT._check_status(b"\x1e001\x03")
    with pytest.raises(ValueError):
      SynergyHT._check_status(b"\x1f000\x03")

  async def test_setup_discards_output_queued_for_an_earlier_connection(
    self, caplog: pytest.LogCaptureFixture
  ):
    device, io = self.device, self.io
    stale = events(("rx", b"0000\x03\x067090202  Version 2.24   0000\x03"))
    replay = attach(io, stale + setup_events(), 4)
    with caplog.at_level(logging.WARNING, logger=SynergyHT.__module__):
      async with device:
        assert device.version == "2.24"
    assert not replay.events
    assert any("Discarded 35 stale bytes" in record.getMessage() for record in caplog.records)

  def test_signed_milliod_overflow_and_row_order(self):
    frame = result_frame([[b"+1234", b"-0012"], [b"+4567", b"*****"]])
    values, temperature = SynergyHT._parse_absorbance(frame, 2, 2)
    assert values[0] == [1.234, -0.012]
    assert values[1][0] == 4.567
    assert math.isnan(values[1][1])
    assert temperature == 23.7

  @pytest.mark.parametrize("target", [3, 26])
  def test_checksum_can_equal_etx_or_sub(self, target):
    # Vary valid numeric fields until the checksum equals a framing control byte.
    for value in range(10000):
      frame = result_frame([[f"+{value:04d}".encode()] * 7 + [f"+{value // 10:04d}".encode()]])
      if frame[-7] == target:
        values, _ = SynergyHT._parse_absorbance(frame, 1, 8)
        assert values == [[value / 1000] * 7 + [value // 10 / 1000]]
        break
    else:
      pytest.fail("No checksum collision vector found")

  @pytest.mark.parametrize(
    "mutation",
    ["checksum", "row", "value", "timestamp", "temperature", "separator", "status", "truncated"],
  )
  def test_corrupt_results_are_rejected(self, mutation):
    frame = bytearray(result_frame([[b"+1234", b"-0012"]]))
    if mutation == "checksum":
      frame[-7] ^= 1
    elif mutation == "row":
      frame[24] = ord("x")
    elif mutation == "value":
      frame[14] = ord("x")
    elif mutation == "timestamp":
      frame[5] = ord("9")
    elif mutation == "temperature":
      frame[-11] = ord("x")
    elif mutation == "separator":
      frame[-6] = 0
    elif mutation == "status":
      frame[-2] = ord("1")
    else:
      frame.pop()
    if mutation not in ("checksum", "truncated"):
      frame[-7] = sum(frame[1:-7]) & 255
    with pytest.raises((ValueError, RuntimeError)):
      SynergyHT._parse_absorbance(bytes(frame), 1, 2)

  @pytest.mark.parametrize(
    "temperature,expected_temperature", [(b"237", 23.7), (b"111", None), (b"999", None)]
  )
  async def test_selection_reads_bounding_rectangle_and_masks_other_wells(
    self, temperature, expected_temperature
  ):
    device, io = self.device, self.io
    transcript = events(
      ("tx", b"V"),
      ("rx", b"\x06"),
      ("tx", bytes.fromhex(ABSORBANCE_SETUPS["600"])),
      ("rx", b"0000\x03"),
      ("tx", b"{"),
      ("rx", b"\x06"),
      ("tx", b"\x02"),
      ("rx", b"0000\x03"),
      ("tx", b"i"),
      ("rx", b"\x06"),
      ("tx", b"1r"),
      ("rx", b"\x06"),
      ("tx", b"\x02\x03"),
      ("rx", b"0000\x03"),
      ("tx", b"%"),
      ("rx", b"\x06"),
      ("tx", b"\x03\x04"),
      ("rx", b"0000\x03"),
      ("tx", b"k"),
      ("rx", b"\x06"),
      ("tx", b"0"),
      ("rx", b"0000\x03"),
      ("tx", b"Q"),
      ("rx", b"\x06"),
      ("tx", b"1t"),
      ("rx", b"\x06"),
      ("tx", b"40000\x03"),
      ("rx", b"0000\x03"),
      ("tx", b"S"),
      (
        "rx",
        b"\x060000\x03"
        + result_frame([[b"+1000", b"+2000"], [b"+3000", b"+4000"]], temperature=temperature),
      ),
    )
    replay = attach(io, transcript, 2)
    (result,) = await device.read_absorbance(600, "96-well", ["B3", "C4"])
    assert isinstance(result, AbsorbanceResult)
    assert result.data[1][2] == 1.0 and result.data[2][3] == 4.0
    assert sum(v is not None for row in result.data for v in row) == 2
    assert result.temperature == expected_temperature
    assert not replay.events

  @pytest.mark.parametrize(
    "kwargs",
    [
      {"wavelength": 1000},
      {"wavelength": 0},
      {"plate_type": "384-well"},
      {"wells": []},
      {"wells": ["I1"]},
      {"wells": ["A13"]},
      {"wells": ["A1", "A1"]},
      {"shake_duration": -1},
      {"shake_duration": 65536},
      {"shake_duration": 1.5},
      {"shake_duration": float("nan")},
      {"shake_duration": float("inf")},
      {"shake_duration": True},
      {"shake_intensity": -1},
      {"shake_intensity": 0},
      {"shake_intensity": 1},
      {"shake_intensity": 2},
      {"shake_intensity": 3},
      {"shake_intensity": 4},
      {"shake_intensity": 1.0},
      {"shake_intensity": True},
      {"shake_intensity": "unknown"},
      {"shake_intensity": "Slow"},
    ],
  )
  async def test_invalid_assay_does_not_write(self, kwargs):
    device, io = self.device, self.io
    params = {"wavelength": 600, "plate_type": "96-well", **kwargs}
    with pytest.raises(ValueError):
      await device.read_absorbance(**params)
    io.write.assert_not_awaited()
    io.stop.assert_not_awaited()

  async def test_tray_and_temperature(self):
    device, io = self.device, self.io
    replay = attach(
      io,
      events(
        ("tx", b"J"),
        ("rx", b"\x060000\x03"),
        ("tx", b"A"),
        ("rx", b"\x060000\x03"),
        ("tx", b"g"),
        ("rx", b"\x06"),
        ("tx", b"37"),
        ("rx", b"0000\x03"),
        ("tx", b"h"),
        ("rx", b"\x063170000\x03"),
        ("tx", b"g"),
        ("rx", b"\x06"),
        ("tx", b" 0"),
        ("rx", b"0000\x03"),
      ),
    )
    await device.open()
    await device.close()
    await device.set_temperature(37)
    assert await device.get_temperature() == 31.7
    await device.stop_heating()
    assert not replay.events

  @pytest.mark.parametrize("temperature", [0, 21, 51])
  async def test_invalid_temperature_does_not_write(self, temperature):
    device, io = self.device, self.io
    with pytest.raises(ValueError):
      await device.set_temperature(temperature)
    io.write.assert_not_awaited()

  @pytest.mark.parametrize("failure", ["nak", "partial_write", "status", "timeout", "cancel"])
  async def test_failed_exchange_requires_reconnection(self, failure):
    device, io = self.device, self.io
    io.write.return_value = 1
    io.read.return_value = b"\x15"
    if failure == "partial_write":
      io.write.return_value = 0
    elif failure == "status":
      attach(io, events(("tx", b"J"), ("rx", b"\x060001\x03")))
    elif failure in ("timeout", "cancel"):
      started = anyio.Event()
      scopes: list[anyio.CancelScope] = []
      errors: list[BaseException] = []

      async def stalled_read(_size):
        started.set()
        await anyio.sleep_forever()

      async def run_open():
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await device.open()
          except BaseException as exc:
            errors.append(exc)
            if not isinstance(exc, (TimeoutError, anyio.get_cancelled_exc_class())):
              raise

      io.read.side_effect = stalled_read
      device.timeout = 0.01
      async with anyio.create_task_group() as tg:
        tg.start_soon(run_open)
        await started.wait()
        if failure == "cancel":
          scopes[0].cancel()
      assert errors or scopes[0].cancelled_caught
    if failure not in ("timeout", "cancel"):
      with pytest.raises((RuntimeError, IOError)):
        await device.open()
    io.stop.assert_awaited_once()
    reads = io.read.await_count
    with pytest.raises(RuntimeError, match="Device not initialized"):
      await device.close()
    assert io.read.await_count == reads

    # Entering a new lifespan can reconnect and repeat initialization after the failed stream is closed.
    replay = attach(io, setup_events())
    async with device:
      assert not replay.events
      assert device.version == "2.24"

  @pytest.mark.parametrize("prefix", [b"\x10", b"\x03"])
  async def test_short_async_error_does_not_wait_for_full_plate(self, prefix):
    device, io = self.device, self.io
    replay = attach(io, events(("rx", prefix + b"002A\x03")), 1)
    with pytest.raises(RuntimeError, match="002A"):
      await device._read_absorbance_frame(8, 12)
    assert not replay.events

  @pytest.mark.parametrize(
    "outcome,shake_duration",
    [("complete", 0), ("cancel", 0), ("timeout", 0), ("complete", 10), ("cancel", 10)],
  )
  async def test_measurement_owns_connection_until_result_or_failure(
    self, outcome, shake_duration
  ):
    device, io = self.device, self.io
    read = SHAKING["read"] if shake_duration else FIXTURES["reads"][0]
    transcript = read["events"] + events(("tx", b"h"), ("rx", b"\x062370000\x03"))
    replay = attach(io, transcript)
    waiting = anyio.Event()
    release = anyio.Event()

    async def gated_read(size):
      """Hold the first measurement byte while another operation tries to run."""
      if replay.events[0][1].startswith(b"\r"):
        waiting.set()
        await release.wait()
      return await replay.read(size)

    io.read.side_effect = gated_read
    device.read_timeout = 0.01 if outcome == "timeout" else 180
    measurement_results: list[list[AbsorbanceResult]] = []
    measurement_errors: list[BaseException] = []
    measurement_scopes: list[anyio.CancelScope] = []
    measurement_done = anyio.Event()
    temperature_results: list[float] = []
    temperature_errors: list[BaseException] = []
    temperature_done = anyio.Event()

    async def run_measurement():
      try:
        with anyio.CancelScope() as scope:
          measurement_scopes.append(scope)
          measurement_results.append(
            await device.read_absorbance(600, "96-well", shake_duration=shake_duration)
          )
      except BaseException as exc:
        measurement_errors.append(exc)
        if not isinstance(exc, (TimeoutError, anyio.get_cancelled_exc_class())):
          raise
      finally:
        measurement_done.set()

    async def run_temperature():
      try:
        temperature_results.append(await device.get_temperature())
      except BaseException as exc:
        temperature_errors.append(exc)
      finally:
        temperature_done.set()

    async with anyio.create_task_group() as tg:
      tg.start_soon(run_measurement)
      await waiting.wait()
      tg.start_soon(run_temperature)
      for _ in range(5):
        await anyio.lowlevel.checkpoint()
      assert not temperature_done.is_set()
      assert io.write.call_args.args[0] == b"S"
      if outcome == "complete":
        release.set()
        await measurement_done.wait()
        await temperature_done.wait()
        (result,) = measurement_results[0]
        assert result.data == [[0.0] * 12 for _ in range(8)]
        assert temperature_results == [23.7]
        assert not replay.events
      else:
        remaining = list(replay.events)
        if outcome == "cancel":
          measurement_scopes[0].cancel()
        await measurement_done.wait()
        await temperature_done.wait()
        assert measurement_errors or measurement_scopes[0].cancelled_caught
        assert len(temperature_errors) == 1
        assert isinstance(temperature_errors[0], RuntimeError)
        assert "Device not initialized" in str(temperature_errors[0])
        io.stop.assert_awaited()
        assert list(replay.events) == remaining
