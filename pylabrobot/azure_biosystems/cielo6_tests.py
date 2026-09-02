import asyncio
import hashlib
import json
import struct
import unittest
import zlib
from dataclasses import replace
from datetime import timedelta
from typing import cast
from unittest.mock import AsyncMock, patch

from pylabrobot.azure_biosystems.cielo6 import (
  DISCONNECT_COMMAND,
  DISCOVERY_DEVICE_ID,
  EXEC_SUCCESSFUL,
  EXPERIMENT_DATA_FILE_GET_COMMAND,
  EXPERIMENT_DATA_FILE_INFO_GET_COMMAND,
  EXPERIMENT_DATA_SIZE,
  EXPERIMENT_DATA_SUMMARY_GET_COMMAND,
  INITIALIZE_COMMAND,
  PAUSE_COMMAND,
  PROGRAM_CHUNK_SIZE,
  PROGRAM_DELETE_COMMAND,
  PROGRAM_GET_COMMAND,
  PROGRAM_UPLOAD_COMMAND,
  RESULT_PATH_SET_COMMAND,
  RESUME_COMMAND,
  RUN_COMMAND,
  RUNNING_DATA_TYPE_NORMAL,
  RUNNING_EXPERIMENT_DATA_UPLOAD_COMMAND,
  RUNNING_EXPERIMENT_INFOS_GET_COMMAND,
  SESSION_LOCK_COMMAND,
  STATUS_QUERY_COMMAND,
  STOP_COMMAND,
  VERSION_CHECK_COMMAND,
  WORK_STATUS_IDLE,
  WORK_STATUS_PAUSED,
  WORK_STATUS_RUNNING,
  WORKSPACE_CREATE_COMMAND,
  WORKSPACE_DELETE_COMMAND,
  WORKSPACE_SUMMARY_GET_COMMAND,
  Cielo6,
  Cielo6CollectionPoint,
  Cielo6Error,
  Cielo6ExperimentInfo,
  Cielo6FirmwareStateError,
  Cielo6Identity,
  Cielo6MeltingData,
  Cielo6MeltRecord,
  Cielo6ResultFile,
  Cielo6RunningData,
  Cielo6RunTimeoutError,
  Cielo6Status,
  Cielo6StoredProgram,
  Cielo6ThermalProtocol,
  Cielo6ThermalStep,
  Cielo6WorkState,
  CieloFrame,
)
from pylabrobot.io.serial import Serial


def make_device(response: bytes = b"", *, is_setup: bool = True) -> Cielo6:
  io = AsyncMock(spec=Serial)
  io.port = "FAKE"
  receive_buffer = bytearray(response)

  async def read(size: int = 1) -> bytes:
    chunk = bytes(receive_buffer[:size])
    del receive_buffer[:size]
    return chunk

  io.read.side_effect = read
  with patch("pylabrobot.azure_biosystems.cielo6.Serial", return_value=io):
    device = Cielo6(port="FAKE", device_id="12345678")
  device._is_setup = is_setup
  return device


def written_frames(device: Cielo6) -> list[CieloFrame]:
  return [
    CieloFrame.from_bytes(call.args[0]) for call in cast(AsyncMock, device.io.write).call_args_list
  ]


def identity_response(device_id: str = "12345678", name: str = "AZURE CIELO 6") -> bytes:
  return (
    b"Azure QPCR SeriesID:"
    + device_id.encode("ascii")
    + b"Name:"
    + name.ljust(20).encode("ascii")
    + b"&USB"
  )


def program_bytes() -> bytes:
  header = struct.pack(
    "<4s6s6s9H6BHHf6HI",
    b"P123",
    bytes([1, 0, 1, 0, 0, 0]),
    bytes([1, 2, 3, 4, 5, 6]),
    2,
    1,
    1050,
    30,
    1,
    25,
    0,
    3,
    2026,
    8,
    29,
    10,
    30,
    1,
    0,
    650,
    950,
    0.2,
    100,
    200,
    300,
    400,
    500,
    600,
    0,
  )
  step = struct.pack(
    "<HHIHHhhHHHHHH2H16h",
    0x5AF1,
    1,
    30,
    0,
    50,
    -10,
    2,
    0,
    0,
    0,
    0,
    0,
    1,
    7,
    8,
    *range(6000, 6016),
  )
  empty_step = bytes(64)
  body = header + step + empty_step * 29 + b"Test\0".ljust(30, b"\0") + b"Public\0".ljust(30, b"\0")
  return body + zlib.crc32(body).to_bytes(4, byteorder="little")


def workspace_summary_response(*entries: str) -> bytes:
  frames = [
    CieloFrame(
      "12345678", WORKSPACE_SUMMARY_GET_COMMAND, struct.pack("<II", 0, len(entries))
    ).to_bytes()
  ]
  frames.extend(
    CieloFrame(
      "12345678",
      WORKSPACE_SUMMARY_GET_COMMAND,
      struct.pack("<I", index) + entry.encode("ascii"),
    ).to_bytes()
    for index, entry in enumerate(entries, start=1)
  )
  return b"".join(frames)


def experiment_summary_response(*entries: str) -> bytes:
  frames = [
    CieloFrame(
      "12345678", EXPERIMENT_DATA_SUMMARY_GET_COMMAND, struct.pack("<II", 0, len(entries))
    ).to_bytes()
  ]
  frames.extend(
    CieloFrame(
      "12345678",
      EXPERIMENT_DATA_SUMMARY_GET_COMMAND,
      struct.pack("<I", index) + entry.encode("ascii"),
    ).to_bytes()
    for index, entry in enumerate(entries, start=1)
  )
  return b"".join(frames)


def status_payload(
  work_status: int = WORK_STATUS_IDLE,
  is_finished: int = 0,
  *,
  current_step: int = 4,
  current_cycle: int = 5,
  current_time_remaining: int = 60,
  program_time_total: int = 3600,
  program_time_remaining: int = 3540,
) -> bytes:
  return struct.pack(
    "<16s2I2IHHHhHHIIIhhHH16h16h",
    b"RUN\0".ljust(16, b"\0"),
    11,
    12,
    21,
    22,
    1,
    work_status,
    3,
    10450,
    current_step,
    current_cycle,
    current_time_remaining,
    program_time_total,
    program_time_remaining,
    2250,
    3100,
    20,
    is_finished,
    *range(4000, 4016),
    *range(5000, 5016),
  )


def running_data_payload(
  index: int = 1, step: int = 3, position: int = 4, channel: int = 0, cycle: int = 1
) -> bytes:
  return (
    struct.pack("<i", index)
    + bytes([RUNNING_DATA_TYPE_NORMAL])
    + struct.pack("<HHHH", step, position, channel, cycle)
    + struct.pack("<16f", *range(16))
  )


def result_file_bytes() -> bytes:
  """Build a synthetic .AZE envelope with the verified firmware layout."""
  magic = b"Azure Data\x00"
  datainfo = {
    "Device id:": "QI6-0000",
    "Device name:": "AZURE CIELO 6",
    "Instrument software Version:": "1.4.4.0",
    "Instrument control module software Version:": "1.1.3.7",
    "Instrument heater module software Version:": "1.2.4.1",
    "Program:": "PLR-Short-2C",
    "Workspace:": "Public",
    "Run start time:": "2026-08-29_13:08:36",
    "Run end time:": "2026-08-29_13:12:35",
    "Gain:": 10,
    **{f"Channel {channel}expose time": 50 for channel in range(1, 7)},
    "Channel1": ["default", "FAM", "SYBR"],
    "Channel2": ["default", "HEX", "VIC"],
    "Channel3": ["default", "TAMRA"],
    "Channel4": ["default", "ROX", "TEXAS  RED"],
    "Channel5": ["CY5", "default"],
    "Channel6": ["default", "Quasar  705"],
    "FAM": ["1.0", "0.0725", "0", "0", "0", "0"],
    "Block1Temp": [2803, 2804],
    "Block2Temp": [2806, 2807],
    "Block3Temp": [2802, 2803],
    "Sample1Temp": [2803, 2804],
    "Sample2Temp": [2806, 2807],
    "Sample3Temp": [2802, 2803],
    "HotlidTemp": [3130, 3160],
  }
  datainfo_bytes = json.dumps(datainfo).encode("utf-8")

  def channel_values(channel: int, spike: float) -> tuple[float, ...]:
    values = [float(channel + position / 100) for position in range(96)]
    values[0] = spike  # A1
    values[8] = spike / 2  # A2
    values[95] = spike / 3  # H12
    return tuple(values)

  def record(step: int, cycle: int, spike: float) -> bytes:
    return struct.pack("<HH", step, cycle) + b"".join(
      struct.pack("<96f", *channel_values(channel, spike)) for channel in range(6)
    )

  experiment_data = record(3, 1, 7.5) + record(3, 2, 7.1)
  return (
    len(magic).to_bytes(4, byteorder="big")
    + magic
    + len(program_bytes()).to_bytes(4, byteorder="big")
    + program_bytes()
    + len(datainfo_bytes).to_bytes(4, byteorder="big")
    + datainfo_bytes
    + b"\xff\xff\xff\xff"
    + len(experiment_data).to_bytes(4, byteorder="big")
    + experiment_data
    + len(experiment_data).to_bytes(4, byteorder="big")
    + experiment_data
  )


def replace_result_metadata(data: bytes, metadata: object) -> bytes:
  magic_end = 4 + int.from_bytes(data[:4], "big")
  program_end = magic_end + 4 + int.from_bytes(data[magic_end : magic_end + 4], "big")
  metadata_length = int.from_bytes(data[program_end : program_end + 4], "big")
  metadata_start = program_end + 4
  encoded = json.dumps(metadata).encode("utf-8")
  return (
    data[:program_end]
    + len(encoded).to_bytes(4, "big")
    + encoded
    + data[metadata_start + metadata_length :]
  )


def result_metadata(data: bytes) -> dict[str, object]:
  magic_end = 4 + int.from_bytes(data[:4], "big")
  program_end = magic_end + 4 + int.from_bytes(data[magic_end : magic_end + 4], "big")
  metadata_length = int.from_bytes(data[program_end : program_end + 4], "big")
  metadata_start = program_end + 4
  return cast(
    dict[str, object], json.loads(data[metadata_start : metadata_start + metadata_length])
  )


def replace_result_melting_data(data: bytes, records: bytes) -> bytes:
  """Replace the binary melting section in a synthetic result file."""
  magic_end = 4 + int.from_bytes(data[:4], "big")
  program_end = magic_end + 4 + int.from_bytes(data[magic_end : magic_end + 4], "big")
  metadata_length = int.from_bytes(data[program_end : program_end + 4], "big")
  melting_length_offset = program_end + 4 + metadata_length
  return (
    data[:melting_length_offset]
    + len(records).to_bytes(4, "big")
    + records
    + data[melting_length_offset + 4 :]
  )


class CieloFrameTests(unittest.TestCase):
  def test_status_query_matches_vendor_crc_and_tail(self) -> None:
    frame = CieloFrame(device_id="99999999", command=STATUS_QUERY_COMMAND)
    self.assertEqual(
      frame.to_bytes(),
      bytes.fromhex("39 39 39 39 39 39 39 39 02 0b 00 00 00 63 28 55 aa"),
    )

  def test_round_trip_with_payload(self) -> None:
    frame = CieloFrame(device_id="12345678", command=0x0B02, payload=b"abc")
    self.assertEqual(CieloFrame.from_bytes(frame.to_bytes()), frame)

  def test_rejects_bad_crc(self) -> None:
    encoded = bytearray(CieloFrame("12345678", 0x0B02).to_bytes())
    encoded[-4] ^= 1
    with self.assertRaisesRegex(Cielo6Error, "CRC"):
      CieloFrame.from_bytes(bytes(encoded))

  def test_rejects_bad_tail(self) -> None:
    encoded = bytearray(CieloFrame("12345678", 0x0B02).to_bytes())
    encoded[-1] = 0
    with self.assertRaisesRegex(Cielo6Error, "tail"):
      CieloFrame.from_bytes(bytes(encoded))

  def test_rejects_invalid_device_id(self) -> None:
    with self.assertRaisesRegex(ValueError, "8 ASCII"):
      CieloFrame("short", 0x0B02)

  def test_rejects_oversized_payload(self) -> None:
    with self.assertRaisesRegex(ValueError, "255"):
      CieloFrame("12345678", 0x0B02, bytes(256))


class CieloStatusTests(unittest.TestCase):
  def test_decodes_complete_128_byte_status(self) -> None:
    payload = struct.pack(
      "<16s2I2IHHHhHHIIIhhHH16h16h",
      b"RUN-001\0".ljust(16, b"\0"),
      11,
      12,
      21,
      22,
      1,
      2,
      3,
      10450,
      4,
      5,
      60,
      3600,
      3540,
      2250,
      3100,
      25,
      0,
      *range(4000, 4016),
      *range(5000, 5016),
    )

    status = Cielo6Status.from_payload(payload)

    self.assertEqual(status.file_name, "RUN-001")
    self.assertEqual(status.run_id, (11, 12))
    self.assertEqual(status.sample_id, (21, 22))
    self.assertEqual(status.hot_lid_temperature_raw, 10450)
    self.assertEqual(status.block_temperatures_raw, tuple(range(4000, 4016)))
    self.assertEqual(status.sample_temperatures, tuple(value / 100 for value in range(5000, 5016)))
    self.assertEqual(status.hot_lid_temperature, 104.5)
    self.assertEqual(status.environment_temperature, 22.5)
    self.assertEqual(status.radiator_temperature, 31.0)
    self.assertEqual(status.block_temperatures, tuple(value / 100 for value in range(4000, 4016)))
    self.assertEqual(status.work_state, Cielo6WorkState.UNKNOWN)
    self.assertEqual(status.progress, 1 / 60)

  def test_exposes_typed_run_state_without_hiding_unknown_firmware_values(self) -> None:
    running = Cielo6Status.from_payload(status_payload(WORK_STATUS_RUNNING))
    unknown = Cielo6Status.from_payload(status_payload(9999))

    self.assertEqual(running.work_state, Cielo6WorkState.RUNNING)
    self.assertTrue(running.is_running)
    self.assertFalse(running.is_paused)
    self.assertEqual(unknown.work_state, Cielo6WorkState.UNKNOWN)
    self.assertTrue(Cielo6WorkState.RUN_ERROR.is_error)
    self.assertFalse(Cielo6WorkState.RUNNING.is_error)

  def test_progress_is_absent_without_firmware_total_and_clamped(self) -> None:
    absent = Cielo6Status.from_payload(
      status_payload(program_time_total=0, program_time_remaining=0)
    )
    overrun = Cielo6Status.from_payload(
      status_payload(program_time_total=100, program_time_remaining=150)
    )

    self.assertIsNone(absent.progress)
    self.assertEqual(overrun.progress, 0.0)

  def test_rejects_wrong_payload_size(self) -> None:
    with self.assertRaisesRegex(Cielo6Error, "expected 128"):
      Cielo6Status.from_payload(bytes(127))


class CieloIdentityTests(unittest.TestCase):
  def test_decodes_captured_response_shape(self) -> None:
    identity = Cielo6Identity.from_bytes(identity_response())
    self.assertEqual(identity.device_id, "12345678")
    self.assertEqual(identity.name, "AZURE CIELO 6")
    self.assertEqual(identity.transport, "USB")

  def test_rejects_wrong_transport(self) -> None:
    with self.assertRaisesRegex(Cielo6Error, "transport"):
      Cielo6Identity.from_bytes(identity_response()[:-4] + b"&TCP")


class CieloProgramTests(unittest.TestCase):
  def test_decodes_complete_program_and_crc(self) -> None:
    encoded = program_bytes()
    program = Cielo6StoredProgram.from_bytes(encoded)
    self.assertEqual(program.identifier, b"P123")
    self.assertEqual(program.name, "Test")
    self.assertEqual(program.workspace, "Public")
    self.assertEqual(program.step_count, 2)
    self.assertEqual(len(program.steps), 2)
    self.assertEqual(program.steps[0].name, 0x5AF1)
    self.assertEqual(program.steps[0].temperatures_raw, tuple(range(6000, 6016)))
    self.assertEqual(program.to_bytes(), encoded)

  def test_rejects_bad_program_crc(self) -> None:
    data = bytearray(program_bytes())
    data[100] ^= 1
    with self.assertRaisesRegex(Cielo6Error, "CRC32"):
      Cielo6StoredProgram.from_bytes(bytes(data))

  def test_preserves_inactive_step_bytes(self) -> None:
    data = bytearray(program_bytes())
    data[128] = 0xA5
    data[-4:] = zlib.crc32(data[:-4]).to_bytes(4, byteorder="little")
    program = Cielo6StoredProgram.from_bytes(bytes(data))
    self.assertEqual(program.to_bytes(), bytes(data))

  def test_crc32_is_derived_from_current_program_content(self) -> None:
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    renamed = replace(program, name="Renamed")
    self.assertNotEqual(renamed.crc32, program.crc32)
    self.assertEqual(renamed.crc32, zlib.crc32(renamed.to_bytes()[:-4]))

  def test_compiles_short_qpcr_protocol_from_hardware_template(self) -> None:
    template = Cielo6StoredProgram.from_bytes(program_bytes())
    protocol = Cielo6ThermalProtocol(
      steps=(
        Cielo6ThermalStep(95, 30),
        Cielo6ThermalStep(95, 5),
        Cielo6ThermalStep(60, 15, collect_fluorescence=True),
      ),
      repeat_from_step=1,
      cycles=2,
      sample_volume=20,
    )

    compiled = protocol.compile(template, workspace="Public", name="PLR-Short-2C")

    self.assertEqual(compiled.name, "PLR-Short-2C")
    self.assertEqual(compiled.workspace, "Public")
    self.assertEqual(compiled.channels, template.channels)
    self.assertEqual(compiled.positions, template.positions)
    self.assertEqual(compiled.hot_lid_temperature_raw, template.hot_lid_temperature_raw)
    self.assertEqual(compiled.melting_curve_mode, 0)
    self.assertEqual(compiled.sample_volume, 20)
    self.assertEqual(compiled.step_count, 4)
    self.assertEqual(compiled.steps[2].temperatures_raw[:3], (6000, 6000, 6000))
    self.assertEqual(compiled.steps[2].collection_mode, 1)
    self.assertEqual(compiled.steps[3].function, 4)
    self.assertEqual(compiled.steps[3].to_step, 2)
    self.assertEqual(compiled.steps[3].goto_times, 1)
    self.assertEqual(compiled.thermal_step_count, 3)
    self.assertEqual(compiled.cycle_count, 2)
    self.assertEqual(compiled.step_target_temperatures(2)[:3], (60.0,) * 3)
    self.assertEqual(compiled.step_target_temperatures(2)[3:], (0.0,) * 13)
    self.assertEqual(
      Cielo6StoredProgram.from_bytes(compiled.to_bytes()).to_bytes(), compiled.to_bytes()
    )

  def test_rejects_cycles_without_repeat_group(self) -> None:
    with self.assertRaisesRegex(ValueError, "repeat_from_step"):
      Cielo6ThermalProtocol(steps=(Cielo6ThermalStep(60, 10),), cycles=2)


class CieloRunningDataTests(unittest.TestCase):
  def test_decodes_normal_payload(self) -> None:
    payload = (
      struct.pack("<i", 7)
      + bytes([RUNNING_DATA_TYPE_NORMAL])
      + struct.pack("<HHHH", 3, 10, 2, 1)
      + struct.pack("<16f", *range(16))
    )

    data = Cielo6RunningData.from_payload(payload)

    self.assertEqual(data.index, 7)
    self.assertEqual(data.step_number, 3)
    self.assertEqual(data.position, 10)
    self.assertEqual(data.channel, 2)
    self.assertEqual(data.cycle, 1)
    self.assertEqual(data.values, tuple(range(16)))

  def test_decodes_melting_payload(self) -> None:
    payload = (
      struct.pack("<i", 9)
      + bytes([2])
      + struct.pack("<H", 7)
      + struct.pack("<i", 6500)
      + struct.pack("<H", 2)
      + struct.pack("<16f", *range(16))
    )

    data = Cielo6MeltingData.from_payload(payload)

    self.assertEqual(data.index, 9)
    self.assertEqual(data.position, 7)
    self.assertEqual(data.temperature, 6500)
    self.assertEqual(data.cycle, 2)

  def test_rejects_wrong_data_type(self) -> None:
    payload = bytearray(running_data_payload())
    payload[4] = 3
    with self.assertRaisesRegex(Cielo6Error, "data type"):
      Cielo6RunningData.from_payload(bytes(payload))

  def test_rejects_truncated_payload(self) -> None:
    with self.assertRaisesRegex(Cielo6Error, "expected 77"):
      Cielo6RunningData.from_payload(running_data_payload()[:-1])


class CieloResultFileTests(unittest.TestCase):
  def test_parses_verified_layout_and_well_order(self) -> None:
    result = Cielo6ResultFile.from_bytes(result_file_bytes())

    self.assertEqual(result.device_id, "QI6-0000")
    self.assertEqual(result.workspace, "Public")
    self.assertEqual(result.program, "PLR-Short-2C")
    self.assertEqual(result.stored_program.name, "Test")
    self.assertEqual(result.stored_program.workspace, "Public")
    self.assertEqual(result.gain, 10)
    self.assertEqual(result.exposure_times, (50,) * 6)
    self.assertEqual(result.dyes[0], ("default", "FAM", "SYBR"))
    self.assertEqual(result.temperature_curves["Block1Temp"], (2803, 2804))
    self.assertEqual(len(result.collection_points), 2)
    self.assertEqual(len(result.processed_collection_points), 2)
    self.assertEqual(result.collection_points[0].step, 3)
    self.assertEqual(result.collection_points[0].cycle, 1)
    self.assertEqual(result.collection_points[1].cycle, 2)

    point = result.collection_points[0]
    self.assertEqual(point.well_value(0, 1, 0), 7.5)  # A1
    self.assertEqual(point.well_value(0, 2, 0), 3.75)  # A2
    self.assertEqual(point.well_value(7, 12, 0), 2.5)  # H12
    self.assertEqual(point.channels[0][0], 7.5)
    self.assertEqual(point.channels[0][8], 3.75)
    self.assertEqual(point.channels[0][95], 2.5)

  def test_collection_point_validates_shape(self) -> None:
    with self.assertRaisesRegex(ValueError, "6 channels"):
      Cielo6CollectionPoint(step=3, cycle=1, channels=((0.0,) * 96,))
    with self.assertRaisesRegex(ValueError, "96"):
      Cielo6CollectionPoint(step=3, cycle=1, channels=((0.0,) * 5,) * 6)
    with self.assertRaisesRegex(ValueError, "row"):
      Cielo6CollectionPoint(step=3, cycle=1, channels=((0.0,) * 96,) * 6).well_value(8, 1, 0)
    with self.assertRaisesRegex(ValueError, "column"):
      Cielo6CollectionPoint(step=3, cycle=1, channels=((0.0,) * 96,) * 6).well_value(0, 13, 0)

  def test_rejects_bad_magic(self) -> None:
    data = bytearray(result_file_bytes())
    data[5] = 0x58
    with self.assertRaisesRegex(Cielo6Error, "magic"):
      Cielo6ResultFile.from_bytes(bytes(data))

  def test_rejects_invalid_experiment_length(self) -> None:
    base = result_file_bytes()
    length_offset = len(base) - 4 * EXPERIMENT_DATA_SIZE - 8
    cases = ((-2, "length"), (EXPERIMENT_DATA_SIZE + 1, "multiple"))
    for value, message in cases:
      with self.subTest(value=value):
        data = bytearray(base)
        data[length_offset : length_offset + 4] = value.to_bytes(4, "big", signed=True)
        with self.assertRaisesRegex(Cielo6Error, message):
          Cielo6ResultFile.from_bytes(bytes(data))

  def test_accepts_empty_amplification_sections(self) -> None:
    base = result_file_bytes()
    length_offset = len(base) - 4 * EXPERIMENT_DATA_SIZE - 8

    for empty_length in (-1, 0):
      with self.subTest(empty_length=empty_length):
        encoded_length = empty_length.to_bytes(4, "big", signed=True)
        data = base[:length_offset] + encoded_length + encoded_length

        result = Cielo6ResultFile.from_bytes(data)

        self.assertEqual(result.collection_points, ())
        self.assertEqual(result.processed_collection_points, ())
        self.assertEqual(result.to_amplification_results(), [])
        self.assertEqual(result.to_processed_amplification_results(), [])

  def test_rejects_truncated_section_with_domain_error(self) -> None:
    with self.assertRaisesRegex(Cielo6Error, "Incomplete.*amplification data"):
      Cielo6ResultFile.from_bytes(result_file_bytes()[:-1])

  def test_rejects_non_object_metadata(self) -> None:
    with self.assertRaisesRegex(Cielo6Error, "expected an object"):
      Cielo6ResultFile.from_bytes(replace_result_metadata(result_file_bytes(), []))

  def test_rejects_invalid_json_metadata(self) -> None:
    data = bytearray(replace_result_metadata(result_file_bytes(), {}))
    magic_end = 4 + int.from_bytes(data[:4], "big")
    program_end = magic_end + 4 + int.from_bytes(data[magic_end : magic_end + 4], "big")
    data[program_end + 4] = 0xFF

    with self.assertRaisesRegex(Cielo6Error, "JSON metadata"):
      Cielo6ResultFile.from_bytes(bytes(data))

  def test_rejects_invalid_optional_metadata_values(self) -> None:
    cases = (
      ({"FAM": ["invalid"]}, "'FAM' value"),
      ({"FAM": "not-a-list"}, "expected an array"),
      ({"Block1Temp": ["invalid"]}, "'Block1Temp' value"),
      ({"Channel1": "FAM"}, "expected a text array"),
      ({"Gain:": "invalid"}, "gain or exposure"),
    )
    data = result_file_bytes()
    raw_metadata = result_metadata(data)

    for update, message in cases:
      with self.subTest(update=update):
        metadata = {**raw_metadata, **update}
        with self.assertRaisesRegex(Cielo6Error, message):
          Cielo6ResultFile.from_bytes(replace_result_metadata(data, metadata))

  def test_uses_empty_values_for_missing_optional_metadata(self) -> None:
    data = result_file_bytes()
    metadata = result_metadata(data)
    del metadata["FAM"]
    del metadata["Block1Temp"]

    result = Cielo6ResultFile.from_bytes(replace_result_metadata(data, metadata))

    self.assertNotIn("FAM", result.dye_crosstalk_coefficients)
    self.assertEqual(result.temperature_curves["Block1Temp"], ())

  def test_rejects_missing_required_metadata(self) -> None:
    data = result_file_bytes()
    metadata = result_metadata(data)
    del metadata["Device id:"]

    with self.assertRaisesRegex(Cielo6Error, "missing device_id"):
      Cielo6ResultFile.from_bytes(replace_result_metadata(data, metadata))

  def test_rejects_invalid_melting_and_processed_lengths(self) -> None:
    base = result_file_bytes()
    magic_end = 4 + int.from_bytes(base[:4], "big")
    program_end = magic_end + 4 + int.from_bytes(base[magic_end : magic_end + 4], "big")
    metadata_length = int.from_bytes(base[program_end : program_end + 4], "big")
    melting_length_offset = program_end + 4 + metadata_length
    experiment_length_offset = melting_length_offset + 4
    experiment_length = int.from_bytes(
      base[experiment_length_offset : experiment_length_offset + 4], "big"
    )
    processed_length_offset = experiment_length_offset + 4 + experiment_length

    cases = (
      (melting_length_offset, -2, "melting data length"),
      (melting_length_offset, 1, "melting data length"),
      (processed_length_offset, -2, "processed amplification data length"),
      (processed_length_offset, 1, "processed amplification data length"),
    )
    for offset, value, message in cases:
      with self.subTest(value=value, message=message):
        data = bytearray(base)
        data[offset : offset + 4] = value.to_bytes(4, "big", signed=True)
        with self.assertRaisesRegex(Cielo6Error, message):
          Cielo6ResultFile.from_bytes(bytes(data))

  def test_rejects_trailing_bytes(self) -> None:
    with self.assertRaisesRegex(Cielo6Error, "trailing byte"):
      Cielo6ResultFile.from_bytes(result_file_bytes() + b"unexpected")

  def test_keeps_processed_amplification_separate_from_raw_data(self) -> None:
    data = bytearray(result_file_bytes())
    processed_data_start = len(data) - 2 * EXPERIMENT_DATA_SIZE
    struct.pack_into("<f", data, processed_data_start + 4, 42.0)

    result = Cielo6ResultFile.from_bytes(bytes(data))

    self.assertEqual(result.collection_points[0].channels[0][0], 7.5)
    self.assertEqual(result.processed_collection_points[0].channels[0][0], 42.0)
    self.assertEqual(result.to_processed_amplification_results()[0].data[0][0], 42.0)

  def test_parses_melting_records(self) -> None:
    base = result_file_bytes()
    records = (
      struct.pack("<i", 6000)
      + struct.pack("<96f", *range(96))
      + struct.pack("<i", 9500)
      + struct.pack("<96f", *reversed(range(96)))
    )
    result = Cielo6ResultFile.from_bytes(replace_result_melting_data(base, records))

    self.assertEqual(len(result.melt_records), 2)
    self.assertEqual(result.melt_records[0].channel_index, 0)
    self.assertEqual(result.melt_records[0].temperature, 60.0)
    self.assertEqual(result.melt_records[0].values[:2], (0.0, 1.0))
    self.assertEqual(result.melt_records[1].temperature, 95.0)
    self.assertEqual(result.melt_records[1].values[:2], (95.0, 94.0))

  def test_parses_additional_melting_channels_from_metadata(self) -> None:
    records = (
      struct.pack("<i", 6000)
      + struct.pack("<96f", *range(96))
      + struct.pack("<i", 9500)
      + struct.pack("<96f", *reversed(range(96)))
    )
    base = replace_result_melting_data(result_file_bytes(), records)
    metadata = result_metadata(base)
    metadata["MeltCurveChannel2"] = list(range(192))
    metadata["MeltCurveChannel6"] = ["2.5"] * 192

    result = Cielo6ResultFile.from_bytes(replace_result_metadata(base, metadata))

    self.assertEqual(len(result.melt_records), 6)
    self.assertEqual(result.melt_records[2].channel_index, 1)
    self.assertEqual(result.melt_records[2].temperature, 60.0)
    self.assertEqual(result.melt_records[2].values[:2], (0.0, 1.0))
    self.assertEqual(result.melt_records[3].temperature, 95.0)
    self.assertEqual(result.melt_records[3].values[:2], (96.0, 97.0))
    self.assertEqual(result.melt_records[4].channel_index, 5)
    self.assertEqual(result.melt_records[4].temperature, 60.0)
    self.assertEqual(result.melt_records[4].values[0], 2.5)
    self.assertEqual(
      [point.channel_index for point in result.to_melting_curve_results()],
      [0, 0, 1, 1, 5, 5],
    )

  def test_rejects_invalid_additional_melting_channel_metadata(self) -> None:
    cases = (
      ("invalid", "expected an array"),
      ([1.0] * 95, "value count"),
      (["invalid"] * 96, "value"),
    )
    records = struct.pack("<i", 6000) + struct.pack("<96f", *range(96))
    base = replace_result_melting_data(result_file_bytes(), records)
    raw_metadata = result_metadata(base)
    for value, message in cases:
      with self.subTest(value=value):
        metadata = {**raw_metadata, "MeltCurveChannel2": value}
        with self.assertRaisesRegex(Cielo6Error, message):
          Cielo6ResultFile.from_bytes(replace_result_metadata(base, metadata))

  def test_melt_record_validates_channel(self) -> None:
    with self.assertRaisesRegex(ValueError, "channel_index"):
      Cielo6MeltRecord(temperature_raw=6000, values=(1.0,) * 96, channel_index=6)

  def test_to_amplification_csv_matches_oem_export_shape(self) -> None:
    result = Cielo6ResultFile.from_bytes(result_file_bytes())

    csv = result.to_amplification_csv()
    self.assertTrue(csv.startswith("\ufeff"))
    lines = csv.splitlines()
    lines[0] = lines[0].lstrip("\ufeff")

    self.assertTrue(lines[0].startswith("Data,A1,B1,C1,D1,E1,F1,G1,H1,A2,"))
    self.assertTrue(lines[0].endswith("H12,"))
    # The template enables channels 0 and 2. Step 3 has two collection points.
    self.assertEqual(lines[1], "Step3Channel1")
    self.assertTrue(lines[2].startswith("1,7.5,0.01,0.02,0.03,0.04,0.05,0.06,0.07,3.75,"))
    self.assertTrue(lines[3].startswith("2,7.1,0.01,0.02,0.03,0.04,0.05,0.06,0.07,3.55,"))
    self.assertEqual(lines[4], "Step3Channel3")
    self.assertEqual(len(lines[5].split(",")), 98)

  def test_converts_amplification_records_to_plr_plate_data(self) -> None:
    result = Cielo6ResultFile.from_bytes(result_file_bytes())

    amplification_results = result.to_amplification_results()

    self.assertEqual(len(amplification_results), 4)
    first = amplification_results[0]
    self.assertEqual(first.cycle, 1)
    self.assertEqual(first.step, 3)
    self.assertEqual(first.channel_index, 0)
    self.assertEqual(len(first.data), 8)
    self.assertEqual(len(first.data[0]), 12)
    self.assertEqual(first.data[0][0], 7.5)  # A1
    self.assertEqual(first.data[0][1], 3.75)  # A2
    self.assertEqual(first.data[7][11], 2.5)  # H12
    self.assertEqual(amplification_results[1].channel_index, 2)
    self.assertEqual(len(result.to_processed_amplification_results()), 4)

  def test_converts_melting_records_to_plr_plate_data(self) -> None:
    result = replace(
      Cielo6ResultFile.from_bytes(result_file_bytes()),
      melt_records=(
        Cielo6MeltRecord(
          temperature_raw=6050,
          values=tuple(float(value) for value in range(96)),
          channel_index=2,
        ),
      ),
    )

    melting_results = result.to_melting_curve_results()

    self.assertEqual(len(melting_results), 1)
    self.assertEqual(melting_results[0].temperature, 60.5)
    self.assertEqual(melting_results[0].channel_index, 2)
    self.assertEqual(melting_results[0].data[0][0], 0.0)  # A1
    self.assertEqual(melting_results[0].data[0][1], 8.0)  # A2
    self.assertEqual(melting_results[0].data[7][11], 95.0)  # H12

  def test_to_melting_csv_matches_oem_export_shape(self) -> None:
    result = Cielo6ResultFile.from_bytes(result_file_bytes())
    with_melt = replace(
      result,
      melt_records=(Cielo6MeltRecord(temperature_raw=6000, values=(1.0,) * 96, channel_index=0),),
    )

    csv = with_melt.to_melting_csv()
    lines = csv.splitlines()
    lines[0] = lines[0].lstrip("\ufeff")

    self.assertTrue(lines[0].startswith("Temperature,A1,B1,C1,D1,E1,F1,G1,H1,A2,"))
    self.assertTrue(lines[0].endswith("H12,"))
    self.assertEqual(lines[1], "60," + ",".join("1" for _ in range(96)) + ",")


class Cielo6LifecycleTests(unittest.IsolatedAsyncioTestCase):
  async def test_setup_and_stop_delegate_to_serial_transport(self) -> None:
    device = make_device(identity_response(), is_setup=False)
    await device.setup()
    await device.stop()
    cast(AsyncMock, device.io.setup).assert_awaited_once()
    cast(AsyncMock, device.io.stop).assert_awaited_once()
    cast(AsyncMock, device.io.write).assert_awaited_once_with(
      CieloFrame(DISCOVERY_DEVICE_ID, VERSION_CHECK_COMMAND).to_bytes()
    )

  async def test_setup_and_stop_are_idempotent(self) -> None:
    device = make_device(identity_response(), is_setup=False)

    await device.setup()
    await device.setup()
    await device.stop()
    await device.stop()

    cast(AsyncMock, device.io.setup).assert_awaited_once()
    cast(AsyncMock, device.io.stop).assert_awaited_once()

  async def test_commands_require_setup(self) -> None:
    device = make_device(is_setup=False)

    with self.assertRaisesRegex(RuntimeError, "Call setup"):
      await device.request_status()

    cast(AsyncMock, device.io.write).assert_not_awaited()

  async def test_setup_rejects_another_instrument(self) -> None:
    device = make_device(identity_response(device_id="87654321"), is_setup=False)
    with self.assertRaisesRegex(Cielo6Error, "does not match"):
      await device.setup()
    cast(AsyncMock, device.io.stop).assert_awaited_once()

  async def test_setup_failure_is_not_masked_by_transport_cleanup_failure(self) -> None:
    device = make_device(identity_response(device_id="87654321"), is_setup=False)
    cast(AsyncMock, device.io.stop).side_effect = RuntimeError("close failed")

    with self.assertRaisesRegex(Cielo6Error, "does not match"):
      await device.setup()

    self.assertFalse(device._is_setup)

  async def test_setup_clears_connection_state_and_keeps_active_program(self) -> None:
    device = make_device(identity_response(), is_setup=False)
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    device._receive_buffer.extend(b"partial frame")
    device._running_data.append(Cielo6RunningData.from_payload(running_data_payload()))
    device._melting_data.append(
      Cielo6MeltingData.from_payload(struct.pack("<iBHiH16f", 9, 2, 7, 6500, 2, *range(16)))
    )
    device.identity = Cielo6Identity("12345678", "old", "USB")
    device.latest_status = Cielo6Status.from_payload(status_payload())
    device._active_program = program

    await device.setup()

    self.assertEqual(device._receive_buffer, bytearray())
    self.assertEqual(device.running_data, ())
    self.assertEqual(device.melting_data, ())
    self.assertIsNone(device.latest_status)
    self.assertEqual(device.identity, Cielo6Identity("12345678", "AZURE CIELO 6", "USB"))
    self.assertIs(device._active_program, program)

  async def test_setup_clears_connection_state_before_transport_failure(self) -> None:
    device = make_device(is_setup=False)
    device._receive_buffer.extend(b"partial frame")
    device._running_data.append(Cielo6RunningData.from_payload(running_data_payload()))
    device.latest_status = Cielo6Status.from_payload(status_payload())
    cast(AsyncMock, device.io.setup).side_effect = RuntimeError("port unavailable")

    with self.assertRaisesRegex(RuntimeError, "port unavailable"):
      await device.setup()

    self.assertEqual(device._receive_buffer, bytearray())
    self.assertEqual(device.running_data, ())
    self.assertIsNone(device.latest_status)
    cast(AsyncMock, device.io.stop).assert_awaited_once()


class Cielo6StatusQueryTests(unittest.IsolatedAsyncioTestCase):
  async def test_request_status_uses_only_read_only_status_command(self) -> None:
    payload = bytes(128)
    response = CieloFrame("12345678", STATUS_QUERY_COMMAND, payload).to_bytes()
    device = make_device(response)

    status = await device.request_status()

    cast(AsyncMock, device.io.write).assert_awaited_once_with(
      CieloFrame("12345678", STATUS_QUERY_COMMAND).to_bytes()
    )
    self.assertEqual(status.block_temperatures_raw, (0,) * 16)

  async def test_request_status_handles_fragmented_response(self) -> None:
    response = CieloFrame("12345678", STATUS_QUERY_COMMAND, bytes(128)).to_bytes()
    device = make_device()
    chunks = [response[:4], response[4:13], response[13:30], response[30:]]
    cast(AsyncMock, device.io.read).side_effect = chunks

    status = await device.request_status()

    self.assertEqual(status.file_name, "")

  async def test_request_status_recovers_from_unrelated_leading_byte(self) -> None:
    response = CieloFrame("12345678", STATUS_QUERY_COMMAND, bytes(128)).to_bytes()
    device = make_device(b"\xff" + response)

    status = await device.request_status()

    self.assertEqual(status.file_name, "")

  async def test_request_status_retains_decoded_unsolicited_running_data(self) -> None:
    running_data = running_data_payload()
    response = (
      CieloFrame("12345678", RUNNING_EXPERIMENT_DATA_UPLOAD_COMMAND, running_data).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, bytes(128)).to_bytes()
    )
    device = make_device(response)

    await device.request_status()

    self.assertEqual(len(device.running_data), 1)
    self.assertEqual(device.running_data[0].step_number, 3)
    self.assertEqual(device.running_data[0].values, tuple(range(16)))

  async def test_request_status_retains_decoded_unsolicited_melting_data(self) -> None:
    melting_data = (
      struct.pack("<i", 9)
      + bytes([2])
      + struct.pack("<H", 7)
      + struct.pack("<i", 6500)
      + struct.pack("<H", 2)
      + struct.pack("<16f", *range(16))
    )
    response = (
      CieloFrame("12345678", RUNNING_EXPERIMENT_DATA_UPLOAD_COMMAND, melting_data).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, bytes(128)).to_bytes()
    )
    device = make_device(response)

    await device.request_status()

    self.assertEqual(device.melting_data, (Cielo6MeltingData.from_payload(melting_data),))

  async def test_request_status_rejects_invalid_unsolicited_running_data(self) -> None:
    cases = ((b"1234", "too short"), (b"1234\x03", "Unsupported"))
    for payload, message in cases:
      with self.subTest(payload=payload):
        response = CieloFrame(
          "12345678", RUNNING_EXPERIMENT_DATA_UPLOAD_COMMAND, payload
        ).to_bytes()
        device = make_device(response)
        with self.assertRaisesRegex(Cielo6Error, message):
          await device.request_status()

  async def test_request_status_ignores_delayed_run_response(self) -> None:
    response = (
      CieloFrame("12345678", RUN_COMMAND).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)).to_bytes()
    )
    device = make_device(response)

    status = await device.request_status()

    self.assertEqual(status.work_state, Cielo6WorkState.RUNNING)

  async def test_request_status_rejects_other_device(self) -> None:
    response = CieloFrame("87654321", STATUS_QUERY_COMMAND, bytes(128)).to_bytes()
    device = make_device(response)
    with self.assertRaisesRegex(Cielo6Error, "device ID"):
      await device.request_status()

  async def test_request_status_rejects_wrong_command(self) -> None:
    response = CieloFrame("12345678", 0x0B01, bytes(128)).to_bytes()
    device = make_device(response)
    with self.assertRaisesRegex(Cielo6Error, "Unexpected"):
      await device.request_status()

  async def test_request_status_rejects_truncated_response(self) -> None:
    device = make_device(b"1234")
    with self.assertRaisesRegex(Cielo6Error, "Incomplete"):
      await device.request_status()


class Cielo6StorageQueryTests(unittest.IsolatedAsyncioTestCase):
  async def test_request_workspace_summary_reads_indexed_entries(self) -> None:
    responses = b"".join(
      (
        CieloFrame("12345678", WORKSPACE_SUMMARY_GET_COMMAND, struct.pack("<II", 0, 3)).to_bytes(),
        CieloFrame(
          "12345678", WORKSPACE_SUMMARY_GET_COMMAND, struct.pack("<I", 1) + b"Research^PCR"
        ).to_bytes(),
        CieloFrame(
          "12345678", WORKSPACE_SUMMARY_GET_COMMAND, struct.pack("<I", 2) + b"Research^Melt"
        ).to_bytes(),
        CieloFrame(
          "12345678", WORKSPACE_SUMMARY_GET_COMMAND, struct.pack("<I", 3) + b"QC^Check"
        ).to_bytes(),
      )
    )
    device = make_device(responses)

    summary = await device.request_workspace_summary()

    self.assertEqual(summary, {"Research": ["PCR", "Melt"], "QC": ["Check"]})
    expected_payloads = [struct.pack("<I", index) for index in range(4)]
    self.assertEqual(
      [call.args[0] for call in cast(AsyncMock, device.io.write).call_args_list],
      [
        CieloFrame("12345678", WORKSPACE_SUMMARY_GET_COMMAND, payload).to_bytes()
        for payload in expected_payloads
      ],
    )

  async def test_request_workspace_summary_rejects_wrong_entry_index(self) -> None:
    responses = b"".join(
      (
        CieloFrame("12345678", WORKSPACE_SUMMARY_GET_COMMAND, struct.pack("<II", 0, 1)).to_bytes(),
        CieloFrame(
          "12345678", WORKSPACE_SUMMARY_GET_COMMAND, struct.pack("<I", 2) + b"Research^PCR"
        ).to_bytes(),
      )
    )
    device = make_device(responses)
    with self.assertRaisesRegex(Cielo6Error, "expected 1"):
      await device.request_workspace_summary()

  async def test_request_program_reassembles_indexed_chunks(self) -> None:
    data = program_bytes()
    indices = list(reversed(range(16)))
    responses = b"".join(
      CieloFrame(
        "12345678",
        PROGRAM_GET_COMMAND,
        bytes([index]) + data[index * PROGRAM_CHUNK_SIZE : (index + 1) * PROGRAM_CHUNK_SIZE],
      ).to_bytes()
      for index in indices
    )
    device = make_device(responses)

    program = await device.request_program("Public", "Test")

    self.assertEqual(program.name, "Test")
    cast(AsyncMock, device.io.write).assert_awaited_once_with(
      CieloFrame("12345678", PROGRAM_GET_COMMAND, b"Public^Test").to_bytes()
    )

  async def test_request_program_retains_unsolicited_status_before_response(self) -> None:
    data = program_bytes()
    responses = CieloFrame("12345678", STATUS_QUERY_COMMAND, bytes(128)).to_bytes() + b"".join(
      CieloFrame(
        "12345678",
        PROGRAM_GET_COMMAND,
        bytes([index]) + data[index * PROGRAM_CHUNK_SIZE : (index + 1) * PROGRAM_CHUNK_SIZE],
      ).to_bytes()
      for index in range(16)
    )
    device = make_device(responses)

    program = await device.request_program("Public", "Test")

    self.assertEqual(program.name, "Test")
    self.assertIsNotNone(device.latest_status)

  async def test_request_program_rejects_duplicate_chunk(self) -> None:
    data = program_bytes()
    responses = b"".join(
      CieloFrame(
        "12345678",
        PROGRAM_GET_COMMAND,
        bytes([0]) + data[:PROGRAM_CHUNK_SIZE],
      ).to_bytes()
      for _ in range(16)
    )
    device = make_device(responses)
    with self.assertRaisesRegex(Cielo6Error, "Duplicate"):
      await device.request_program("Public", "Test")

  async def test_request_experiment_summary_reads_indexed_entries(self) -> None:
    device = make_device(
      experiment_summary_response(
        "Research^PCR^Run-1^2026-08-29 10:00^2026-08-29 11:00",
        "QC^Melt^Check\0^2026-08-28 09:00^2026-08-28 09:30",
      )
    )

    results = await device.request_experiment_summary()

    self.assertEqual(
      results,
      (
        Cielo6ExperimentInfo(
          workspace="Research",
          protocol="PCR",
          name="Run-1",
          started_at_raw="2026-08-29 10:00",
          ended_at_raw="2026-08-29 11:00",
        ),
        Cielo6ExperimentInfo(
          workspace="QC",
          protocol="Melt",
          name="Check",
          started_at_raw="2026-08-28 09:00",
          ended_at_raw="2026-08-28 09:30",
        ),
      ),
    )

  async def test_request_experiment_summary_rejects_short_entry(self) -> None:
    device = make_device(experiment_summary_response("Research^PCR^Run-1"))
    with self.assertRaisesRegex(Cielo6Error, "expected 5 fields"):
      await device.request_experiment_summary()


class Cielo6RunStateQueryTests(unittest.IsolatedAsyncioTestCase):
  async def test_request_running_experiment_info(self) -> None:
    response = CieloFrame(
      "12345678", RUNNING_EXPERIMENT_INFOS_GET_COMMAND, b"Research^PCR^Run-1"
    ).to_bytes()
    device = make_device(response)

    result = await device.request_running_experiment_info()

    self.assertEqual(
      result, Cielo6ExperimentInfo(workspace="Research", protocol="PCR", name="Run-1")
    )

  async def test_request_running_experiment_info_returns_none_for_empty_payload(self) -> None:
    response = CieloFrame("12345678", RUNNING_EXPERIMENT_INFOS_GET_COMMAND).to_bytes()
    device = make_device(response)
    self.assertIsNone(await device.request_running_experiment_info())

  async def test_request_running_experiment_info_returns_none_for_stale_empty_name(self) -> None:
    response = CieloFrame(
      "12345678", RUNNING_EXPERIMENT_INFOS_GET_COMMAND, b"Public^Public^"
    ).to_bytes()
    device = make_device(response)
    self.assertIsNone(await device.request_running_experiment_info())

  async def test_request_run_state_normalizes_one_snapshot_with_program_context(self) -> None:
    template = Cielo6StoredProgram.from_bytes(program_bytes())
    program = Cielo6ThermalProtocol(
      steps=(
        Cielo6ThermalStep(95, 30),
        Cielo6ThermalStep(95, 5),
        Cielo6ThermalStep(60, 15, collect_fluorescence=True),
      ),
      repeat_from_step=1,
      cycles=2,
    ).compile(template, workspace="Public", name="PLR-Short-2C")
    responses = (
      CieloFrame(
        "12345678", RUNNING_EXPERIMENT_DATA_UPLOAD_COMMAND, running_data_payload()
      ).to_bytes()
      + CieloFrame(
        "12345678",
        STATUS_QUERY_COMMAND,
        status_payload(
          WORK_STATUS_RUNNING,
          current_step=3,
          current_cycle=2,
          program_time_total=200,
          program_time_remaining=50,
        ),
      ).to_bytes()
      + CieloFrame(
        "12345678", RUNNING_EXPERIMENT_INFOS_GET_COMMAND, b"Public^PLR-Short-2C^Run-1"
      ).to_bytes()
    )
    device = make_device(responses)

    state = await device.request_run_state(program)

    self.assertEqual(state.status.work_state, Cielo6WorkState.RUNNING)
    self.assertEqual(state.experiment, Cielo6ExperimentInfo("Public", "PLR-Short-2C", "Run-1"))
    self.assertEqual(state.current_step_index, 2)
    self.assertEqual(state.current_cycle_index, 1)
    self.assertEqual(state.total_step_count, 3)
    self.assertEqual(state.total_cycle_count, 2)
    assert state.target_temperatures is not None
    self.assertEqual(state.target_temperatures[:3], (60.0,) * 3)
    self.assertEqual(state.target_temperatures[3:], (0.0,) * 13)
    self.assertEqual(state.progress, 0.75)
    self.assertEqual(len(state.amplification_data), 1)
    self.assertEqual(state.estimated_completion_at, state.observed_at + timedelta(seconds=50))

  async def test_request_finished_run_state_avoids_identity_request(self) -> None:
    response = CieloFrame(
      "12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_IDLE, 1)
    ).to_bytes()
    device = make_device(response)

    state = await device.request_run_state()

    self.assertIsNone(state.experiment)
    self.assertIsNone(state.total_step_count)
    self.assertIsNone(state.total_cycle_count)
    self.assertIsNone(state.estimated_completion_at)
    cast(AsyncMock, device.io.write).assert_awaited_once()

  async def test_request_idle_run_state_avoids_identity_request_and_eta(self) -> None:
    response = CieloFrame(
      "12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_IDLE, 0)
    ).to_bytes()
    device = make_device(response)

    state = await device.request_run_state()

    self.assertIsNone(state.experiment)
    self.assertIsNone(state.estimated_completion_at)
    cast(AsyncMock, device.io.write).assert_awaited_once()

  async def test_completed_run_state_returns_then_clears_retained_program_context(self) -> None:
    response = CieloFrame(
      "12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_IDLE, 1)
    ).to_bytes()
    device = make_device(response)
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    device._active_program = program
    device._active_run_identity = ((11, 12), (21, 22), "RUN")
    device._run_phase = type(device._run_phase).DISPATCHED

    state = await device.request_run_state()

    self.assertEqual(state.total_step_count, program.thermal_step_count)
    self.assertIsNone(device._active_program)

  async def test_preparation_does_not_attach_program_to_stale_finished_status(self) -> None:
    response = CieloFrame(
      "12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_IDLE, 1)
    ).to_bytes()
    device = make_device(response)
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    device._active_program = program
    device._run_phase = type(device._run_phase).PREPARING

    state = await device.request_run_state()

    self.assertIsNone(state.total_step_count)
    self.assertIsNone(state.total_cycle_count)
    self.assertIs(device._active_program, program)
    self.assertIsNone(device._active_run_identity)

  async def test_new_active_run_does_not_reuse_stale_program_context(self) -> None:
    responses = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)).to_bytes()
      + CieloFrame(
        "12345678", RUNNING_EXPERIMENT_INFOS_GET_COMMAND, b"Public^Other^Run-2"
      ).to_bytes()
    )
    device = make_device(responses)
    device._active_program = Cielo6StoredProgram.from_bytes(program_bytes())
    device._active_run_identity = ((99, 100), (101, 102), "OLD")

    state = await device.request_run_state()

    self.assertIsNone(state.total_step_count)
    self.assertIsNone(state.target_temperatures)
    self.assertIsNone(device._active_program)


class Cielo6ResultTransferTests(unittest.IsolatedAsyncioTestCase):
  async def test_request_experiment_data_verifies_size_and_md5(self) -> None:
    experiment = Cielo6ExperimentInfo(workspace="Research", protocol="PCR", name="Run-1")
    data = bytes(range(255)) + b"tail"
    info = len(data).to_bytes(4, byteorder="little", signed=True) + hashlib.md5(data).digest()
    responses = (
      CieloFrame("12345678", EXPERIMENT_DATA_FILE_INFO_GET_COMMAND, info).to_bytes()
      + CieloFrame("12345678", EXPERIMENT_DATA_FILE_GET_COMMAND, data[:255]).to_bytes()
      + CieloFrame("12345678", EXPERIMENT_DATA_FILE_GET_COMMAND, data[255:]).to_bytes()
    )
    device = make_device(responses)

    result = await device.request_experiment_data(experiment)

    self.assertEqual(result, data)
    self.assertEqual(
      [frame.payload for frame in written_frames(device)],
      [b"Research/PCR/Run-1", b"Research/PCR/Run-1"],
    )

  async def test_request_experiment_data_rejects_bad_md5(self) -> None:
    experiment = Cielo6ExperimentInfo(workspace="Research", protocol="PCR", name="Run-1")
    data = b"result"
    info = len(data).to_bytes(4, byteorder="little", signed=True) + bytes(16)
    responses = (
      CieloFrame("12345678", EXPERIMENT_DATA_FILE_INFO_GET_COMMAND, info).to_bytes()
      + CieloFrame("12345678", EXPERIMENT_DATA_FILE_GET_COMMAND, data).to_bytes()
    )
    device = make_device(responses)

    with self.assertRaisesRegex(Cielo6Error, "MD5"):
      await device.request_experiment_data(experiment)

  async def test_request_experiment_data_rejects_oversized_stream(self) -> None:
    experiment = Cielo6ExperimentInfo(workspace="Research", protocol="PCR", name="Run-1")
    info = (3).to_bytes(4, byteorder="little", signed=True) + hashlib.md5(b"abc").digest()
    responses = (
      CieloFrame("12345678", EXPERIMENT_DATA_FILE_INFO_GET_COMMAND, info).to_bytes()
      + CieloFrame("12345678", EXPERIMENT_DATA_FILE_GET_COMMAND, b"abcd").to_bytes()
    )
    device = make_device(responses)

    with self.assertRaisesRegex(Cielo6Error, "exceeded"):
      await device.request_experiment_data(experiment)

  async def test_request_experiment_data_rejects_invalid_file_information(self) -> None:
    experiment = Cielo6ExperimentInfo(workspace="Research", protocol="PCR", name="Run-1")
    cases = (
      (bytes(19), "expected 20"),
      ((-1).to_bytes(4, "little", signed=True) + bytes(16), "size"),
    )
    for payload, message in cases:
      with self.subTest(message=message):
        response = CieloFrame("12345678", EXPERIMENT_DATA_FILE_INFO_GET_COMMAND, payload).to_bytes()
        device = make_device(response)
        with self.assertRaisesRegex(Cielo6Error, message):
          await device.request_experiment_data(experiment)

  async def test_request_experiment_data_rejects_empty_chunk(self) -> None:
    experiment = Cielo6ExperimentInfo(workspace="Research", protocol="PCR", name="Run-1")
    info = (1).to_bytes(4, "little", signed=True) + hashlib.md5(b"x").digest()
    responses = (
      CieloFrame("12345678", EXPERIMENT_DATA_FILE_INFO_GET_COMMAND, info).to_bytes()
      + CieloFrame("12345678", EXPERIMENT_DATA_FILE_GET_COMMAND).to_bytes()
    )
    device = make_device(responses)

    with self.assertRaisesRegex(Cielo6Error, "download stopped"):
      await device.request_experiment_data(experiment)

  async def test_request_experiment_data_validates_path_before_io(self) -> None:
    cases = (
      (Cielo6ExperimentInfo("", "PCR", "Run-1"), "non-empty"),
      (Cielo6ExperimentInfo("Research", "PCR", "Rún-1"), "ASCII"),
    )
    for experiment, message in cases:
      with self.subTest(experiment=experiment):
        device = make_device()
        with self.assertRaisesRegex(ValueError, message):
          await device.request_experiment_data(experiment)
        cast(AsyncMock, device.io.write).assert_not_awaited()


class Cielo6StorageMutationTests(unittest.IsolatedAsyncioTestCase):
  async def test_operation_lock_serializes_storage_mutations(self) -> None:
    device = make_device()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def create_workspace(_workspace: str) -> None:
      entered.set()
      await release.wait()

    with (
      patch.object(device, "_create_workspace", new=AsyncMock(side_effect=create_workspace)),
      patch.object(device, "_delete_workspace", new=AsyncMock()) as delete_workspace,
    ):
      create_task = asyncio.create_task(device.create_workspace("First"))
      await entered.wait()
      delete_task = asyncio.create_task(device.delete_workspace("Second"))
      await asyncio.sleep(0)
      delete_workspace.assert_not_awaited()
      release.set()
      await asyncio.gather(create_task, delete_task)

    delete_workspace.assert_awaited_once_with("Second")

  async def test_status_read_remains_available_during_an_operation(self) -> None:
    response = CieloFrame(
      "12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)
    ).to_bytes()
    device = make_device(response)

    async with device._operation_lock:
      status = await device.request_status()

    self.assertTrue(status.is_running)

  async def test_create_workspace_preflights_and_confirms_readback(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = (
      workspace_summary_response()
      + CieloFrame("12345678", WORKSPACE_CREATE_COMMAND, success).to_bytes()
      + workspace_summary_response("Validation^")
    )
    device = make_device(responses)

    await device.create_workspace("Validation")

    self.assertEqual(
      [call.args[0] for call in cast(AsyncMock, device.io.write).call_args_list],
      [
        CieloFrame("12345678", WORKSPACE_SUMMARY_GET_COMMAND, struct.pack("<I", 0)).to_bytes(),
        CieloFrame("12345678", WORKSPACE_CREATE_COMMAND, b"Validation").to_bytes(),
        CieloFrame("12345678", WORKSPACE_SUMMARY_GET_COMMAND, struct.pack("<I", 0)).to_bytes(),
        CieloFrame("12345678", WORKSPACE_SUMMARY_GET_COMMAND, struct.pack("<I", 1)).to_bytes(),
      ],
    )

  async def test_create_workspace_is_noop_when_it_already_exists(self) -> None:
    device = make_device(workspace_summary_response("Validation^"))

    await device.create_workspace("Validation")

    commands = [frame.command for frame in written_frames(device)]
    self.assertNotIn(WORKSPACE_CREATE_COMMAND, commands)

  async def test_create_workspace_rejects_inconsistent_readback(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = (
      workspace_summary_response()
      + CieloFrame("12345678", WORKSPACE_CREATE_COMMAND, success).to_bytes()
      + workspace_summary_response()
    )
    device = make_device(responses)

    with self.assertRaisesRegex(Cielo6Error, "not present in readback"):
      await device.create_workspace("Validation")

  async def test_mutation_rejects_firmware_error(self) -> None:
    responses = (
      workspace_summary_response()
      + CieloFrame("12345678", WORKSPACE_CREATE_COMMAND, b"\x02\x5a").to_bytes()
    )
    device = make_device(responses)
    with self.assertRaisesRegex(Cielo6Error, "0x5a02"):
      await device.create_workspace("Validation")

  async def test_delete_workspace_rejects_non_empty_workspace(self) -> None:
    device = make_device(workspace_summary_response("Validation^RoundTrip"))
    with self.assertRaisesRegex(Cielo6Error, "non-empty"):
      await device.delete_workspace("Validation")
    self.assertEqual(cast(AsyncMock, device.io.write).await_count, 2)

  async def test_delete_program_confirms_readback(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = (
      workspace_summary_response("Validation^RoundTrip", "Validation^Keep")
      + CieloFrame("12345678", PROGRAM_DELETE_COMMAND, success).to_bytes()
      + workspace_summary_response("Validation^Keep")
    )
    device = make_device(responses)

    await device.delete_program("Validation", "RoundTrip")

    frames = written_frames(device)
    deletion = next(frame for frame in frames if frame.command == PROGRAM_DELETE_COMMAND)
    self.assertEqual(deletion.payload, b"Validation^RoundTrip")

  async def test_delete_program_is_noop_when_program_is_absent(self) -> None:
    device = make_device(workspace_summary_response("Validation^Keep"))

    await device.delete_program("Validation", "RoundTrip")

    commands = [frame.command for frame in written_frames(device)]
    self.assertNotIn(PROGRAM_DELETE_COMMAND, commands)

  async def test_delete_program_rejects_inconsistent_readback(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = (
      workspace_summary_response("Validation^RoundTrip")
      + CieloFrame("12345678", PROGRAM_DELETE_COMMAND, success).to_bytes()
      + workspace_summary_response("Validation^RoundTrip")
    )
    device = make_device(responses)

    with self.assertRaisesRegex(Cielo6Error, "remained"):
      await device.delete_program("Validation", "RoundTrip")

  async def test_delete_empty_workspace_confirms_readback(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = (
      workspace_summary_response("Validation^")
      + CieloFrame("12345678", WORKSPACE_DELETE_COMMAND, success).to_bytes()
      + workspace_summary_response()
    )
    device = make_device(responses)

    await device.delete_workspace("Validation")

    commands = [frame.command for frame in written_frames(device)]
    self.assertEqual(commands.count(WORKSPACE_DELETE_COMMAND), 1)

  async def test_delete_workspace_is_noop_when_absent(self) -> None:
    device = make_device(workspace_summary_response())

    await device.delete_workspace("Validation")

    commands = [frame.command for frame in written_frames(device)]
    self.assertNotIn(WORKSPACE_DELETE_COMMAND, commands)

  async def test_delete_workspace_rejects_inconsistent_readback(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = (
      workspace_summary_response("Validation^")
      + CieloFrame("12345678", WORKSPACE_DELETE_COMMAND, success).to_bytes()
      + workspace_summary_response("Validation^")
    )
    device = make_device(responses)

    with self.assertRaisesRegex(Cielo6Error, "remained in readback"):
      await device.delete_workspace("Validation")

  async def test_storage_names_match_firmware_field_width(self) -> None:
    device = make_device()
    with self.assertRaisesRegex(ValueError, "30 ASCII"):
      await device.create_workspace("x" * 31)


class Cielo6RunCommandTests(unittest.IsolatedAsyncioTestCase):
  async def test_lock_returns_status_snapshot(self) -> None:
    response = CieloFrame("12345678", SESSION_LOCK_COMMAND, status_payload()).to_bytes()
    device = make_device(response)

    status = await device._lock()

    self.assertIsInstance(status, Cielo6Status)
    self.assertEqual(status.work_status, WORK_STATUS_IDLE)
    self.assertEqual(device.latest_status, status)
    cast(AsyncMock, device.io.write).assert_awaited_once_with(
      CieloFrame("12345678", SESSION_LOCK_COMMAND).to_bytes()
    )

  async def test_initialize_uses_mutation_ack(self) -> None:
    response = CieloFrame(
      "12345678", INITIALIZE_COMMAND, EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    ).to_bytes()
    device = make_device(response)

    await device._initialize()

    cast(AsyncMock, device.io.write).assert_awaited_once_with(
      CieloFrame("12345678", INITIALIZE_COMMAND).to_bytes()
    )

  async def test_upload_program_sends_indexed_chunks(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = b"".join(
      CieloFrame("12345678", PROGRAM_UPLOAD_COMMAND, success).to_bytes() for _ in range(16)
    )
    device = make_device(responses)
    program = Cielo6StoredProgram.from_bytes(program_bytes())

    await device._upload_program(program)

    writes = [call.args[0] for call in cast(AsyncMock, device.io.write).call_args_list]
    self.assertEqual(len(writes), 16)
    for index, encoded in enumerate(writes):
      frame = CieloFrame.from_bytes(encoded)
      self.assertEqual(frame.command, PROGRAM_UPLOAD_COMMAND)
      self.assertEqual(frame.payload[0], index)
      self.assertEqual(
        frame.payload[1:],
        program.to_bytes()[index * PROGRAM_CHUNK_SIZE : (index + 1) * PROGRAM_CHUNK_SIZE],
      )

  async def test_set_result_path_joins_names(self) -> None:
    response = CieloFrame(
      "12345678", RESULT_PATH_SET_COMMAND, EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    ).to_bytes()
    device = make_device(response)

    await device._set_result_path("Public", "PLR-Short-2C", "Run-1")

    cast(AsyncMock, device.io.write).assert_awaited_once_with(
      CieloFrame("12345678", RESULT_PATH_SET_COMMAND, b"Public^PLR-Short-2C^Run-1").to_bytes()
    )

  async def test_start_run_confirms_running_without_run_ack(self) -> None:
    response = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload()).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)).to_bytes()
    )
    device = make_device(response)

    status = await device._start_run(wait=0.0, attempts=1)

    self.assertEqual(status.work_status, WORK_STATUS_RUNNING)
    self.assertEqual(
      [call.args[0] for call in cast(AsyncMock, device.io.write).call_args_list],
      [
        CieloFrame("12345678", STATUS_QUERY_COMMAND).to_bytes(),
        CieloFrame("12345678", RUN_COMMAND).to_bytes(),
        CieloFrame("12345678", STATUS_QUERY_COMMAND).to_bytes(),
      ],
    )

  async def test_start_run_rejects_idle_state(self) -> None:
    response = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload()).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload()).to_bytes()
    )
    device = make_device(response)
    with self.assertRaisesRegex(Cielo6RunTimeoutError, "may still be active"):
      await device._start_run(wait=0.0, attempts=1)

  async def test_start_run_waits_through_firmware_preparing_state(self) -> None:
    responses = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload()).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload()).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)).to_bytes()
    )
    device = make_device(responses)

    status = await device._start_run(wait=0.0, attempts=3)

    self.assertEqual(status.work_status, WORK_STATUS_RUNNING)
    commands = [frame.command for frame in written_frames(device)]
    self.assertEqual(commands, [STATUS_QUERY_COMMAND, RUN_COMMAND, *([STATUS_QUERY_COMMAND] * 2)])

  async def test_start_run_validates_confirmation_bounds_before_io(self) -> None:
    device = make_device()

    with self.assertRaisesRegex(ValueError, "wait cannot be negative"):
      await device._start_run(wait=-1)
    with self.assertRaisesRegex(ValueError, "attempts must be at least 1"):
      await device._start_run(attempts=0)

    cast(AsyncMock, device.io.write).assert_not_awaited()

  async def test_start_run_accepts_short_run_with_new_identity(self) -> None:
    before = status_payload(WORK_STATUS_IDLE, 1)
    after = bytearray(before)
    struct.pack_into("<I", after, 16, 99)
    response = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, before).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, bytes(after)).to_bytes()
    )
    device = make_device(response)

    status = await device._start_run(wait=0.0, attempts=1)

    self.assertEqual(status.work_status, WORK_STATUS_IDLE)
    self.assertEqual(status.is_finished, 1)

  async def test_start_run_rejects_stale_finished_status(self) -> None:
    stale = status_payload(WORK_STATUS_IDLE, 1)
    response = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, stale).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, stale).to_bytes()
    )
    device = make_device(response)

    with self.assertRaisesRegex(Cielo6RunTimeoutError, "may still be active"):
      await device._start_run(wait=0.0, attempts=1)

  async def test_start_run_fails_immediately_on_firmware_error(self) -> None:
    response = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload()).to_bytes()
      + CieloFrame(
        "12345678", STATUS_QUERY_COMMAND, status_payload(Cielo6WorkState.RUN_ERROR)
      ).to_bytes()
    )
    device = make_device(response)

    with self.assertRaisesRegex(Cielo6FirmwareStateError, "RUN_ERROR") as raised:
      await device._start_run(wait=0.0, attempts=10)

    self.assertEqual(raised.exception.status.work_state, Cielo6WorkState.RUN_ERROR)
    self.assertEqual(cast(AsyncMock, device.io.write).await_count, 3)

  async def test_stop_run_and_disconnect_use_mutation_acks(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)).to_bytes()
      + CieloFrame("12345678", STOP_COMMAND, success).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload()).to_bytes()
      + CieloFrame("12345678", DISCONNECT_COMMAND, success).to_bytes()
    )
    device = make_device(responses)

    await device.stop_run()
    await device._disconnect_session()

    commands = [frame.command for frame in written_frames(device)]
    self.assertEqual(
      commands,
      [STATUS_QUERY_COMMAND, STOP_COMMAND, STATUS_QUERY_COMMAND, DISCONNECT_COMMAND],
    )

  async def test_stop_run_is_noop_when_firmware_is_idle(self) -> None:
    response = CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload()).to_bytes()
    device = make_device(response)

    await device.stop_run()

    self.assertEqual([frame.command for frame in written_frames(device)], [STATUS_QUERY_COMMAND])

  async def test_stop_run_keeps_context_when_status_is_still_active(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)).to_bytes()
      + CieloFrame("12345678", STOP_COMMAND, success).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)).to_bytes()
    )
    device = make_device(responses)
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    device._active_program = program

    with self.assertRaisesRegex(Cielo6Error, "still reports an active run"):
      await device.stop_run()

    self.assertIs(device._active_program, program)

  async def test_stop_run_does_not_clear_context_on_firmware_error(self) -> None:
    responses = CieloFrame(
      "12345678", STATUS_QUERY_COMMAND, status_payload(Cielo6WorkState.STOP_ERROR)
    ).to_bytes()
    device = make_device(responses)
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    device._active_program = program

    with self.assertRaisesRegex(Cielo6FirmwareStateError, "STOP_ERROR"):
      await device.stop_run()

    self.assertIs(device._active_program, program)

  async def test_pause_run_sends_command_and_confirms_paused_state(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)).to_bytes()
      + CieloFrame("12345678", PAUSE_COMMAND, success).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_PAUSED)).to_bytes()
    )
    device = make_device(responses)

    await device.pause_run()

    self.assertEqual(
      [frame.command for frame in written_frames(device)],
      [STATUS_QUERY_COMMAND, PAUSE_COMMAND, STATUS_QUERY_COMMAND],
    )

  async def test_pause_run_is_noop_when_already_paused(self) -> None:
    response = CieloFrame(
      "12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_PAUSED)
    ).to_bytes()
    device = make_device(response)

    await device.pause_run()

    self.assertEqual([frame.command for frame in written_frames(device)], [STATUS_QUERY_COMMAND])

  async def test_pause_run_rejects_idle_state(self) -> None:
    response = CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload()).to_bytes()
    device = make_device(response)

    with self.assertRaisesRegex(Cielo6Error, "no run is active"):
      await device.pause_run()

  async def test_pause_run_rejects_inconsistent_readback(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)).to_bytes()
      + CieloFrame("12345678", PAUSE_COMMAND, success).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)).to_bytes()
    )
    device = make_device(responses)

    with self.assertRaisesRegex(Cielo6Error, "did not report a paused run"):
      await device.pause_run()

  async def test_resume_run_sends_command_and_confirms_running_state(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_PAUSED)).to_bytes()
      + CieloFrame("12345678", RESUME_COMMAND, success).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)).to_bytes()
    )
    device = make_device(responses)

    await device.resume_run()

    self.assertEqual(
      [frame.command for frame in written_frames(device)],
      [STATUS_QUERY_COMMAND, RESUME_COMMAND, STATUS_QUERY_COMMAND],
    )

  async def test_resume_run_is_noop_when_already_running(self) -> None:
    response = CieloFrame(
      "12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)
    ).to_bytes()
    device = make_device(response)

    await device.resume_run()

    self.assertEqual([frame.command for frame in written_frames(device)], [STATUS_QUERY_COMMAND])

  async def test_resume_run_rejects_idle_state(self) -> None:
    response = CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload()).to_bytes()
    device = make_device(response)

    with self.assertRaisesRegex(Cielo6Error, "no run is paused"):
      await device.resume_run()

  async def test_resume_run_rejects_inconsistent_readback(self) -> None:
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    responses = (
      CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_PAUSED)).to_bytes()
      + CieloFrame("12345678", RESUME_COMMAND, success).to_bytes()
      + CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_PAUSED)).to_bytes()
    )
    device = make_device(responses)

    with self.assertRaisesRegex(Cielo6Error, "did not report an active run"):
      await device.resume_run()


class Cielo6RunWorkflowTests(unittest.IsolatedAsyncioTestCase):
  async def test_wait_for_completion_times_out_with_latest_running_state(self) -> None:
    device = make_device()
    running = Cielo6Status.from_payload(status_payload(WORK_STATUS_RUNNING))
    with patch.object(device, "request_status", new=AsyncMock(return_value=running)):
      with self.assertRaisesRegex(Cielo6RunTimeoutError, "may still be active"):
        await device._wait_for_completion(poll_interval=0.0, timeout=0.0)

  async def test_wait_for_completion_fails_immediately_on_firmware_error(self) -> None:
    device = make_device()
    error = Cielo6Status.from_payload(status_payload(Cielo6WorkState.ERROR_1))
    with patch.object(device, "request_status", new=AsyncMock(return_value=error)):
      with self.assertRaisesRegex(Cielo6FirmwareStateError, "ERROR_1"):
        await device._wait_for_completion(poll_interval=1.0, timeout=None)

  async def test_wait_for_completion_allows_finished_flag_to_settle_after_idle(self) -> None:
    device = make_device()
    idle = Cielo6Status.from_payload(status_payload(WORK_STATUS_IDLE, 0))
    finished = Cielo6Status.from_payload(status_payload(WORK_STATUS_IDLE, 1))
    with (
      patch.object(device, "request_status", new=AsyncMock(side_effect=(idle, finished))),
      patch("pylabrobot.azure_biosystems.cielo6.asyncio.sleep", new=AsyncMock()) as sleep,
    ):
      result = await device._wait_for_completion(poll_interval=1.0, timeout=None)

    self.assertIs(result, finished)
    sleep.assert_awaited_once_with(0.25)

  async def test_wait_for_completion_rejects_persistent_idle_without_completion(self) -> None:
    device = make_device()
    idle = Cielo6Status.from_payload(status_payload(WORK_STATUS_IDLE, 0))
    with (
      patch.object(device, "request_status", new=AsyncMock(return_value=idle)),
      patch("pylabrobot.azure_biosystems.cielo6.time.monotonic", side_effect=(0.0, 5.0)),
      patch("pylabrobot.azure_biosystems.cielo6.asyncio.sleep", new=AsyncMock()) as sleep,
    ):
      with self.assertRaisesRegex(Cielo6Error, "remained idle without.*completion"):
        await device._wait_for_completion(poll_interval=1.0, timeout=None)

    sleep.assert_awaited_once_with(0.25)

  async def test_run_experiment_validates_wait_parameters_before_io(self) -> None:
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    cases = ((0.0, None, "poll_interval"), (1.0, 0.0, "timeout"))

    for poll_interval, timeout, message in cases:
      with self.subTest(message=message):
        device = make_device()
        with self.assertRaisesRegex(ValueError, message):
          await device.run_experiment(
            program,
            workspace="Public",
            protocol="PCR",
            poll_interval=poll_interval,
            timeout=timeout,
          )
        cast(AsyncMock, device.io.write).assert_not_awaited()

  async def test_stop_during_preparation_prevents_run_dispatch(self) -> None:
    device = make_device()
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    idle = Cielo6Status.from_payload(status_payload())
    initialize_started = asyncio.Event()
    release_initialize = asyncio.Event()

    async def initialize() -> None:
      initialize_started.set()
      await release_initialize.wait()

    with (
      patch.object(device, "_lock", new=AsyncMock(return_value=idle)),
      patch.object(device, "_initialize", new=AsyncMock(side_effect=initialize)),
      patch.object(device, "_upload_program", new=AsyncMock()) as upload_program,
      patch.object(device, "_set_result_path", new=AsyncMock()),
      patch.object(device, "_start_run", new=AsyncMock()) as start_run,
      patch.object(device, "request_status", new=AsyncMock(return_value=idle)),
      patch.object(device, "_disconnect_session", new=AsyncMock()),
    ):
      run = asyncio.create_task(device.run_experiment(program, workspace="Public", protocol="PCR"))
      await initialize_started.wait()
      stop = asyncio.create_task(device.stop_run())
      await asyncio.sleep(0)
      release_initialize.set()
      with self.assertRaisesRegex(Cielo6Error, "stopped before dispatch"):
        await run
      await stop

    upload_program.assert_not_awaited()
    start_run.assert_not_awaited()

  async def test_start_confirmation_timeout_keeps_indeterminate_run_context(self) -> None:
    device = make_device()
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    idle = Cielo6Status.from_payload(status_payload())

    async def time_out_after_dispatch() -> Cielo6Status:
      device._run_phase = type(device._run_phase).DISPATCHED
      device.latest_status = idle
      raise Cielo6RunTimeoutError(idle)

    with (
      patch.object(device, "_lock", new=AsyncMock(return_value=idle)),
      patch.object(device, "_initialize", new=AsyncMock()),
      patch.object(device, "_upload_program", new=AsyncMock()),
      patch.object(device, "_set_result_path", new=AsyncMock()),
      patch.object(device, "_start_run", new=AsyncMock(side_effect=time_out_after_dispatch)),
      patch.object(device, "_disconnect_session", new=AsyncMock()),
    ):
      with self.assertRaises(Cielo6RunTimeoutError):
        await device.run_experiment(program, workspace="Public", protocol="PCR")

    self.assertIs(device._active_program, program)

  async def test_timeout_keeps_program_context_for_active_hardware(self) -> None:
    device = make_device()
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    idle = Cielo6Status.from_payload(status_payload())
    running = Cielo6Status.from_payload(status_payload(WORK_STATUS_RUNNING))

    async def report_dispatched_run() -> Cielo6Status:
      device._run_phase = type(device._run_phase).DISPATCHED
      return running

    with (
      patch.object(device, "_lock", new=AsyncMock(return_value=idle)),
      patch.object(device, "_initialize", new=AsyncMock()),
      patch.object(device, "_upload_program", new=AsyncMock()),
      patch.object(device, "_set_result_path", new=AsyncMock()),
      patch.object(device, "_start_run", new=AsyncMock(side_effect=report_dispatched_run)),
      patch.object(
        device,
        "_wait_for_completion",
        new=AsyncMock(side_effect=Cielo6RunTimeoutError(running)),
      ),
      patch.object(device, "_disconnect_session", new=AsyncMock()),
    ):
      with self.assertRaises(Cielo6RunTimeoutError):
        await device.run_experiment(
          program,
          workspace="Public",
          protocol="PCR",
          poll_interval=1.0,
        )

    self.assertIs(device._active_program, program)

  async def test_cancellation_after_run_dispatch_keeps_context_and_releases_session(self) -> None:
    device = make_device()
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    idle = Cielo6Status.from_payload(status_payload())

    async def cancel_after_dispatch() -> None:
      device._run_phase = type(device._run_phase).DISPATCHED
      raise asyncio.CancelledError

    with (
      patch.object(device, "_lock", new=AsyncMock(return_value=idle)),
      patch.object(device, "_initialize", new=AsyncMock()),
      patch.object(device, "_upload_program", new=AsyncMock()),
      patch.object(device, "_set_result_path", new=AsyncMock()),
      patch.object(device, "_start_run", new=AsyncMock(side_effect=cancel_after_dispatch)),
      patch.object(device, "_disconnect_session", new=AsyncMock()) as disconnect,
    ):
      with self.assertRaises(asyncio.CancelledError):
        await device.run_experiment(program, workspace="Public", protocol="PCR")

    self.assertIs(device._active_program, program)
    disconnect.assert_awaited_once()

  async def test_run_experiment_rejects_active_firmware_state_before_initialize(self) -> None:
    device = make_device()
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    running = Cielo6Status.from_payload(status_payload(WORK_STATUS_RUNNING))
    with (
      patch.object(device, "_lock", new=AsyncMock(return_value=running)),
      patch.object(device, "_initialize", new=AsyncMock()) as initialize,
      patch.object(device, "_disconnect_session", new=AsyncMock()) as disconnect,
    ):
      with self.assertRaisesRegex(Cielo6Error, "already has an active run"):
        await device.run_experiment(program, workspace="Public", protocol="PCR")

    initialize.assert_not_awaited()
    disconnect.assert_awaited_once()

  async def test_run_experiment_rejects_error_state_and_releases_session(self) -> None:
    device = make_device()
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    error = Cielo6Status.from_payload(status_payload(Cielo6WorkState.RUN_ERROR))
    with (
      patch.object(device, "_lock", new=AsyncMock(return_value=error)),
      patch.object(device, "_initialize", new=AsyncMock()) as initialize,
      patch.object(device, "_disconnect_session", new=AsyncMock()) as disconnect,
    ):
      with self.assertRaisesRegex(Cielo6FirmwareStateError, "RUN_ERROR"):
        await device.run_experiment(program, workspace="Public", protocol="PCR")

    initialize.assert_not_awaited()
    disconnect.assert_awaited_once()

  async def test_error_after_run_dispatch_keeps_context_and_releases_session(self) -> None:
    device = make_device()
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    idle = Cielo6Status.from_payload(status_payload())
    running = Cielo6Status.from_payload(status_payload(WORK_STATUS_RUNNING))
    error = Cielo6Status.from_payload(status_payload(Cielo6WorkState.ERROR_1))

    async def report_dispatched_run() -> Cielo6Status:
      device._run_phase = type(device._run_phase).DISPATCHED
      return running

    async def report_error(*, poll_interval: float, timeout: object) -> Cielo6Status:
      del poll_interval, timeout
      device.latest_status = error
      raise Cielo6FirmwareStateError("waiting for run completion", error)

    with (
      patch.object(device, "_lock", new=AsyncMock(return_value=idle)),
      patch.object(device, "_initialize", new=AsyncMock()),
      patch.object(device, "_upload_program", new=AsyncMock()),
      patch.object(device, "_set_result_path", new=AsyncMock()),
      patch.object(device, "_start_run", new=AsyncMock(side_effect=report_dispatched_run)),
      patch.object(device, "_wait_for_completion", new=AsyncMock(side_effect=report_error)),
      patch.object(device, "_disconnect_session", new=AsyncMock()) as disconnect,
    ):
      with self.assertRaisesRegex(Cielo6FirmwareStateError, "ERROR_1"):
        await device.run_experiment(program, workspace="Public", protocol="PCR")

    self.assertIs(device._active_program, program)
    disconnect.assert_awaited_once()

  async def test_run_experiment_serializes_complete_run_workflows(self) -> None:
    device = make_device()
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    idle = Cielo6Status.from_payload(status_payload())
    experiment = Cielo6ExperimentInfo("Public", "PCR", "Result")
    first_initialize_started = asyncio.Event()
    release_first_initialize = asyncio.Event()

    async def initialize() -> None:
      if not first_initialize_started.is_set():
        first_initialize_started.set()
        await release_first_initialize.wait()

    with (
      patch.object(device, "_lock", new=AsyncMock(return_value=idle)),
      patch.object(device, "_initialize", new=AsyncMock(side_effect=initialize)) as initialize_mock,
      patch.object(device, "_upload_program", new=AsyncMock()),
      patch.object(device, "_set_result_path", new=AsyncMock()),
      patch.object(device, "_start_run", new=AsyncMock(return_value=idle)),
      patch.object(device, "_wait_for_completion", new=AsyncMock(return_value=idle)),
      patch.object(device, "_find_experiment", new=AsyncMock(return_value=experiment)),
      patch.object(
        device, "request_experiment_data", new=AsyncMock(return_value=result_file_bytes())
      ),
      patch.object(device, "_disconnect_session", new=AsyncMock()),
    ):
      first = asyncio.create_task(
        device.run_experiment(program, workspace="Public", protocol="PCR", result_name="First")
      )
      await first_initialize_started.wait()
      second = asyncio.create_task(
        device.run_experiment(program, workspace="Public", protocol="PCR", result_name="Second")
      )
      await asyncio.sleep(0)
      self.assertEqual(initialize_mock.await_count, 1)
      release_first_initialize.set()
      await asyncio.gather(first, second)

    self.assertEqual(initialize_mock.await_count, 2)

  async def test_default_result_name_is_bounded_and_not_based_on_protocol_length(self) -> None:
    device = make_device()
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    idle = Cielo6Status.from_payload(status_payload())
    experiment = Cielo6ExperimentInfo("Public", "P" * 30, "Result")
    with (
      patch.object(device, "_lock", new=AsyncMock(return_value=idle)),
      patch.object(device, "_initialize", new=AsyncMock()),
      patch.object(device, "_upload_program", new=AsyncMock()),
      patch.object(device, "_set_result_path", new=AsyncMock()) as set_result_path,
      patch.object(device, "_start_run", new=AsyncMock(return_value=idle)),
      patch.object(device, "_wait_for_completion", new=AsyncMock(return_value=idle)),
      patch.object(device, "_find_experiment", new=AsyncMock(return_value=experiment)),
      patch.object(
        device, "request_experiment_data", new=AsyncMock(return_value=result_file_bytes())
      ),
      patch.object(device, "_disconnect_session", new=AsyncMock()),
    ):
      await device.run_experiment(program, workspace="Public", protocol="P" * 30)

    assert set_result_path.await_args is not None
    result_name = set_result_path.await_args.args[2]
    self.assertLessEqual(len(result_name), 30)
    self.assertRegex(result_name, r"^PLR-\d{8}-\d{6}-\d{6}$")

  async def test_find_experiment_rejects_missing_completed_result(self) -> None:
    device = make_device()
    with patch.object(device, "request_experiment_summary", new=AsyncMock(return_value=())):
      with self.assertRaisesRegex(Cielo6Error, "was not present"):
        await device._find_experiment("Public", "PCR", "Run-1")

  async def test_run_experiment_orchestrates_verified_sequence(self) -> None:
    result_data = result_file_bytes()
    running = running_data_payload()
    success = EXEC_SUCCESSFUL.to_bytes(2, byteorder="little")
    info = (
      len(result_data).to_bytes(4, byteorder="little", signed=True)
      + hashlib.md5(result_data).digest()
    )
    frames = [
      CieloFrame("12345678", SESSION_LOCK_COMMAND, status_payload()).to_bytes(),
      CieloFrame("12345678", INITIALIZE_COMMAND, success).to_bytes(),
    ]
    frames.extend(
      CieloFrame("12345678", PROGRAM_UPLOAD_COMMAND, success).to_bytes() for _ in range(16)
    )
    frames.extend(
      (
        CieloFrame("12345678", RESULT_PATH_SET_COMMAND, success).to_bytes(),
        CieloFrame("12345678", STATUS_QUERY_COMMAND, status_payload()).to_bytes(),
        CieloFrame(
          "12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_RUNNING)
        ).to_bytes(),
        CieloFrame("12345678", RUNNING_EXPERIMENT_DATA_UPLOAD_COMMAND, running).to_bytes(),
        CieloFrame(
          "12345678", STATUS_QUERY_COMMAND, status_payload(WORK_STATUS_IDLE, 1)
        ).to_bytes(),
      )
    )
    frames.append(
      experiment_summary_response(
        "Public^PLR-Short-2C^PLR-Short-2C-20260829-130646^2026-08-29_13:08:36^2026-08-29_13:12:35"
      )
    )
    frames.append(CieloFrame("12345678", EXPERIMENT_DATA_FILE_INFO_GET_COMMAND, info).to_bytes())
    frames.extend(
      CieloFrame(
        "12345678",
        EXPERIMENT_DATA_FILE_GET_COMMAND,
        result_data[start : start + 255],
      ).to_bytes()
      for start in range(0, len(result_data), 255)
    )
    frames.append(CieloFrame("12345678", DISCONNECT_COMMAND, success).to_bytes())
    device = make_device(b"".join(frames))

    result = await device.run_experiment(
      Cielo6StoredProgram.from_bytes(program_bytes()),
      workspace="Public",
      protocol="PLR-Short-2C",
      result_name="PLR-Short-2C-20260829-130646",
      poll_interval=0.001,
    )

    self.assertEqual(result.workspace, "Public")
    self.assertEqual(result.program, "PLR-Short-2C")
    self.assertEqual(len(device.running_data), 1)
    self.assertEqual(device.running_data[0], Cielo6RunningData.from_payload(running))
    commands = [frame.command for frame in written_frames(device)]
    self.assertEqual(
      commands,
      [
        SESSION_LOCK_COMMAND,
        INITIALIZE_COMMAND,
        *([PROGRAM_UPLOAD_COMMAND] * 16),
        RESULT_PATH_SET_COMMAND,
        STATUS_QUERY_COMMAND,
        RUN_COMMAND,
        STATUS_QUERY_COMMAND,
        STATUS_QUERY_COMMAND,
        EXPERIMENT_DATA_SUMMARY_GET_COMMAND,
        EXPERIMENT_DATA_SUMMARY_GET_COMMAND,
        EXPERIMENT_DATA_FILE_INFO_GET_COMMAND,
        EXPERIMENT_DATA_FILE_GET_COMMAND,
        DISCONNECT_COMMAND,
      ],
    )

  async def test_run_experiment_preserves_primary_error_when_session_release_fails(self) -> None:
    device = make_device()
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    with (
      patch.object(
        device,
        "_lock",
        new=AsyncMock(return_value=Cielo6Status.from_payload(status_payload())),
      ),
      patch.object(device, "_initialize", new=AsyncMock(side_effect=RuntimeError("run failed"))),
      patch.object(
        device,
        "_disconnect_session",
        new=AsyncMock(side_effect=Cielo6Error("release failed")),
      ) as disconnect,
    ):
      with self.assertRaisesRegex(RuntimeError, "run failed"):
        await device.run_experiment(
          program,
          workspace="Public",
          protocol="PLR-Short-2C",
          result_name="Run-1",
        )

    disconnect.assert_awaited_once()

  async def test_run_experiment_reports_session_release_failure_after_success(self) -> None:
    device = make_device()
    program = Cielo6StoredProgram.from_bytes(program_bytes())
    experiment = Cielo6ExperimentInfo("Public", "PLR-Short-2C", "Run-1")
    with (
      patch.object(
        device,
        "_lock",
        new=AsyncMock(return_value=Cielo6Status.from_payload(status_payload())),
      ),
      patch.object(device, "_initialize", new=AsyncMock()),
      patch.object(device, "_upload_program", new=AsyncMock()),
      patch.object(device, "_set_result_path", new=AsyncMock()),
      patch.object(device, "_start_run", new=AsyncMock()),
      patch.object(device, "_wait_for_completion", new=AsyncMock()),
      patch.object(device, "_find_experiment", new=AsyncMock(return_value=experiment)),
      patch.object(
        device,
        "request_experiment_data",
        new=AsyncMock(return_value=result_file_bytes()),
      ),
      patch.object(
        device,
        "_disconnect_session",
        new=AsyncMock(side_effect=Cielo6Error("release failed")),
      ),
    ):
      with self.assertRaisesRegex(Cielo6Error, "release failed"):
        await device.run_experiment(
          program,
          workspace="Public",
          protocol="PLR-Short-2C",
          result_name="Run-1",
        )

  async def test_run_protocol_compiles_and_delegates(self) -> None:
    device = make_device()
    template = Cielo6StoredProgram.from_bytes(program_bytes())
    protocol = Cielo6ThermalProtocol(
      steps=(
        Cielo6ThermalStep(95, 30),
        Cielo6ThermalStep(95, 5),
        Cielo6ThermalStep(60, 15, collect_fluorescence=True),
      ),
      repeat_from_step=1,
      cycles=2,
      sample_volume=20,
    )
    compiled = protocol.compile(template, workspace="Public", name="PLR-Short-2C")
    expected = Cielo6ResultFile.from_bytes(result_file_bytes())
    with (
      patch.object(device, "_create_workspace", new=AsyncMock()) as create_workspace,
      patch.object(
        device, "_run_experiment", new=AsyncMock(return_value=expected)
      ) as run_experiment,
    ):
      result = await device.run_protocol(
        protocol,
        template=template,
        workspace="Public",
        program_name="PLR-Short-2C",
        result_name="Run-1",
        poll_interval=0.5,
        timeout=10.0,
      )

    create_workspace.assert_not_awaited()
    run_experiment.assert_awaited_once_with(
      compiled,
      workspace="Public",
      protocol="PLR-Short-2C",
      result_name="Run-1",
      poll_interval=0.5,
      timeout=10.0,
      ensure_workspace=True,
    )
    self.assertEqual(result, expected)

  async def test_run_protocol_rejects_active_state_before_workspace_creation(self) -> None:
    device = make_device()
    template = Cielo6StoredProgram.from_bytes(program_bytes())
    protocol = Cielo6ThermalProtocol(steps=(Cielo6ThermalStep(30, 1),))
    running = Cielo6Status.from_payload(status_payload(WORK_STATUS_RUNNING))
    with (
      patch.object(device, "_lock", new=AsyncMock(return_value=running)),
      patch.object(device, "_create_workspace", new=AsyncMock()) as create_workspace,
      patch.object(device, "_disconnect_session", new=AsyncMock()),
    ):
      with self.assertRaisesRegex(Cielo6Error, "already has an active run"):
        await device.run_protocol(
          protocol,
          template=template,
          workspace="Validation",
          program_name="Short",
        )

    create_workspace.assert_not_awaited()


if __name__ == "__main__":
  unittest.main()
