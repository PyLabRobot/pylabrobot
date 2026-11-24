import math
import unittest
from dataclasses import replace
from datetime import datetime
from struct import pack
from unittest.mock import AsyncMock, patch

from pylabrobot.lib.absorbance.results import AbsorbanceResult
from pylabrobot.resources.biorad.plates import biorad_96_wellplate_200uL_Vb
from pylabrobot.tecan import InfiniteM1000Pro, infinite_m1000_pro


def reader_frame(text, sequence=0):
  body = b"\x02" + text.encode("ascii") + b"\x03"
  checksum = 0
  for byte in body:
    checksum ^= byte
  return body + bytes([sequence]) + (len(text) + 1).to_bytes(2, "big") + bytes([checksum, 13])


async def connect_without_initialization(device):
  """Isolate connection and tray tests from active initialization."""
  with (
    patch.object(device, "_initialize_locked", new_callable=AsyncMock),
    patch.object(device, "_startup_reference_sequence", new_callable=AsyncMock),
  ):
    await device.setup()


class InfiniteM1000ProTests(unittest.IsolatedAsyncioTestCase):
  def test_encode_matches_captured_nonzero_sequence_command(self):
    expected = bytes.fromhex("02 50 52 45 50 41 52 45 20 52 45 46 03 7a 00 0b 31 0d")
    self.assertEqual(infinite_m1000_pro._encode("PREPARE REF", sequence=0x7A), expected)

  def test_captured_nonzero_sequence_text_response(self):
    # USBPcap FirstScriptTest, response to sequence 0x5c.
    frame = bytes.fromhex("02 35 03 5c 00 02 34 0d")
    self.assertEqual(infinite_m1000_pro._decode(frame, expected_sequence=0x5C), "5")
    with self.assertRaisesRegex(ValueError, "sequence"):
      infinite_m1000_pro._decode(frame, expected_sequence=0x5D)

  def test_captured_text_response_rejects_bad_declared_length(self):
    frame = bytearray.fromhex("02 35 03 5c 00 02 34 0d")
    frame[-3] = 3
    with self.assertRaisesRegex(ValueError, "length"):
      infinite_m1000_pro._decode(bytes(frame), expected_sequence=0x5C)

  def test_captured_nonzero_sequence_binary_response(self):
    block = bytes.fromhex("01 00 00 00 14 00 00 00 00 28 00 00 00 28 04 00 00 00 c3 50 7a 00 14 82")
    self.assertEqual(
      infinite_m1000_pro._decode_binary_block(block, 20, expected_sequence=0x7A), block[:20]
    )
    with self.assertRaisesRegex(ValueError, "sequence"):
      infinite_m1000_pro._decode_binary_block(block, 20, expected_sequence=0x7B)

  def test_captured_binary_response_rejects_bad_declared_length(self):
    block = bytearray.fromhex(
      "01 00 00 00 14 00 00 00 00 28 00 00 00 28 04 00 00 00 c3 50 7a 00 14 82"
    )
    block[-2] = 21
    with self.assertRaisesRegex(ValueError, "length"):
      infinite_m1000_pro._decode_binary_block(bytes(block), 20, expected_sequence=0x7A)

  def test_validate_complete_synthetic_absorbance_binary_burst(self):
    header = pack(">HHBBIIHI", 256, 0, 20, 0, 40, 20, 1040, 25000)
    first = bytes(range(20))
    second = bytes(range(20, 40))
    events = ["20,BIN:", header, "20,BIN:", first, "MSG4:working", "20,BIN:", second, "ST"]
    blocks = infinite_m1000_pro._validate_absorbance_burst(events, expected_type=1040)
    self.assertEqual(blocks, [first, second])

  def test_validate_absorbance_burst_rejects_mismatches(self):
    header = pack(">HHBBIIHI", 256, 0, 20, 0, 40, 20, 1040, 25000)
    block = bytes(range(20))
    cases = [
      (["20,BIN:", header, "20,BIN:", block, "ST"], 1040, "Incomplete"),
      (["20,BIN:", header, "19,BIN:", block, "ST"], 1040, "length"),
      (["20,BIN:", header, "20,BIN:", block, "20,BIN:", block], 1040, "standby"),
      (["20,BIN:", header, "20,BIN:", block, "20,BIN:", block, "ST"], 1024, "type"),
    ]
    for events, kind, message in cases:
      with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
        infinite_m1000_pro._validate_absorbance_burst(events, expected_type=kind)

  def test_full_plate_four_channel_packet_mapping(self):
    plate = biorad_96_wellplate_200uL_Vb("plate")
    cal = infinite_m1000_pro._decode_absorbance_prepare_4channel(
      bytes.fromhex(
        "0001 1770 00000000 0000 0001 0001 0001 0001 0064 0064 0064 0064 "
        "076c 076c 076c 076c 0001 0064 07d0"
      )
    )
    packets = []
    expected = {}
    for scan_index in range(24):
      column = scan_index + 1 if scan_index < 12 else 24 - scan_index
      row_offset = 0 if scan_index < 12 else 4
      base = 500 + 4 * scan_index
      counts = (base, base + 1, base + 2, base + 3)
      item = infinite_m1000_pro._AbsorbanceRawItem4Channel(0, 0, counts, 2000)
      packet = infinite_m1000_pro._AbsorbanceRawPacket4Channel(1, 6000, None, [item])
      packets.append(packet)
      for channel in range(4):
        well = f"{'ABCDEFGH'[row_offset + channel]}{column}"
        expected[well] = infinite_m1000_pro._calculate_absorbance_4channel(
          cal, packet, channel=channel
        )
    now = datetime(2026, 1, 1)
    result = infinite_m1000_pro._assemble_full_plate_absorbance(plate, cal, packets, 600, now, now)
    self.assertEqual(
      result.data,
      {key: expected[key] for key in sorted(expected, key=lambda x: (x[0], int(x[1:])))},
    )
    self.assertEqual(len(result.data), 96)

  def test_full_plate_mapping_rejects_incomplete_and_wrong_wavelength(self):
    plate = biorad_96_wellplate_200uL_Vb("plate")
    cal = infinite_m1000_pro._decode_absorbance_prepare_4channel(
      bytes.fromhex(
        "0001 1770 00000000 0000 0001 0001 0001 0001 0064 0064 0064 0064 "
        "076c 076c 076c 076c 0001 0064 07d0"
      )
    )
    packet = infinite_m1000_pro._decode_absorbance_data_4channel(
      bytes.fromhex("0001 1770 00000000 0000 03e8 03e8 03e8 03e8 07d0")
    )
    now = datetime(2026, 1, 1)
    with self.assertRaisesRegex(ValueError, "24"):
      infinite_m1000_pro._assemble_full_plate_absorbance(plate, cal, [packet] * 23, 600, now, now)
    with self.assertRaisesRegex(ValueError, "wavelength"):
      infinite_m1000_pro._assemble_full_plate_absorbance(plate, cal, [packet] * 24, 500, now, now)

  def test_absorbance_calculation_from_synthetic_four_channel_counts(self):
    calibration = infinite_m1000_pro._decode_absorbance_prepare_4channel(
      bytes.fromhex(
        "0001 1770 00000000 0000 "
        "0001 0001 0001 0001 0064 0064 0064 0064 "
        "076c 076c 076c 076c 0001 0064 07d0"
      )
    )
    measurement = infinite_m1000_pro._decode_absorbance_data_4channel(
      bytes.fromhex("0001 1770 00000000 0000 03e8 03e8 03e8 03e8 07d0")
    )
    result = infinite_m1000_pro._calculate_absorbance_4channel(calibration, measurement, channel=0)
    self.assertAlmostEqual(result, 0.3010299956639812)

  def test_absorbance_calculation_rejects_invalid_inputs(self):
    calibration = infinite_m1000_pro._decode_absorbance_prepare_4channel(
      bytes.fromhex(
        "0001 1770 00000000 0000 "
        "0001 0001 0001 0001 0064 0064 0064 0064 "
        "076c 076c 076c 076c 0001 0064 07d0"
      )
    )
    measurement = infinite_m1000_pro._decode_absorbance_data_4channel(
      bytes.fromhex("0001 1770 00000000 0000 03e8 03e8 03e8 03e8 07d0")
    )
    cases = [
      (calibration, measurement, 4, "channel"),
      (calibration, replace(measurement, label=2), 0, "differ"),
      (replace(calibration, items=[]), measurement, 0, "empty"),
      (calibration, replace(measurement, items=[]), 0, "empty"),
    ]
    for cal, data, channel, error in cases:
      with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
        infinite_m1000_pro._calculate_absorbance_4channel(cal, data, channel=channel)
    low = replace(measurement, items=[replace(measurement.items[0], reference_count=100)])
    self.assertTrue(
      math.isnan(infinite_m1000_pro._calculate_absorbance_4channel(calibration, low, channel=0))
    )
    over = replace(measurement, items=[replace(measurement.items[0], reference_count=65535)])
    self.assertEqual(
      infinite_m1000_pro._calculate_absorbance_4channel(calibration, over, channel=0),
      infinite_m1000_pro.OVER,
    )

  def test_format_absorbance_result_uses_shared_plr_result_and_well_names(self):
    plate = biorad_96_wellplate_200uL_Vb("plate")
    start = datetime(2026, 1, 1, 12, 0)
    end = datetime(2026, 1, 1, 12, 1)
    result = infinite_m1000_pro._format_absorbance_result(
      plate, [plate.get_item("H12"), plate.get_item("A1")], [0.7, 0.2], 600, start, end
    )
    self.assertIsInstance(result, AbsorbanceResult)
    self.assertEqual(list(result.data.items()), [("A1", 0.2), ("H12", 0.7)])
    self.assertEqual((result.wavelength, result.temperature), (600, None))
    self.assertEqual((result.start_timestamp, result.end_timestamp), (start, end))

  def test_format_absorbance_full_plate_preserves_well_value_mapping(self):
    plate = biorad_96_wellplate_200uL_Vb("plate")
    wells = plate.get_all_items()
    values = [float(index) for index in range(len(wells))]
    now = datetime(2026, 1, 1)
    result = infinite_m1000_pro._format_absorbance_result(plate, wells, values, 600, now, now)
    self.assertEqual(len(result.data), 96)
    self.assertEqual(next(iter(result.data)), "A1")
    self.assertEqual(list(result.data)[-1], "H12")
    self.assertEqual(
      result.data, {well.get_identifier(): value for well, value in zip(wells, values)}
    )

  def test_format_absorbance_result_rejects_bad_well_value_pairs(self):
    plate = biorad_96_wellplate_200uL_Vb("plate")
    other = biorad_96_wellplate_200uL_Vb("other")
    now = datetime(2026, 1, 1)
    cases = [
      ([plate.get_item("A1")], [], "value"),
      ([other.get_item("A1")], [0.1], "belong"),
      ([plate.get_item("A1"), plate.get_item("A1")], [0.1, 0.2], "Duplicate"),
    ]
    for wells, values, message in cases:
      with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
        infinite_m1000_pro._format_absorbance_result(plate, wells, values, 600, now, now)

  def test_replay_captured_prepare_transaction(self):
    # Exact USBPcap PREPARE response from the i-control FirstScriptTest capture.
    wire = bytes.fromhex(
      "02 32 30 2c 42 49 4e 3a 03 7a 00 08 50 0d "
      "01 00 00 00 14 00 00 00 00 28 00 00 00 28 04 00 00 00 c3 50 7a 00 14 82 "
      "02 34 30 2c 42 49 4e 3a 03 7a 00 08 56 0d "
      "00 01 17 70 00 02 45 bc f6 25 00 45 00 a1 00 37 00 3a "
      "02 e0 06 ba 02 96 02 7b c6 d9 c4 76 c6 7f c5 b7 "
      "00 47 06 c7 c5 30 7a 00 28 01 "
      "02 53 54 03 7a 00 03 06 0d"
    )
    parser = infinite_m1000_pro._ReaderStreamParser(expected_sequence=0x7A)
    events = []
    for offset in range(0, len(wire), 7):
      events.extend(parser.feed(wire[offset : offset + 7]))
    blocks = infinite_m1000_pro._validate_absorbance_burst(events, expected_type=1024)
    cal = infinite_m1000_pro._decode_absorbance_prepare_4channel(blocks[0])
    self.assertEqual((cal.label, cal.ex_wavelength_raw), (1, 6000))
    self.assertEqual(cal.items[0].measurement_gain, (69, 161, 55, 58))
    self.assertEqual(cal.items[0].reference_bright, 50480)

  def test_stream_replays_captured_nonzero_binary_header(self):
    parser = infinite_m1000_pro._ReaderStreamParser(expected_sequence=0x7A)
    data = bytes.fromhex(
      "02 32 30 2c 42 49 4e 3a 03 7a 00 08 50 0d "
      "01 00 00 00 14 00 00 00 00 28 00 00 00 28 04 00 00 00 c3 50 7a 00 14 82"
    )
    events = []
    for i in range(0, len(data), 3):
      events.extend(parser.feed(data[i : i + 3]))
    self.assertEqual(events[0], "20,BIN:")
    header = infinite_m1000_pro._decode_binary_header(events[1])
    self.assertEqual((header.data_type, header.data_size), (1024, 40))

  def test_binary_stream_parses_fragmented_and_coalesced_frames(self):
    parser = infinite_m1000_pro._ReaderStreamParser()
    payload = b"\x02\x03\x0d\x10"
    block = payload + bytes([0, 0, 4, 0x1C])
    wire = reader_frame("4,BIN:") + block + reader_frame("ST")
    output = []
    for byte in wire:
      output.extend(parser.feed(bytes([byte])))
    self.assertEqual(output, ["4,BIN:", payload, "ST"])

  def test_binary_stream_rejects_corrupted_and_oversized_blocks(self):
    parser = infinite_m1000_pro._ReaderStreamParser()
    with self.assertRaisesRegex(ValueError, "checksum"):
      parser.feed(reader_frame("1,BIN:") + b"\x01\x00\x00\x00\x00")
    parser = infinite_m1000_pro._ReaderStreamParser()
    with self.assertRaisesRegex(ValueError, "too large"):
      parser.feed(reader_frame("1048577,BIN:"))

  def test_decode_synthetic_binary_block(self):
    # Binary.ReceiveBinary checks the payload XOR and the trailer sequence.
    payload = bytes.fromhex("10 20 30")
    block = payload + bytes.fromhex("00 00 03 00")
    self.assertEqual(infinite_m1000_pro._decode_binary_block(block, 3), payload)

  def test_binary_block_rejects_corruption_error_and_incomplete_data(self):
    for block, message in (
      (bytes.fromhex("10 20 30 00 00 03 01"), "checksum"),
      (bytes.fromhex("10 20 30 01 00 03 00"), "sequence"),
      (bytes.fromhex("10 20 30 00"), "length"),
    ):
      with self.subTest(block=block), self.assertRaisesRegex(ValueError, message):
        infinite_m1000_pro._decode_binary_block(block, 3)

  def test_binary_header_rejects_invalid_identifier_size_and_truncation(self):
    valid = bytes.fromhex("0100 0001 14 00 00000100 00000040 0042 00001388")
    for header in (b"", b"\x00" + valid[1:], valid[:4] + b"\x13" + valid[5:]):
      with self.subTest(header=header), self.assertRaises(ValueError):
        infinite_m1000_pro._decode_binary_header(header)

  def test_decode_synthetic_binary_header(self):
    # Vendor BinaryDataHeader: big-endian, with a 20-byte header marker.
    header = bytes.fromhex("0100 0001 14 00 00000100 00000040 0042 00001388")
    result = infinite_m1000_pro._decode_binary_header(header)
    self.assertEqual((result.flags, result.data_size, result.max_block_size), (1, 256, 64))
    self.assertEqual((result.data_type, result.timeout_raw), (0x42, 5000))

  def test_decode_synthetic_four_channel_absorbance_data(self):
    # Synthetic bytes following vendor binary-decoder field order; not captured results.
    packet = bytes.fromhex("0002 1770 01020304 0506 0102 0304 0506 0708 090a")
    result = infinite_m1000_pro._decode_absorbance_data_4channel(packet)
    self.assertEqual(result.label, 2)
    self.assertEqual(result.ex_wavelength_raw, 6000)
    self.assertEqual(len(result.items), 1)
    self.assertEqual(result.items[0].ticker_overflows, 0x01020304)
    self.assertEqual(result.items[0].ticker_counter, 0x0506)
    self.assertEqual(result.items[0].measurement_counts, (0x0102, 0x0304, 0x0506, 0x0708))
    self.assertEqual(result.items[0].reference_count, 0x090A)

  def test_decode_synthetic_four_channel_multi_packet(self):
    payload = bytes.fromhex("0002 1770 0003 01020304 0506 0102 0304 0506 0708 090a")
    result = infinite_m1000_pro._decode_absorbance_data_4channel(payload, multi_packet=True)
    self.assertEqual(
      (result.label, result.ex_wavelength_raw, result.number_of_packets), (2, 6000, 3)
    )
    self.assertEqual(result.items[0].reference_count, 0x090A)

  def test_decode_synthetic_four_channel_absorbance_prepare(self):
    # Synthetic raw calibration values, not an instrument capture.
    payload = bytes.fromhex(
      "0001 1770 01020304 1234 "
      "0001 0002 0003 0004 0011 0012 0013 0014 "
      "0021 0022 0023 0024 0031 0032 0033"
    )
    result = infinite_m1000_pro._decode_absorbance_prepare_4channel(payload)
    self.assertEqual((result.label, result.ex_wavelength_raw), (1, 6000))
    item = result.items[0]
    self.assertEqual((item.ticker_overflows, item.ticker_counter), (0x01020304, 0x1234))
    self.assertEqual(item.measurement_gain, (1, 2, 3, 4))
    self.assertEqual(item.measurement_dark, (17, 18, 19, 20))
    self.assertEqual(item.measurement_bright, (33, 34, 35, 36))
    self.assertEqual(
      (item.reference_gain, item.reference_dark, item.reference_bright), (49, 50, 51)
    )

  def test_four_channel_decoder_rejects_partial_packets(self):
    for payload in (b"", b"\x00\x01\x02", b"\x00\x01\x02\x03\x04"):
      with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "length"):
        infinite_m1000_pro._decode_absorbance_data_4channel(payload)

  async def test_command_sequence_wraps_after_254(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      device = InfiniteM1000Pro()
      device._next_sequence = 254
      await device._write_command("QQ")
      await device._write_command("QQ")
      self.assertEqual([call.args[0][-5] for call in usb.write.await_args_list], [254, 0])

  async def test_stale_sequence_raises_but_session_stays_usable(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("IN", 254))
      device = InfiniteM1000Pro()
      device._ready = True
      with self.assertRaisesRegex(ValueError, "sequence"):
        await device.open()
      self.assertTrue(device._ready)
      self.assertEqual(usb.write.await_count, 1)

  async def test_request_instrument_status_reports_busy(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("BY#T5000"))
      device = InfiniteM1000Pro()
      device._ready = True
      device._initialized = True
      self.assertEqual(await device.request_instrument_status(), "busy")
      self.assertIn(b"QQ", usb.write.await_args_list[0].args[0])

  async def test_request_current_temperature_converts_tenths_celsius(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("253"))
      device = InfiniteM1000Pro()
      device._ready = True
      device._initialized = True
      self.assertEqual(await device.request_current_temperature(), 25.3)
      self.assertIn(b"?TEMPERATURE PLATE,CURRENT", usb.write.await_args_list[0].args[0])

  async def test_temperature_queries_use_incrementing_sequences(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      sent = []

      async def write(frame):
        sent.append(frame)

      async def read(timeout):
        del timeout
        value = "310" if len(sent) == 1 else "ON"
        return reader_frame(value, sequence=sent[-1][-5])

      usb.write = AsyncMock(side_effect=write)
      usb.read = AsyncMock(side_effect=read)
      device = InfiniteM1000Pro()
      device._ready = True
      device._initialized = True
      await device.set_temperature(31.0)
      self.assertEqual([frame[-5] for frame in sent], [0, 1])

  async def test_set_temperature_is_idempotent_when_already_at_target(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(side_effect=[reader_frame("310"), reader_frame("ON", 1)])
      device = InfiniteM1000Pro()
      device._ready = True
      device._initialized = True
      await device.set_temperature(31.0)
      commands = [
        call.args[0][1 : call.args[0].index(b"\x03")].decode() for call in usb.write.await_args_list
      ]
      self.assertEqual(commands, ["?TEMPERATURE PLATE,TARGET", "?TEMPERATURE PLATE,STATUS"])

  async def test_set_temperature_sets_target_and_enables_with_readback(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame(s, i) for i, s in enumerate(("250", "OFF", "ST", "300", "ST", "ON"))
        ]
      )
      device = InfiniteM1000Pro()
      device._ready = True
      device._initialized = True
      await device.set_temperature(30.0)
      commands = [
        call.args[0][1 : call.args[0].index(b"\x03")].decode() for call in usb.write.await_args_list
      ]
      self.assertEqual(
        commands,
        [
          "?TEMPERATURE PLATE,TARGET",
          "?TEMPERATURE PLATE,STATUS",
          "TEMPERATURE PLATE,TARGET=300",
          "?TEMPERATURE PLATE,TARGET",
          "TEMPERATURE PLATE=ON",
          "?TEMPERATURE PLATE,STATUS",
        ],
      )

  async def test_temperature_failed_readback_raises(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("250", 0),
          reader_frame("OFF", 1),
          reader_frame("ST", 2),
          reader_frame("299", 3),
        ]
      )
      device = InfiniteM1000Pro()
      device._ready = True
      with self.assertRaisesRegex(RuntimeError, "target was not confirmed"):
        await device.set_temperature(30.0)
      self.assertTrue(device._ready)
      self.assertEqual(usb.write.await_count, 4)

  async def test_temperature_has_no_software_range_limit(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      device = InfiniteM1000Pro()
      device._ready = True
      for temperature in (float("nan"), float("inf")):
        with self.subTest(temperature=temperature), self.assertRaises(ValueError):
          await device.set_temperature(temperature)
      usb.write.assert_not_awaited()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("250", 0),
          reader_frame("ON", 1),
          reader_frame("ST", 2),
          reader_frame("450", 3),
        ]
      )
      await device.set_temperature(45.0)
      self.assertEqual(
        usb.write.await_args_list[2].args[0],
        infinite_m1000_pro._encode("TEMPERATURE PLATE,TARGET=450", sequence=2),
      )

  async def test_stop_temperature_control_is_idempotent(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("OFF"))
      device = InfiniteM1000Pro()
      device._ready = True
      device._initialized = True
      await device.stop_temperature_control()
      self.assertEqual(usb.write.await_count, 1)
      self.assertIn(b"?TEMPERATURE PLATE,STATUS", usb.write.await_args_list[0].args[0])

  async def test_stop_temperature_control_sends_off_and_verifies(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[reader_frame("ON", 0), reader_frame("ST", 1), reader_frame("OFF", 2)]
      )
      device = InfiniteM1000Pro()
      device._ready = True
      device._initialized = True
      await device.stop_temperature_control()
      commands = [
        call.args[0][1 : call.args[0].index(b"\x03")].decode() for call in usb.write.await_args_list
      ]
      self.assertEqual(
        commands,
        ["?TEMPERATURE PLATE,STATUS", "TEMPERATURE PLATE=OFF", "?TEMPERATURE PLATE,STATUS"],
      )

  async def test_setup_connects_and_initializes_once(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("ST", sequence=0),
          reader_frame("BY#T85000", sequence=1),
          reader_frame("ST", sequence=1),
        ]
      )
      device = InfiniteM1000Pro()
      with patch.object(device, "_startup_reference_sequence", new_callable=AsyncMock):
        await device.setup()
        await device.setup()
      self.assertTrue(device._initialized)
      self.assertEqual(usb.setup.await_count, 1)
      self.assertEqual(usb.write.await_count, 2)

  async def test_initialize_then_open_with_mocked_transport(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("ST"),
          reader_frame("BY#T85000", 1),
          reader_frame("MSG9:test", 1),
          reader_frame("ST", 1),
          reader_frame("IN", 2),
          reader_frame("BY", 3) + reader_frame("ST", 3),
          reader_frame("OUT", 4),
        ]
      )
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      await device.initialize()
      await device.open()
      self.assertEqual(usb.write.await_count, 5)

  async def test_tray_moves_without_initialization(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[reader_frame("IN"), reader_frame("ST", 1), reader_frame("OUT", 2)]
      )
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      await device.open()
      self.assertEqual(usb.write.await_count, 3)

  async def test_initialize_discards_unexpected_buffered_response(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("ST") + reader_frame("ST"),
          reader_frame("BY", 1),
          reader_frame("ST", 1),
        ]
      )
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      await device.initialize()
      self.assertTrue(device._initialized)
      self.assertEqual(usb.write.await_count, 2)

  async def test_initialize_discards_extra_response_after_completion(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("ST"),
          reader_frame("BY", 1),
          reader_frame("ST", 1) + reader_frame("OUT", 1),
        ]
      )
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      await device.initialize()
      self.assertTrue(device._initialized)
      self.assertFalse(device._buffer)

  async def test_initialize_resets_busy_reader(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[reader_frame("BY"), reader_frame("BY", 1), reader_frame("ST", 1)]
      )
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      await device.initialize()
      self.assertEqual(
        usb.write.await_args_list[1].args[0], infinite_m1000_pro._encode("INIT FORCE", sequence=1)
      )

  async def test_initialize_timeout_raises_and_allows_retry(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("ST"),
          reader_frame("BY#T85000", 1),
          TimeoutError(),
          reader_frame("ST", 2),
          reader_frame("BY", 3),
          reader_frame("ST", 3),
        ]
      )
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      with self.assertRaises(TimeoutError):
        await device.initialize()
      self.assertFalse(device._initialized)
      await device.initialize()
      self.assertTrue(device._initialized)

  async def test_initialize_waits_for_completion_once_per_session(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("ST"),
          reader_frame("BY#T85000", 1),
          reader_frame("MSG9:initializing", 1) + reader_frame("ST", 1),
        ]
      )
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      await device.initialize()
      await device.initialize()
      self.assertEqual(len(usb.write.await_args_list), 2)
      self.assertIn(b"QQ", usb.write.await_args_list[0].args[0])
      self.assertIn(b"INIT FORCE", usb.write.await_args_list[1].args[0])

  async def test_setup_opens_standard_usb_transport(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      usb.setup.assert_awaited_once_with(empty_buffer=False)
      self.assertEqual(usb_class.call_args.kwargs["id_vendor"], 0x0C47)
      self.assertEqual(usb_class.call_args.kwargs["id_product"], 0x8009)
      self.assertEqual(usb_class.call_args.kwargs["read_endpoint_address"], 0x81)
      self.assertEqual(usb_class.call_args.kwargs["write_endpoint_address"], 0x02)

  async def test_repeated_setup_does_not_reconnect_or_discard_pending_input(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      device._buffer.extend(reader_frame("ST"))
      await connect_without_initialization(device)
      usb.setup.assert_awaited_once_with(empty_buffer=False)
      self.assertEqual(device._buffer, reader_frame("ST"))

  async def test_failed_reconnect_can_be_retried(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock(side_effect=[None, RuntimeError("usb failure"), None])
      usb.stop = AsyncMock()
      usb.write = AsyncMock()
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      await device.stop()
      with self.assertRaisesRegex(RuntimeError, "usb failure"):
        await connect_without_initialization(device)
      with self.assertRaisesRegex(RuntimeError, "not ready"):
        await device.open()
      await connect_without_initialization(device)
      self.assertTrue(device._ready)
      self.assertEqual(usb.setup.await_count, 3)

  async def test_open_skips_informational_frames_before_command_response(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("MSG4:initializing") + reader_frame("IN"),
          reader_frame("BY#T85000", 1),
          reader_frame("LOG:working", 1) + reader_frame("ST", 1),
          reader_frame("OUT", 2),
        ]
      )
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      device._initialized = True  # Simulate prior successful initialization in tray-only tests.
      await device.open()
      self.assertEqual(usb.write.await_count, 3)

  async def test_receive_does_not_truncate_remaining_timeout(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.read = AsyncMock(return_value=reader_frame("ST"))
      device = InfiniteM1000Pro()
      with patch("pylabrobot.tecan.infinite_m1000_pro.time") as clock:
        clock.monotonic.side_effect = [100.0, 100.1, 100.1, 100.2]
        self.assertEqual(await device._receive(), "ST")
      self.assertGreaterEqual(usb.read.await_args_list[0].kwargs["timeout"], 29.9)

  async def test_oversized_complete_response_raises(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("MSG" + "x" * 4100) + reader_frame("OUT"))
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      with self.assertRaisesRegex(ValueError, "too long"):
        await device.open()
      self.assertEqual(usb.write.await_count, 1)

  async def test_open_already_out_is_idempotent(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("OUT"))
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      device._initialized = True  # Simulate prior successful initialization in tray-only tests.
      await device.open()
      self.assertEqual(usb.write.await_count, 1)

  async def test_close_handles_fragmented_and_coalesced_responses(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      position = reader_frame("OUT")
      usb.read = AsyncMock(
        side_effect=[
          position[:3],
          position[3:],
          reader_frame("BY", 1) + reader_frame("ST", 1),
          reader_frame("IN", 2),
        ]
      )
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      device._initialized = True  # Isolate tray behavior from initialization.
      await device.close()
      self.assertEqual(usb.write.await_count, 3)
      self.assertIn(b"ABSOLUTE MTP,IN", usb.write.await_args_list[1].args[0])

  async def test_unknown_position_still_moves(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[reader_frame("UNKNOWN"), reader_frame("ST", 1), reader_frame("OUT", 2)]
      )
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      await device.open()
      self.assertEqual(
        usb.write.await_args_list[1].args[0],
        infinite_m1000_pro._encode("ABSOLUTE MTP,OUT", sequence=1),
      )

  async def test_session_can_reconnect_after_error(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.stop = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(side_effect=[reader_frame("IN"), reader_frame("ER", 1)])
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      with self.assertRaisesRegex(RuntimeError, "Unexpected movement status"):
        await device.open()
      await device.stop()
      await connect_without_initialization(device)
      self.assertEqual(usb.setup.await_count, 2)

  async def test_unexpected_movement_status_fails_without_retry(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(side_effect=[reader_frame("IN"), reader_frame("ER", 1)])
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      device._initialized = True
      with self.assertRaisesRegex(RuntimeError, "Unexpected movement status"):
        await device.open()
      self.assertEqual(usb.write.await_count, 2)

  async def test_checksum_failure_raises(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      corrupt = bytearray(reader_frame("IN"))
      corrupt[-2] ^= 1
      usb.read = AsyncMock(return_value=bytes(corrupt))
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      with self.assertRaisesRegex(ValueError, "checksum"):
        await device.open()
      self.assertTrue(device._ready)
      self.assertEqual(usb.write.await_count, 1)

  async def test_position_mismatch_logs_warning(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[reader_frame("IN"), reader_frame("ST", 1), reader_frame("IN", 2)]
      )
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      with self.assertLogs("pylabrobot.tecan.infinite_m1000_pro", "WARNING"):
        await device.open()

  async def test_stop_closes_transport(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.stop = AsyncMock()
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      await device.stop()
      usb.stop.assert_awaited_once()
      with self.assertRaisesRegex(RuntimeError, "not ready"):
        await device.open()

  async def test_open_checks_position_moves_and_verifies(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("IN"),
          reader_frame("BY", 1),
          reader_frame("ST", 1),
          reader_frame("OUT", 2),
        ]
      )
      device = InfiniteM1000Pro()
      await connect_without_initialization(device)
      device._initialized = True  # Simulate prior successful initialization in tray-only tests.
      await device.open()
      self.assertEqual(
        [call.args[0] for call in usb.write.await_args_list],
        [
          bytes.fromhex("02 3f 41 42 53 4f 4c 55 54 45 20 4d 54 50 2c 50 4f 53 03 00 00 11 20 0d"),
          bytes.fromhex("02 41 42 53 4f 4c 55 54 45 20 4d 54 50 2c 4f 55 54 03 01 00 10 1d 0d"),
          bytes.fromhex("02 3f 41 42 53 4f 4c 55 54 45 20 4d 54 50 2c 50 4f 53 03 02 00 11 20 0d"),
        ],
      )
