import unittest
from collections import deque
from typing import Deque, List, Sequence, Tuple
from unittest.mock import AsyncMock, patch

from pylabrobot.thermo_fisher.btx.gemini.X2.file_transfer_control import (
  ProtocolDeletionPendingError,
  _FileTransferControl,
)

from .standard import ElectroporationProtocol


def _program_listing(entries: Sequence[Tuple[str, int]]) -> bytes:
  rows = [
    "Method name      Size",
    "---------------  ----",
  ]
  rows.extend([f"{name:<16} {size}" for name, size in entries])
  rows.append("")
  rows.append(f"{len(entries)} file(s) using {sum(size for _, size in entries)} steps")
  rows.append(":")
  return "\n".join(rows).encode("utf-8")


def _sd_listing(command: str, entries: Sequence[str]) -> bytes:
  rows = [command]
  rows.extend(entries)
  rows.append(":")
  return "\n".join(rows).encode("utf-8")


class _FakeSerial:
  def __init__(self) -> None:
    self.setup = AsyncMock()
    self.stop = AsyncMock()
    self.writes: List[bytes] = []
    self.read_chunks: Deque[bytes] = deque()

  async def write(self, data: bytes) -> None:
    self.writes.append(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    del num_bytes
    if len(self.read_chunks) == 0:
      return b""
    return self.read_chunks.popleft()


class _ConstructedSerial:
  instances: List["_ConstructedSerial"] = []

  def __init__(
    self,
    human_readable_device_name: str,
    port: str,
    baudrate: int,
    timeout: float,
    write_timeout: float,
  ) -> None:
    self.human_readable_device_name = human_readable_device_name
    self.port = port
    self.baudrate = baudrate
    self.timeout = timeout
    self.write_timeout = write_timeout
    self.setup = AsyncMock()
    self.stop = AsyncMock()
    _ConstructedSerial.instances.append(self)

  async def write(self, data: bytes) -> None:
    del data

  async def read(self, num_bytes: int = 1) -> bytes:
    del num_bytes
    return b""


class TestFileTransferControl(unittest.IsolatedAsyncioTestCase):
  async def test_setup_stop(self):
    fake = _FakeSerial()
    control = _FileTransferControl(serial_io=fake)

    await control.setup()
    await control.stop()

    fake.setup.assert_awaited_once_with()
    fake.stop.assert_awaited_once_with()

  async def test_setup_and_stop_are_idempotent(self):
    fake = _FakeSerial()
    control = _FileTransferControl(serial_io=fake)

    await control.setup()
    await control.setup()
    await control.stop()
    await control.stop()

    fake.setup.assert_awaited_once_with()
    fake.stop.assert_awaited_once_with()

  async def test_setup_failure_attempts_serial_cleanup(self):
    fake = _FakeSerial()
    fake.setup.side_effect = RuntimeError("open failed")
    control = _FileTransferControl(serial_io=fake)

    with self.assertRaisesRegex(RuntimeError, "open failed"):
      await control.setup()

    fake.stop.assert_awaited_once_with()

  async def test_setup_autodiscovers_btx_port_then_uses_shared_serial(self):
    _ConstructedSerial.instances.clear()

    with (
      patch(
        "pylabrobot.thermo_fisher.btx.gemini.X2.file_transfer_control.find_serial_ports",
        return_value=["/dev/cu.btx"],
      ) as find_ports,
      patch(
        "pylabrobot.thermo_fisher.btx.gemini.X2.file_transfer_control.Serial",
        _ConstructedSerial,
      ),
    ):
      control = _FileTransferControl()
      await control.setup()
      await control.stop()

    find_ports.assert_called_once_with(_FileTransferControl.SUPPORTED_USB_IDS)
    self.assertEqual(len(_ConstructedSerial.instances), 1)
    serial_io = _ConstructedSerial.instances[0]
    self.assertEqual(serial_io.port, "/dev/cu.btx")
    self.assertEqual(serial_io.baudrate, 9600)
    self.assertEqual(serial_io.timeout, 1.0)
    self.assertEqual(serial_io.write_timeout, 1.0)
    serial_io.setup.assert_awaited_once_with()
    serial_io.stop.assert_awaited_once_with()
    self.assertEqual(control.port, "/dev/cu.btx")

  async def test_list_protocols_parses_program_table(self):
    fake = _FakeSerial()
    fake.read_chunks.append(b"Y\n:")
    fake.read_chunks.append(_program_listing([("CD", 1), ("NECATOR", 8)]))
    fake.read_chunks.append(b"Y\n:")
    fake.read_chunks.append(_program_listing([("CD", 1), ("NECATOR", 8)]))
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    rows = await control.list_protocols_with_size()
    names = await control.list_protocols()

    self.assertEqual(rows, [{"name": "CD", "size": 1}, {"name": "NECATOR", "size": 8}])
    self.assertEqual(names, ["CD", "NECATOR"])
    self.assertEqual(
      fake.writes,
      [b"isprog\r\n", b'cat "*.BTX"\r\n', b"isprog\r\n", b'cat "*.BTX"\r\n'],
    )

  async def test_add_exponential_protocol_success(self):
    fake = _FakeSerial()
    fake.read_chunks.append(b"Y\n:")
    fake.read_chunks.append(_program_listing([("CD", 1)]))
    fake.read_chunks.append(b":")
    fake.read_chunks.append(b":")
    fake.read_chunks.append(b"Y\n:")
    fake.read_chunks.append(_program_listing([("CD", 1), ("TESTX", 1)]))
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    result = await control.add_protocol(
      "TESTX",
      ElectroporationProtocol(
        protocol_type="exponential",
        pulse_amplitude_volts=2400,
        gap_mm=2.0,
        resistance_ohms=200,
        capacitance_uf=25,
      ),
    )

    self.assertEqual(result["operation"], "add_protocol")
    self.assertEqual(result["protocol"], "TESTX")
    self.assertEqual(result["decoded"]["protocol_type"], "exponential")
    self.assertEqual(result["decoded"]["pulse_amplitude_volts"], 2400)
    self.assertEqual(result["decoded"]["resistance_ohms"], 200)
    self.assertEqual(result["decoded"]["capacitance_uf"], 25)
    self.assertEqual(result["decoded"]["pulse_count"], 1)
    self.assertAlmostEqual(result["decoded"]["electrode_gap_mm"], 2.0)
    self.assertTrue(fake.writes[2].startswith(b"meth "))
    self.assertEqual(fake.writes[3], b"mend\r\n")

  async def test_add_square_protocol_success(self):
    fake = _FakeSerial()
    fake.read_chunks.append(b"Y\n:")
    fake.read_chunks.append(_program_listing([("CD", 1)]))
    fake.read_chunks.append(b":")
    fake.read_chunks.append(b":")
    fake.read_chunks.append(b"Y\n:")
    fake.read_chunks.append(_program_listing([("CD", 1), ("SQTEST", 1)]))
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    result = await control.add_protocol(
      "SQTEST",
      ElectroporationProtocol(
        protocol_type="square",
        pulse_amplitude_volts=250,
        gap_mm=1.0,
        duration_us=1000,
      ),
    )

    self.assertEqual(result["decoded"]["protocol_type"], "square")
    self.assertEqual(result["decoded"]["pulse_amplitude_volts"], 250)
    self.assertEqual(result["decoded"]["pulse_duration_us"], 1000)
    self.assertAlmostEqual(result["decoded"]["electrode_gap_mm"], 1.0)
    self.assertEqual(result["decoded"]["pulse_count"], 1)
    self.assertEqual(result["decoded"]["pulse_interval_ms"], 0)
    self.assertEqual(result["decoded"]["pulse_interval_seconds"], 0.0)

  async def test_add_square_protocol_supports_multiple_pulse_interval(self):
    fake = _FakeSerial()
    fake.read_chunks.append(b"Y\n:")
    fake.read_chunks.append(_program_listing([("CD", 1)]))
    fake.read_chunks.append(b":")
    fake.read_chunks.append(b":")
    fake.read_chunks.append(b"Y\n:")
    fake.read_chunks.append(_program_listing([("CD", 1), ("SQMP", 1)]))
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    result = await control.add_protocol(
      "SQMP",
      ElectroporationProtocol(
        protocol_type="square",
        pulse_amplitude_volts=2400,
        gap_mm=2.0,
        pulse_count=3,
        pulse_interval_seconds=2.0,
        duration_us=500,
      ),
    )

    self.assertEqual(result["decoded"]["protocol_type"], "square")
    self.assertEqual(result["decoded"]["pulse_amplitude_volts"], 2400)
    self.assertEqual(result["decoded"]["pulse_duration_us"], 500)
    self.assertEqual(result["decoded"]["pulse_count"], 3)
    self.assertEqual(result["decoded"]["pulse_interval_ms"], 2000)
    self.assertEqual(result["decoded"]["pulse_interval_seconds"], 2.0)

  async def test_add_exponential_protocol_rejects_multiple_pulse_write(self):
    control = _FileTransferControl(serial_io=_FakeSerial())

    with self.assertRaisesRegex(ValueError, "currently support only pulse_count=1"):
      control._build_method_payload(
        "TEST",
        ElectroporationProtocol(
          protocol_type="exponential",
          pulse_amplitude_volts=250,
          gap_mm=1.0,
          pulse_count=2,
          pulse_interval_seconds=5.0,
          resistance_ohms=200,
          capacitance_uf=25,
        ),
      )

  async def test_overwrite_payload_is_validated_before_existing_protocol_is_deleted(self):
    fake = _FakeSerial()
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    with self.assertRaisesRegex(ValueError, "Missing pulse amplitude"):
      await control.add_protocol("TEST", {"protocol_type": "square"}, overwrite=True)

    self.assertEqual(fake.writes, [])

  def test_protocol_model_rejects_zero_pulses_and_mixed_waveform_fields(self):
    with self.assertRaisesRegex(ValueError, "pulse_count must be greater than zero"):
      ElectroporationProtocol(
        protocol_type="square",
        pulse_amplitude_volts=250,
        gap_mm=1.0,
        pulse_count=0,
        duration_us=1000,
      )
    with self.assertRaisesRegex(ValueError, "cannot define resistance"):
      ElectroporationProtocol(
        protocol_type="square",
        pulse_amplitude_volts=250,
        gap_mm=1.0,
        duration_us=1000,
        resistance_ohms=200,
      )

  async def test_request_protocol_decodes_payload(self):
    fake = _FakeSerial()
    fake.read_chunks.append(
      (
        b"meth "
        b"010000004A4A00000000000000000000000000000000000000000000000000000000000000000000"
        b"19000000000000000000000000000000320000002C01000001000000000000000000004000000000"
        b"000000000000000000000000000000000000000000000000\nmend\n:"
      )
    )
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    result = await control.request_protocol("JJ")

    self.assertEqual(result["protocol"], "JJ")
    self.assertEqual(result["decoded"]["name"], "JJ")
    self.assertEqual(result["decoded"]["pulse_amplitude_volts"], 25)
    self.assertEqual(result["decoded"]["resistance_ohms"], 50)
    self.assertEqual(result["decoded"]["capacitance_uf"], 300)
    self.assertEqual(result["decoded"]["pulse_count"], 1)
    self.assertEqual(result["decoded"]["pulse_interval_seconds"], 0.0)
    self.assertAlmostEqual(result["decoded"]["electrode_gap_mm"], 2.0)

  async def test_decode_manual_square_protocol_includes_interval(self):
    control = _FileTransferControl(serial_io=_FakeSerial())
    payload = bytes.fromhex(
      "01000000544553545351554152450000000000000000000000000000000000000100000000000000"
      "6009000000000000F401000000000000000000000000000003000000D00700000000004000000000"
      "000000000000000000000000000000000000000000000000"
    )

    decoded = control._decode_method_payload(payload)

    self.assertEqual(decoded["name"], "TESTSQUARE")
    self.assertEqual(decoded["protocol_type"], "square")
    self.assertEqual(decoded["pulse_amplitude_volts"], 2400)
    self.assertEqual(decoded["pulse_duration_us"], 500)
    self.assertEqual(decoded["pulse_count"], 3)
    self.assertEqual(decoded["pulse_interval_ms"], 2000)
    self.assertEqual(decoded["pulse_interval_seconds"], 2.0)

  async def test_build_square_payload_matches_known_manual_payload(self):
    control = _FileTransferControl(serial_io=_FakeSerial())

    payload = control._build_method_payload(
      "TESTSQUARE",
      ElectroporationProtocol(
        protocol_type="square",
        pulse_amplitude_volts=2400,
        gap_mm=2.0,
        pulse_count=3,
        pulse_interval_seconds=2.0,
        duration_us=500,
      ),
    )

    self.assertEqual(
      payload.hex().upper(),
      (
        "01000000544553545351554152450000000000000000000000000000000000000100000000000000"
        "6009000000000000F401000000000000000000000000000003000000D00700000000004000000000"
        "000000000000000000000000000000000000000000000000"
      ),
    )

  async def test_delete_protocol(self):
    fake = _FakeSerial()
    fake.read_chunks.append(b"Y\n:")
    fake.read_chunks.append(_program_listing([("CD", 1), ("TEST", 1)]))
    fake.read_chunks.append(b":")
    fake.read_chunks.append(b"Y\n:")
    fake.read_chunks.append(_program_listing([("CD", 1)]))
    fake.read_chunks.append(b"Y\n:")
    fake.read_chunks.append(_program_listing([("CD", 1)]))
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    result = await control.delete_protocol("TEST")

    self.assertTrue(result["deleted"])
    self.assertFalse(result["exists_after"])

  async def test_delete_protocol_raises_typed_pending_error(self):
    fake = _FakeSerial()
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    with (
      patch.object(control, "list_protocols", AsyncMock(return_value=["TEST"])),
      patch.object(control, "_send_text_command", AsyncMock(return_value=":")),
    ):
      with self.assertRaises(ProtocolDeletionPendingError):
        await control.delete_protocol("TEST")

  async def test_prompt_reader_rejects_empty_and_partial_responses(self):
    fake = _FakeSerial()
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    with self.assertRaisesRegex(TimeoutError, "without a terminating ':'"):
      await control._read_until_prompt(max_reads=1)

    fake.read_chunks.append(b"partial response")
    with self.assertRaisesRegex(TimeoutError, "without a terminating ':'"):
      await control._read_until_prompt(max_reads=1)

  async def test_single_value_command_rejects_a_prompt_without_a_value(self):
    fake = _FakeSerial()
    fake.read_chunks.append(b":")
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    with self.assertRaisesRegex(RuntimeError, "returned no value"):
      await control.request_version()

  def test_sd_paths_reject_control_characters_and_traversal(self):
    control = _FileTransferControl(serial_io=_FakeSerial())

    for path in ("\\BTXDATA\nversion", "\\BTXDATA\\..\\secret", "\\BTXDATA\\bad:name"):
      with self.subTest(path=path):
        with self.assertRaises(ValueError):
          control._normalize_sd_path(path)

  async def test_verify_protocol_rejects_any_changed_payload_field(self):
    control = _FileTransferControl(serial_io=_FakeSerial())
    expected = ElectroporationProtocol(
      protocol_type="square",
      pulse_amplitude_volts=250,
      gap_mm=1.0,
      duration_us=1000,
    )
    request_protocol = AsyncMock(
      return_value={
        "decoded": {
          "name": "TEST",
          "protocol_type": "square",
          "pulse_amplitude_volts": 251,
          "pulse_count": 1,
          "pulse_interval_ms": 0,
          "electrode_gap_mm": 1.0,
          "pulse_duration_us": 1000,
        }
      }
    )

    with patch.object(control, "request_protocol", request_protocol):
      with self.assertRaisesRegex(RuntimeError, "pulse_amplitude_volts"):
        await control.verify_protocol("TEST", expected)

  async def test_sd_dir_and_file_helpers(self):
    fake = _FakeSerial()
    fake.read_chunks.append(_sd_listing(r"sddir \BTXDATA", ["2026-03"]))
    fake.read_chunks.append(
      (
        "sdsend \\BTXDATA\\2026-03\\260309\\153425.TXT\n"
        "Protocol Name: H16_C\n"
        "Protocol Result: Complete\n"
        ":\n"
      ).encode("utf-8")
    )
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    entries = await control.list_sd_dir(r"\BTXDATA")
    content = await control.fetch_sd_file(r"\BTXDATA\2026-03\260309\153425.TXT")

    self.assertEqual(entries, ["2026-03"])
    self.assertEqual(content, "Protocol Name: H16_C\nProtocol Result: Complete")

  async def test_list_log_files_walks_btxdata_tree(self):
    fake = _FakeSerial()
    fake.read_chunks.append(_sd_listing(r"sddir \BTXDATA", ["2026-03", "notes"]))
    fake.read_chunks.append(_sd_listing(r"sddir \BTXDATA\2026-03", ["260308", "260309"]))
    fake.read_chunks.append(_sd_listing(r"sddir \BTXDATA\2026-03\260308", ["113530PP.TXT"]))
    fake.read_chunks.append(
      _sd_listing(r"sddir \BTXDATA\2026-03\260309", ["153008.TXT", "153425.TXT"])
    )
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    logs = await control.list_log_files()

    self.assertEqual(
      logs,
      [
        r"\BTXDATA\2026-03\260308\113530PP.TXT",
        r"\BTXDATA\2026-03\260309\153008.TXT",
        r"\BTXDATA\2026-03\260309\153425.TXT",
      ],
    )

  async def test_request_device_info_helpers(self):
    fake = _FakeSerial()
    fake.read_chunks.append(b"BTX Gemini 4.0.4\nSerial number: 1135421\n:")
    fake.read_chunks.append(b"1135421\n:")
    fake.read_chunks.append(b"03/06/2026 2:36:11 PM\n:")
    fake.read_chunks.append(
      b"\nSuccessful Tx: 57295\nSuccessful Rx: 57296\nFailed: 0\nRetries: 0\n:"
    )
    control = _FileTransferControl(serial_io=fake)
    await control.setup()

    version = await control.request_version()
    serial_number = await control.request_serial_number()
    device_time = await control.request_device_time()
    stats = await control.request_comm_stats()

    self.assertEqual(version, "BTX Gemini 4.0.4")
    self.assertEqual(serial_number, "1135421")
    self.assertEqual(device_time, "03/06/2026 2:36:11 PM")
    self.assertEqual(stats["Successful Tx"], 57295)
    self.assertEqual(stats["Successful Rx"], 57296)

  async def test_parse_run_log_extracts_summary_fields(self):
    control = _FileTransferControl(serial_io=_FakeSerial())
    parsed = control.parse_run_log(
      "\n".join(
        [
          "Date/Time: 03/09/2026 3:34:25 PM",
          "Model: BTX Gemini",
          "Mode: Electroporation",
          "Serial Number: 1135421",
          "GUI Software Version: 4.0.4",
          "DC Pulse Generator Firmware Version: 4.0.4",
          "Auto-PrePulse: On",
          "Protocol Name: !PLR_154635",
          "Protocol Type: Exponential",
          "Pulse Amplitude: 2300 V",
          "Number of Pulses: 1",
          "Pulse Interval: 0 sec",
          "Electrode Gap: 2.0 mm",
          "Plate Columns: 3",
          "Resistance: 200 ohms",
          "Capacitance: 25 uF",
          "PrePulse External Load: 5000 ohms",
          "Droop: 0.0%",
          "Pulse 1 Voltage: 2303.53 V",
          "Pulse 1 Time Constant: 5021 us",
          "Pulse 1 Total Load: 199 ohms",
          "Protocol Result: Complete",
          "Status: 0x00000000.00000000 - No error.",
        ]
      )
    )

    self.assertEqual(parsed["summary"]["protocol_name"], "!PLR_154635")
    self.assertEqual(parsed["summary"]["protocol_type"], "Exponential")
    self.assertEqual(parsed["summary"]["plate_columns"], 3)
    self.assertEqual(parsed["summary"]["pulse_amplitude_volts"], 2300)
    self.assertEqual(parsed["summary"]["protocol_result"], "Complete")
    self.assertEqual(parsed["summary"]["status_code"], "0x00000000.00000000")
    self.assertEqual(parsed["summary"]["status_message"], "No error.")
    self.assertNotIn("raw_fields", parsed)
    self.assertNotIn("line_count", parsed)

  async def test_parse_run_log_extracts_tabular_fields(self):
    control = _FileTransferControl(serial_io=_FakeSerial())
    parsed = control.parse_run_log(
      "\n".join(
        [
          "Date (MM/DD/YYYY)\tTime (HHMMSS)\tModel\tMode\tSerial Number\tGUI Firmware\tDC Firmware\tAuto-PrePulse",
          "03/09/2026\t3:34:25 PM\tBTX Gemini\tElectroporation\t1135421\t4.0.4\t4.0.4\tOn",
          "",
          "Protocol Name\tProtocol Type\tPulse Amplitude (V)\t# of Pulses\tPulse Interval (sec)\tGap (mm)\tPlate Columns\tResistance (Ohms)\tCapacitance (uF)",
          "!PLR_0309160010\tExponential\t2300\t1\t0\t3.0\t3\t200\t25",
          "",
          "PrePulse:\tExternal Load (Ohms):\t5000\tDroop (%):\t0.0",
          "DC Pulses\tVoltage (V)\tTime Constant (us)\tTotal Load (Ohms)",
          "Pulse 1\t2303.53\t5021\t199",
          "",
          "Protocol Result\tStatus Code",
          "Complete\t0x00000000.00000000\t(No error.)",
        ]
      )
    )

    self.assertEqual(parsed["summary"]["date_time"], "03/09/2026 3:34:25 PM")
    self.assertEqual(parsed["summary"]["protocol_name"], "!PLR_0309160010")
    self.assertEqual(parsed["summary"]["pulse_amplitude_volts"], 2300)
    self.assertEqual(parsed["summary"]["plate_columns"], 3)
    self.assertAlmostEqual(parsed["summary"]["pulse_1_voltage_volts"], 2303.53)
    self.assertEqual(parsed["summary"]["pulse_1_time_constant_us"], 5021)
    self.assertEqual(parsed["summary"]["pulse_1_total_load_ohms"], 199)
    self.assertEqual(parsed["summary"]["status_code"], "0x00000000.00000000")
    self.assertEqual(parsed["summary"]["status_message"], "(No error.)")
