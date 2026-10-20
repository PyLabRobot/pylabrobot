"""Synergy HT command sequences, measurement parsing, and protocol failure cases."""

import asyncio
import json
import math
from collections import deque
from datetime import datetime
from functools import wraps
from pathlib import Path
from unittest import TestCase
from unittest.mock import AsyncMock, patch

import pytest

from pylabrobot.agilent.biotek.plate_reader_base import AbsorbanceResult
from pylabrobot.agilent.biotek.synergy_ht import SynergyHT
from pylabrobot.io.ftdi import FTDI

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


def async_test(function):
  """Run an async test without an additional pytest event-loop plugin."""

  @wraps(function)
  def run(*args, **kwargs):
    return asyncio.run(function(*args, **kwargs))

  return run


@pytest.fixture
def reader():
  """Replace every transport method; no test can open hardware."""
  with patch("pylabrobot.agilent.biotek._reader.FTDI", autospec=FTDI) as transport:
    device = SynergyHT()
    device._temperature_range = (22, 50)
    io = transport.return_value

    async def disconnect():
      """Model FTDI rejecting I/O once stop() closes its handle."""
      io.write.side_effect = RuntimeError("Device not initialized. Call setup() first.")
      io.read.side_effect = RuntimeError("Device not initialized. Call setup() first.")

    io.stop.side_effect = disconnect
    with patch("pylabrobot.agilent.biotek.synergy_ht.asyncio.sleep", new_callable=AsyncMock):
      yield device, io


def attach(io, transcript, chunk_size=4096):
  """Attach a strict replay to a mocked PLR FTDI transport."""
  replay = Replay(transcript, chunk_size)
  io.read.side_effect = replay.read
  io.write.side_effect = replay.write
  return replay


@pytest.mark.parametrize("chunk_size", [1, 2, 61, 64, 4096])
@pytest.mark.parametrize("read", FIXTURES["reads"], ids=lambda x: f"{x['rows']}x{x['columns']}")
@async_test
async def test_endpoint_sequence(reader, read, chunk_size):
  device, io = reader
  replay = attach(io, read["events"], chunk_size)
  plate_type = "24-well" if read["rows"] == 4 else "96-well"
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
def test_absorbance_measurements(measurement):
  data, _ = SynergyHT._parse_absorbance(
    bytes.fromhex(measurement["result"]), measurement["rows"], measurement["columns"]
  )
  assert data == [[0.0] * measurement["columns"] for _ in range(measurement["rows"])]


@pytest.mark.parametrize("wavelength,expected", ABSORBANCE_SETUPS.items())
def test_absorbance_setup_encoding(wavelength, expected):
  assert SynergyHT._absorbance_setup(int(wavelength)) == bytes.fromhex(expected)


@pytest.mark.parametrize(
  "setup", SHAKING["setups"], ids=lambda x: f"{x['duration']}s-{x['intensity']}"
)
def test_shake_setup_encoding(setup):
  assert SynergyHT._absorbance_setup(600, setup["duration"], setup["intensity"]) == bytes.fromhex(
    setup["hex"]
  )


@pytest.mark.parametrize(
  "setup",
  SHAKING["duration_setups"],
  ids=lambda x: f"{x['wavelength']}nm-{x['duration']}s-{x['intensity']}",
)
def test_shake_duration_encoding(setup):
  assert SynergyHT._absorbance_setup(
    setup["wavelength"], setup["duration"], setup["intensity"]
  ) == bytes.fromhex(setup["hex"])


@pytest.mark.parametrize("chunk_size", [1, 2, 61, 64, 4096])
@async_test
async def test_shake_then_read_without_shaking(reader, chunk_size):
  device, io = reader
  read = SHAKING["read"]
  replay = attach(io, read["events"] + FIXTURES["reads"][0]["events"], chunk_size)
  with patch("asyncio.wait_for", wraps=asyncio.wait_for) as wait_for:
    (result,) = await device.read_absorbance(
      600,
      "96-well",
      shake_duration=read["duration"],
      shake_intensity=read["intensity"],
    )
    assert wait_for.call_args.args[1] == device.read_timeout + read["duration"]
    assert result.data == [[0.0] * 12 for _ in range(8)]
    assert result.temperature == 32.0
    (unshaken,) = await device.read_absorbance(600, "96-well")
    assert wait_for.call_args.args[1] == device.read_timeout
    assert unshaken.data == [[0.0] * 12 for _ in range(8)]
  assert not replay.events


@async_test
async def test_setup_and_idempotent_lifecycle(reader):
  device, io = reader
  with pytest.raises(RuntimeError, match="Firmware version is not set"):
    _ = device.version
  replay = attach(io, setup_events() * 2, 1)
  with TestCase().assertNoLogs(SynergyHT.__module__, level="WARNING"):
    await device.setup()
    await device.setup()
  assert not replay.events
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
  await device.stop()
  await device.stop()
  assert io.stop.await_count == 2
  with pytest.raises(RuntimeError, match="Firmware version is not set"):
    _ = device.version
  assert device.serial_number is None
  assert device.temperature_range is None


@pytest.mark.parametrize("failure", ["model", "rdop22", "capability", "status", "range"])
@async_test
async def test_setup_rejects_unsupported_or_malformed_responses(reader, failure):
  device, io = reader
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
    await device.setup()
  io.stop.assert_awaited_once()


@async_test
async def test_setup_after_power_on(reader):
  """Initialize a reader whose power-on statuses carry the 0x1E flag until n 1."""
  device, io = reader
  replay = attach(io, setup_events(FIXTURES["power_on"]), 1)
  await device.setup()
  assert not replay.events
  assert device.version == "2.24"
  assert device.serial_number == "196804"
  assert device.temperature_range == (22, 50)


def test_power_on_status_still_reports_errors():
  SynergyHT._check_status(b"\x1e000\x03")
  with pytest.raises(RuntimeError, match="0x0001"):
    SynergyHT._check_status(b"\x1e001\x03")
  with pytest.raises(ValueError):
    SynergyHT._check_status(b"\x1f000\x03")


@async_test
async def test_setup_discards_output_queued_for_an_earlier_connection(reader):
  device, io = reader
  stale = events(("rx", b"0000\x03\x067090202  Version 2.24   0000\x03"))
  replay = attach(io, stale + setup_events(), 4)
  with TestCase().assertLogs(SynergyHT.__module__, level="WARNING") as logs:
    await device.setup()
  assert not replay.events
  assert device.version == "2.24"
  assert any("Discarded 35 stale bytes" in message for message in logs.output)


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


def test_signed_milliod_overflow_and_row_order():
  frame = result_frame([[b"+1234", b"-0012"], [b"+4567", b"*****"]])
  values, temperature = SynergyHT._parse_absorbance(frame, 2, 2)
  assert values[0] == [1.234, -0.012]
  assert values[1][0] == 4.567
  assert math.isnan(values[1][1])
  assert temperature == 23.7


@pytest.mark.parametrize("target", [3, 26])
def test_checksum_can_equal_etx_or_sub(target):
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
def test_corrupt_results_are_rejected(mutation):
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
@async_test
async def test_selection_reads_bounding_rectangle_and_masks_other_wells(
  reader, temperature, expected_temperature
):
  device, io = reader
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
@async_test
async def test_invalid_assay_does_not_write(reader, kwargs):
  device, io = reader
  params = {"wavelength": 600, "plate_type": "96-well", **kwargs}
  with pytest.raises(ValueError):
    await device.read_absorbance(**params)
  io.write.assert_not_awaited()
  io.stop.assert_not_awaited()


@async_test
async def test_tray_and_temperature(reader):
  device, io = reader
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
@async_test
async def test_invalid_temperature_does_not_write(reader, temperature):
  device, io = reader
  with pytest.raises(ValueError):
    await device.set_temperature(temperature)
  io.write.assert_not_awaited()


@pytest.mark.parametrize("failure", ["nak", "partial_write", "status", "timeout", "cancel"])
@async_test
async def test_failed_exchange_requires_reconnection(reader, failure):
  device, io = reader
  io.write.return_value = 1
  io.read.return_value = b"\x15"
  if failure == "partial_write":
    io.write.return_value = 0
  elif failure == "status":
    attach(io, events(("tx", b"J"), ("rx", b"\x060001\x03")))
  elif failure in ("timeout", "cancel"):
    started = asyncio.Event()

    async def stalled_read(_size):
      started.set()
      await asyncio.Future()

    io.read.side_effect = stalled_read
    device.timeout = 0.01
    task = asyncio.create_task(device.open())
    await started.wait()
    if failure == "cancel":
      task.cancel()
    with pytest.raises((TimeoutError, asyncio.CancelledError)):
      await task
  if failure not in ("timeout", "cancel"):
    with pytest.raises((RuntimeError, IOError)):
      await device.open()
  io.stop.assert_awaited_once()
  reads = io.read.await_count
  with pytest.raises(RuntimeError, match="Device not initialized"):
    await device.close()
  assert io.read.await_count == reads

  # setup() can reconnect and repeat initialization after the failed stream is closed.
  replay = attach(io, setup_events())
  await device.setup()
  assert not replay.events
  assert device.version == "2.24"


@pytest.mark.parametrize("prefix", [b"\x10", b"\x03"])
@async_test
async def test_short_async_error_does_not_wait_for_full_plate(reader, prefix):
  device, io = reader
  replay = attach(io, events(("rx", prefix + b"002A\x03")), 1)
  with pytest.raises(RuntimeError, match="002A"):
    await device._read_absorbance_frame(8, 12)
  assert not replay.events


@pytest.mark.parametrize(
  "outcome,shake_duration",
  [("complete", 0), ("cancel", 0), ("timeout", 0), ("complete", 10), ("cancel", 10)],
)
@async_test
async def test_measurement_owns_connection_until_result_or_failure(reader, outcome, shake_duration):
  device, io = reader
  read = SHAKING["read"] if shake_duration else FIXTURES["reads"][0]
  transcript = read["events"] + events(("tx", b"h"), ("rx", b"\x062370000\x03"))
  replay = attach(io, transcript)
  waiting = asyncio.Event()
  release = asyncio.Event()

  async def gated_read(size):
    """Hold the first measurement byte while another operation tries to run."""
    if replay.events[0][1].startswith(b"\r"):
      waiting.set()
      await release.wait()
    return await replay.read(size)

  io.read.side_effect = gated_read
  device.read_timeout = 0.01 if outcome == "timeout" else 180
  measurement = asyncio.create_task(
    device.read_absorbance(600, "96-well", shake_duration=shake_duration)
  )
  await waiting.wait()
  temperature = asyncio.create_task(device.get_temperature())
  # Yield to the contender without the patched command-settling sleep.
  yielded = asyncio.get_running_loop().create_future()
  asyncio.get_running_loop().call_soon(yielded.set_result, None)
  await yielded
  assert not temperature.done()
  assert io.write.call_args.args[0] == b"S"
  if outcome == "complete":
    release.set()
    (result,) = await measurement
    assert result.data == [[0.0] * 12 for _ in range(8)]
    assert await temperature == 23.7
    assert not replay.events
  else:
    remaining = list(replay.events)
    if outcome == "cancel":
      measurement.cancel()
    with pytest.raises((asyncio.CancelledError, asyncio.TimeoutError)):
      await measurement
    with pytest.raises(RuntimeError, match="Device not initialized"):
      await temperature
    io.stop.assert_awaited()
    assert list(replay.events) == remaining
