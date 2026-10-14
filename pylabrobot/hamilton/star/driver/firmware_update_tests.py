"""Firmware download boundaries, exercised through the real STAR command assembler."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, Mock, patch

from pylabrobot.hamilton.star.driver.errors import STARFirmwareError, check_fw_string_error
from pylabrobot.hamilton.star.driver.features.pipettes import Pipettes
from pylabrobot.hamilton.star.driver.firmware_update import _PipetteFirmwareImage
from pylabrobot.hamilton.star.driver.master import STARDriver
from pylabrobot.io.io import IOBase

VERSION = "4.0S j 2022-03-16"
PREVIOUS_VERSION = "4.0S a 2017-04-24"


def ach_record(kind: int, payload: bytes = b"", address: int = 0) -> str:
  """Build synthetic padded ACH records without distributing a manufacturer image."""
  data = bytes([32]) + address.to_bytes(2, "big") + bytes([kind]) + payload.ljust(32, b"\xff")
  return ":" + (data + bytes([-sum(data) % 256])).hex().upper()


def image_records(version: str = VERSION) -> list[str]:
  """A complete synthetic image containing an address, version, payload, and EOF."""
  return [
    ach_record(4, b"\x00\x00"),
    ach_record(0, version.encode()),
    ach_record(0, b"synthetic firmware", address=32),
    ach_record(1, address=65535),
  ]


class TestFirmwareImage(unittest.TestCase):
  """Reject malformed images before any channel can enter download mode."""

  def test_full_image_validation(self):
    valid = image_records()
    corrupt = valid.copy()
    corrupt[2] = corrupt[2][:-2] + "00"
    for label, records in (
      ("valid", valid),
      ("empty", []),
      ("checksum", corrupt),
      ("no EOF", valid[:-1]),
      ("early EOF", valid + [valid[1]]),
      ("no address", valid[1:]),
      ("wrong version", image_records(PREVIOUS_VERSION)),
      ("unsupported type", [valid[0], ach_record(2), valid[-1]]),
      ("ordinary Intel HEX", [":00000001FF"]),
    ):
      with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "GRUPXS_test.ACH"
        path.write_text("\r\n".join(records) + "\r\n")
        if label == "valid":
          image = _PipetteFirmwareImage.from_file(path, VERSION)
          self.assertEqual(image.records, tuple(valid))
          self.assertEqual(image.version, VERSION)
          self.assertEqual(len(image.sha256), 64)
        else:
          with self.assertRaises(ValueError):
            _PipetteFirmwareImage.from_file(path, VERSION)

  def test_other_module_filename_is_rejected(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "GRUC0_test.ACH"
      path.write_text("\n".join(image_records()))
      with self.assertRaisesRegex(ValueError, "GRUPXS"):
        _PipetteFirmwareImage.from_file(path, VERSION)


class FirmwareInstrument:
  """A channel application/downloader responding below the driver's command methods."""

  def __init__(self, target: str = "P1", fault: Optional[str] = None):
    """Configure the addressed channel and an optional failure at a protocol boundary."""
    self.target = target
    self.fault = fault
    self.commands: list[str] = []
    self.download = False
    self.programmed = 0
    self.previous_version = PREVIOUS_VERSION
    self.program_started = asyncio.Event()
    self.continue_programming = asyncio.Event()

  async def send(self, cmd: str, id_: Optional[int], **kwargs) -> Optional[str]:
    """Answer assembled protocol commands, enforcing AP's no-response behavior."""
    self.commands.append(cmd)
    module, command = cmd[:2], cmd[2:4]
    target = module == self.target
    if command == "AP":
      assert target and cmd == self.target + "AP" and kwargs["wait"] is False
      self.download = not self.download
      return None
    assert id_ is not None
    prefix = module + command + f"id{id_:04}"
    if command in ("DE", "DP"):
      assert target and self.download
      if command == "DE" and self.fault == "erase":
        return prefix + "er80"
      if command == "DP":
        self.programmed += 1
        if self.programmed == 2:
          if self.fault == "timeout":
            raise TimeoutError("Lost program acknowledgement")
          if self.fault == "cancel":
            raise asyncio.CancelledError()
          if self.fault == "error":
            response = prefix + "er80"
            check_fw_string_error(response)
            return response
          if self.fault == "missing_ack":
            return prefix
          if self.fault == "wrong_ack":
            return "P2DP" + f"id{id_:04}er00"
          if self.fault == "pause":
            self.program_started.set()
            await self.continue_programming.wait()
      return prefix + "er00"
    restarted = self.programmed > 0 and not self.download
    if command == "RF":
      if module == "C0":
        version = "7.5S 00 2017_02_02 (GRU C0 PLE)"
        if restarted and self.fault == "master":
          version = "7.5S 00 2018_02_02 (GRU C0 PLE)"
      elif target:
        version = VERSION if restarted and self.fault != "version" else self.previous_version
      else:
        version = PREVIOUS_VERSION if restarted and self.fault == "peer" else VERSION
      return prefix + "rf" + version
    if command == "RQ":
      return prefix + (
        "rq0800"
        if self.download and self.fault != "download"
        else "rq0001"
        if self.fault == "status"
        else "rq0000"
      )
    if command == "QV":
      return prefix + (
        "qv0001"
        if self.fault == "eeprom" or (restarted and self.fault == "eeprom_after")
        else "qv0000"
      )
    if command == "VW":
      return prefix + ("vw1 0" if self.fault == "hardware" else "vw0 0 0 0")
    if command == "QG":
      return prefix + ("qg0" if self.fault == "plunger" else "qg1")
    if command == "RV":
      return (
        prefix
        + ("na0000006357" if restarted and self.fault == "counters" else "na0000006426")
        + "nb0000001802nc0000023937nd0000047768"
      )
    if command == "RK":
      if self.fault == "empty_calibration":
        return prefix + "er00"
      return prefix + ("kd+001" if restarted and self.fault == "calibration" else "kd+000")
    if command == "QX":
      return prefix + "ke-0002 +5000 +0082 +0007 +0000"
    if command == "RJ":
      return prefix + "jd2025-09-30js1"
    if command == "QT":
      return prefix + "qt173305/0310897 173304/0522421"
    if command == "RA":
      assert cmd.endswith("raau")
      return prefix + "au1 0 0 0"
    raise AssertionError(f"Unexpected command: {cmd}")


class TestFirmwareUpdate(unittest.IsolatedAsyncioTestCase):
  """Test the entire update with real assembly/parsing and synthetic firmware."""

  async def asyncSetUp(self):
    """Create an offline driver and an image; skip only the physical reboot delays."""
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.path = Path(self.directory.name) / "GRUPXS_test.ACH"
    self.records = image_records()
    self.path.write_text("\r\n".join(self.records) + "\r\n")
    self.driver = STARDriver(io=Mock(spec=IOBase))
    self.driver._connected = True
    self.driver._num_channels = 12
    self.pipettes = Pipettes(self.driver)
    self.pipettes.configuration.resolve_channels(12)
    patcher = patch("pylabrobot.hamilton.star.driver.features.pipettes.asyncio", wraps=asyncio)
    patched = patcher.start()
    self.addCleanup(patcher.stop)
    patched.sleep = AsyncMock()
    self.delays = patched.sleep
    self.instrument = FirmwareInstrument()
    self.driver._replies.send = AsyncMock(side_effect=self.instrument.send)  # type: ignore[method-assign]

  async def update(self, channel: int = 0, allow_downgrade: bool = False):
    """Run the public update API against the synthetic image."""
    return await self.pipettes.update_firmware(
      channel, self.path, expected_version=VERSION, allow_downgrade=allow_downgrade
    )

  def mutations(self) -> list[str]:
    """Return the commands that can change firmware or persistent channel state."""
    return [cmd for cmd in self.instrument.commands if cmd[2:4] in ("AP", "DE", "DP", "AO", "AV")]

  async def test_complete_transfer_addresses_only_the_selected_channel(self):
    self.instrument.target = "PB"
    result = await self.update(channel=10)
    changes = self.mutations()
    self.assertTrue(all(cmd.startswith("PB") for cmd in changes))
    self.assertEqual([cmd[2:4] for cmd in changes], ["AP", "DE", "DP", "DP", "DP", "DP", "AP"])
    self.assertEqual([cmd[12:] for cmd in changes if cmd[2:4] == "DP"], self.records)
    self.assertEqual(result.version, VERSION)
    self.assertEqual(result.records_programmed, len(self.records))
    self.assertEqual(result.cycle_counts_before, result.cycle_counts_after)
    self.assertEqual(self.pipettes.configuration.channels[10].firmware_version, VERSION)
    self.assertEqual([call.args[0] for call in self.delays.await_args_list], [15, 18])

  async def test_invalid_input_sends_nothing(self):
    for channel in (-1, 12, True):
      with self.assertRaises(ValueError):
        await self.update(channel)
    self.path.write_text("\n".join(self.records[:-1]))
    with self.assertRaises(ValueError):
      await self.update()
    self.assertEqual(self.instrument.commands, [])

  async def test_preflight_errors_send_no_mutations(self):
    for fault in ("status", "eeprom", "hardware", "plunger", "empty_calibration"):
      with self.subTest(fault=fault):
        self.instrument.fault = fault
        with self.assertRaises((ValueError, RuntimeError)):
          await self.update()
        self.assertEqual(self.mutations(), [])

  async def test_downgrade_requires_explicit_opt_in(self):
    self.instrument.previous_version = "4.0S k 2023-03-16"
    with self.assertRaisesRegex(ValueError, "allow_downgrade"):
      await self.update()
    self.assertEqual(self.mutations(), [])
    result = await self.update(allow_downgrade=True)
    self.assertEqual(result.version, VERSION)

  async def test_download_status_is_checked_before_erase(self):
    self.instrument.fault = "download"
    with self.assertRaisesRegex(RuntimeError, "download mode"):
      await self.update()
    self.assertEqual(self.mutations(), ["P1AP"])

  async def test_erase_failure_does_not_program_or_restart(self):
    self.instrument.fault = "erase"
    with self.assertRaises(RuntimeError):
      await self.update()
    self.assertEqual([cmd[2:4] for cmd in self.mutations()], ["AP", "DE"])

  async def test_uncertain_program_does_not_retry_or_restart(self):
    for fault in ("timeout", "error", "missing_ack", "wrong_ack", "cancel"):
      with self.subTest(fault=fault):
        self.instrument = FirmwareInstrument(fault=fault)
        self.driver._replies.send = AsyncMock(side_effect=self.instrument.send)  # type: ignore[method-assign]
        with self.assertRaises(
          (RuntimeError, TimeoutError, STARFirmwareError, asyncio.CancelledError)
        ):
          await self.update()
        self.assertEqual([cmd[2:4] for cmd in self.mutations()], ["AP", "DE", "DP", "DP"])
        self.assertIsNone(self.driver._firmware_update_task)
        self.assertIsNone(self.pipettes.configuration.channels[0].firmware_version)
        self.assertNotIn("pipettes", self.driver.firmware)

  async def test_verification_failures_are_reported(self):
    for fault in ("version", "calibration", "eeprom_after", "peer", "master"):
      with self.subTest(fault=fault):
        self.instrument = FirmwareInstrument(fault=fault)
        self.driver._replies.send = AsyncMock(side_effect=self.instrument.send)  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
          await self.update()
        self.assertEqual(sum(cmd[2:4] == "AP" for cmd in self.mutations()), 2)

  async def test_counter_changes_are_reported_without_rewriting_them(self):
    self.instrument.fault = "counters"
    with self.assertLogs("pylabrobot.hamilton.star.driver.features.pipettes", level="WARNING"):
      result = await self.update()
    self.assertEqual(result.cycle_counts_before.tip_pickups, 6426)
    self.assertEqual(result.cycle_counts_after.tip_pickups, 6357)
    self.assertFalse(any(cmd[2:4] in ("AV", "AO") for cmd in self.instrument.commands))
    self.assertEqual(self.driver.firmware["pipettes"], VERSION)

  async def test_update_waits_for_motion_already_in_progress(self):
    """An existing X-arm motion must finish before the update starts querying or switching modes."""
    async with self.driver._lock.subsystem("X0"):
      task = asyncio.create_task(self.update())
      await asyncio.sleep(0)
      self.assertEqual(self.instrument.commands, [])
    await task

  async def test_other_tasks_cannot_send_commands_during_the_update(self):
    self.instrument.fault = "pause"
    task = asyncio.create_task(self.update())
    await asyncio.wait_for(self.instrument.program_started.wait(), timeout=2)
    try:
      for command in ("RF", "JY"):
        with self.assertRaisesRegex(RuntimeError, "firmware update"):
          await self.driver.send_command("C0", command)
      with self.assertRaisesRegex(RuntimeError, "firmware update"):
        await self.driver.send_raw_command("P1RF")
      with self.assertRaisesRegex(RuntimeError, "already in progress"):
        async with self.driver._firmware_update_session():
          pass
    finally:
      self.instrument.continue_programming.set()
      await task
    await self.driver.send_command("P1", "RF")
