"""Firmware download boundaries, exercised through the real STAR command assembler."""

import asyncio
import datetime
import tempfile
import unittest
from pathlib import Path
from typing import Optional, Union
from unittest.mock import AsyncMock, Mock, patch

from pylabrobot.hamilton.star.driver.configuration import DeviceConfiguration
from pylabrobot.hamilton.star.driver.errors import STARFirmwareError, check_fw_string_error
from pylabrobot.hamilton.star.driver.features.autoload import Autoload
from pylabrobot.hamilton.star.driver.features.head96 import Head96
from pylabrobot.hamilton.star.driver.features.head384 import Head384
from pylabrobot.hamilton.star.driver.features.iswap import iSWAP
from pylabrobot.hamilton.star.driver.features.pipettes import PipetteCycleCounts, Pipettes
from pylabrobot.hamilton.star.driver.features.x_arm import XArm, XArmConfiguration
from pylabrobot.hamilton.star.driver.firmware_update import _FirmwareDownloader, _FirmwareImage
from pylabrobot.hamilton.star.driver.master import STARDriver
from pylabrobot.io.io import IOBase
from pylabrobot.io.usb import USB

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
          image = _FirmwareImage.from_file(path, VERSION, "P1")
          self.assertEqual(image.records, tuple(valid))
          self.assertEqual(image.version, VERSION)
          self.assertEqual(len(image.sha256), 64)
        else:
          with self.assertRaises(ValueError):
            _FirmwareImage.from_file(path, VERSION, "P1")

  def test_other_module_filename_is_rejected(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "GRUC0_test.ACH"
      path.write_text("\n".join(image_records()))
      with self.assertRaisesRegex(ValueError, "GRUPXS"):
        _FirmwareImage.from_file(path, VERSION, "P1")


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
    self.download_date = "2009-05-28"

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
    if command == "AO":
      assert target and self.programmed > 0 and not self.download
      if self.fault == "date_ack":
        return prefix
      self.download_date = cmd.split("ao")[1]
      return prefix + ("er00/00" if module == "C0" else "er00")
    if command == "RO":
      date = "2009-05-28" if self.fault == "date_readback" else self.download_date
      return prefix + ("er00/00" if module == "C0" else "") + "ao" + date
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
      return prefix + ("qg0" if self.fault == "pipette_type" else "qg1")
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
      if self.fault == "repeated_calibration":
        return (prefix + "jd2025-09-30js1") * 2
      if self.fault == "concatenated_calibration":
        return prefix + "jd2025-09-30js1X0QTid123"
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
    self.driver.configuration = DeviceConfiguration(pip_type_1000ul=True)
    self.pipettes = Pipettes(self.driver)
    self.pipettes.configuration.resolve_channels(12)
    patcher = patch("pylabrobot.hamilton.star.driver.firmware_update.asyncio", wraps=asyncio)
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
    self.assertEqual(
      [cmd[2:4] for cmd in changes], ["AP", "DE", "DP", "DP", "DP", "DP", "AP", "AO"]
    )
    self.assertEqual([cmd[12:] for cmd in changes if cmd[2:4] == "DP"], self.records)
    self.assertEqual(result.version, VERSION)
    self.assertEqual(result.records_programmed, len(self.records))
    self.assertEqual(result.cycle_counts_before, result.cycle_counts_after)
    self.assertEqual(result.download_date, datetime.date.today())
    self.assertEqual(self.instrument.download_date, result.download_date.isoformat())
    self.assertEqual(self.pipettes.configuration.channels[10].firmware_version, VERSION)
    self.assertEqual([call.args[0] for call in self.delays.await_args_list], [15, 18])

  async def test_invalid_input_sends_nothing(self):
    for channel in (-1, 12):
      with self.assertRaises(ValueError):
        await self.update(channel)
    self.path.write_text("\n".join(self.records[:-1]))
    with self.assertRaises(ValueError):
      await self.update()
    self.assertEqual(self.instrument.commands, [])

  async def test_preflight_errors_send_no_mutations(self):
    for fault in (
      "status",
      "eeprom",
      "hardware",
      "pipette_type",
      "empty_calibration",
      "repeated_calibration",
      "concatenated_calibration",
    ):
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

  async def test_master_channel_type_disagreement_prevents_update(self):
    """The channel and master must agree before the downloader is entered."""
    self.driver.configuration = DeviceConfiguration(pip_type_1000ul=False)
    with self.assertRaisesRegex(ValueError, "P1.*master"):
      await self.update()
    self.assertEqual(self.mutations(), [])

  async def test_update_reads_channel_type_instead_of_trusting_discovery(self):
    """A stale cached 1000 uL type cannot authorize a different channel for flashing."""
    self.pipettes.configuration.channels[0].pip_type_1000ul = True
    self.instrument.fault = "pipette_type"
    with self.assertRaises(ValueError):
      await self.update()
    self.assertTrue(any(cmd.startswith("P1QG") for cmd in self.instrument.commands))
    self.assertEqual(self.mutations(), [])

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
        self.assertFalse(any(cmd[2:4] == "AO" for cmd in self.mutations()))

  async def test_download_date_failure_prevents_success(self):
    """The date must be acknowledged and read back before the update succeeds."""
    for fault in ("date_ack", "date_readback"):
      with self.subTest(fault=fault):
        self.instrument = FirmwareInstrument(fault=fault)
        self.driver._replies.send = AsyncMock(side_effect=self.instrument.send)  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
          await self.update()
        self.assertIsNone(self.pipettes.configuration.channels[0].firmware_version)
        self.assertEqual(sum(cmd[2:4] == "AO" for cmd in self.mutations()), 1)

  async def test_counter_changes_are_reported_without_rewriting_them(self):
    self.instrument.fault = "counters"
    with self.assertLogs("pylabrobot.hamilton.star.driver.features.pipettes", level="WARNING"):
      result = await self.update()
    self.assertEqual(result.cycle_counts_before.tip_pickups, 6426)
    self.assertEqual(result.cycle_counts_after.tip_pickups, 6357)
    self.assertFalse(any(cmd[2:4] == "AV" for cmd in self.instrument.commands))
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


# Synthetic versions exercise each controller generation without including vendor firmware.
COMPONENT_IMAGES = (
  ("X0", "GRUX0S", "1.4S 2020-01-01"),
  ("X0", "GRUX0S", "5.1S 2020-01-01 (X0 XE167)"),
  ("R0", "GRUR0S", "3.1S 2020-01-01"),
  ("R0", "GRUR0S", "4.1S 2020-01-01"),
  ("I0", "GRUALS", "3.4S e 2020-01-01"),
  ("H0", "GRUH0S", "2.3S 2020-01-01"),
  ("H0", "GRUH0S", "5.0S e 2020-01-01 (H0 XE167)"),
  ("D0", "GRUD0S", "1.4S b 2020-01-01"),
)


class ComponentFirmwareInstrument(FirmwareInstrument):
  """Standard component variants sharing the downloader but differing in application reads."""

  def __init__(self, module: str, version: str, fault: Optional[str] = None):
    """Configure a component's version and documented head variant."""
    super().__init__(module, fault)
    self.version = version
    self.previous_version = version.replace("2020", "2019")
    self.head_type = 2 if module == "H0" and version.startswith("5.") else 0

  async def send(self, cmd: str, id_: Optional[int], **kwargs) -> Optional[str]:
    """Supply component-specific reads; all writes pass through the shared fake downloader."""
    module, command = cmd[:2], cmd[2:4]
    if module != self.target or command not in ("RF", "RV", "QG", "QU", "QX", "RA"):
      return await super().send(cmd, id_, **kwargs)
    self.commands.append(cmd)
    prefix = module + command + f"id{id_:04}"
    restarted = self.programmed > 0 and not self.download
    if command == "RF":
      if self.fault == "malformed_version":
        return prefix + "rf" + self.previous_version + prefix + "rf4."
      return prefix + "rf" + (self.version if restarted else self.previous_version)
    if command == "RV":
      assert module != "X0" or self.version.startswith("5."), "Legacy X0 has no RV command"
      body = "na0000000001" if restarted and self.fault == "counters" else "na0000000010"
      if module in ("H0", "D0"):
        body += "nb0000000020nc0000000030nd0000000040"
      elif module == "X0":
        body += "nb0000000020nc0000000030nf0000000040"
      return prefix + body
    if command == "QG":
      return prefix + f"qg{self.head_type}"
    if command == "QU":
      return prefix + ("au1 0 0 0" if restarted and self.fault == "configuration" else "au0 0 0 0")
    if command == "RA":
      assert module == "I0" and cmd.endswith("raau")
      return prefix + ("au1 1 0 0" if restarted and self.fault == "configuration" else "au0 1 0 0")
    assert module == "I0", "QX has different meanings on other components"
    return prefix + ("bx02" if restarted and self.fault == "init_track" else "bx01")


class TestComponentFirmwareUpdate(unittest.IsolatedAsyncioTestCase):
  """Exercise every public component API through the command assembler and verification."""

  async def asyncSetUp(self):
    """Create a driver with both X configurations and skip only reboot delays."""
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.driver = STARDriver(io=Mock(spec=IOBase))
    self.driver._connected = True
    self.driver.configuration = DeviceConfiguration(
      left_arm=XArmConfiguration(), right_arm=XArmConfiguration()
    )
    self.features: dict[str, Union[XArm, iSWAP, Autoload, Head96, Head384]] = {
      "X0": XArm(self.driver),
      "R0": iSWAP(self.driver),
      "I0": Autoload(self.driver),
      "H0": Head96(self.driver),
      "D0": Head384(self.driver),
    }
    patcher = patch("pylabrobot.hamilton.star.driver.firmware_update.asyncio", wraps=asyncio)
    patched = patcher.start()
    self.addCleanup(patcher.stop)
    patched.sleep = AsyncMock()

  def prepare(self, module: str, family: str, version: str, fault: Optional[str] = None) -> Path:
    """Create a synthetic image and configure its matching responder."""
    self.instrument = ComponentFirmwareInstrument(module, version, fault)
    self.driver._replies.send = AsyncMock(side_effect=self.instrument.send)  # type: ignore[method-assign]
    path = Path(self.directory.name) / (family + "_test.ACH")
    path.write_text("\n".join(image_records(version)))
    return path

  def mutations(self) -> list[str]:
    """Return writes so failure tests can prove that programming stopped at the boundary."""
    return [cmd for cmd in self.instrument.commands if cmd[2:4] in ("AP", "DE", "DP", "AV", "AO")]

  async def test_all_component_generations(self):
    for module, family, version in COMPONENT_IMAGES:
      with self.subTest(module=module, version=version):
        path = self.prepare(module, family, version)
        feature = self.features[module]
        result = await feature.update_firmware(path, expected_version=version)
        self.assertEqual(result.module, module)
        self.assertEqual(result.version, version)
        self.assertEqual(result.cycle_counts_before, result.cycle_counts_after)
        self.assertEqual(feature.configuration.firmware_version, version)
        changes = self.mutations()
        self.assertTrue(all(cmd.startswith(module) for cmd in changes))
        self.assertEqual(
          [cmd[2:4] for cmd in changes], ["AP", "DE", "DP", "DP", "DP", "DP", "AP", "AO"]
        )
        self.assertEqual(result.download_date, datetime.date.today())
        if module == "X0":
          assert self.driver.configuration and self.driver.configuration.right_arm
          self.assertEqual(self.driver.configuration.right_arm.firmware_version, version)
          if version.startswith("1."):
            self.assertIsNone(result.cycle_counts_before)
            self.assertFalse(any(cmd[2:4] == "RV" for cmd in self.instrument.commands))
          else:
            self.assertEqual(result.cycle_counts_before, dict(na=10, nb=20, nc=30, nf=40))
        elif module == "R0":
          self.assertEqual(result.cycle_counts_before, {"gripping_cycles": 10})
        elif module == "I0":
          self.assertEqual(result.cycle_counts_before, {"barcode_scans": 10})
        else:
          self.assertEqual(
            result.cycle_counts_before,
            PipetteCycleCounts(tip_pickups=10, tip_discards=20, aspirations=30, dispenses=40),
          )
        if module != "I0":
          self.assertFalse(any(cmd[2:4] == "QX" for cmd in self.instrument.commands))

  async def test_legacy_x_calibration_uses_standard_adjustment_query(self):
    """Read X calibration with RK and reject missing or changed calibration."""
    module, family, version = COMPONENT_IMAGES[0]
    path = self.prepare(module, family, version)
    await self.features[module].update_firmware(path, expected_version=version)
    self.assertEqual(sum(cmd[2:4] == "RK" for cmd in self.instrument.commands), 2)
    self.assertFalse(any(cmd[2:4] == "RA" for cmd in self.instrument.commands))
    path = self.prepare(module, family, version, "empty_calibration")
    with self.assertRaisesRegex(ValueError, "Empty X0 RK reply"):
      await self.features[module].update_firmware(path, expected_version=version)
    self.assertEqual(self.mutations(), [])
    path = self.prepare(module, family, version, "calibration")
    with self.assertRaisesRegex(RuntimeError, "calibration"):
      await self.features[module].update_firmware(path, expected_version=version)

  async def test_wrong_family_and_unsupported_targets_send_nothing(self):
    for module, family, version in COMPONENT_IMAGES:
      with self.subTest(module=module):
        path = self.prepare(module, "GRUPXS", version)
        with self.assertRaisesRegex(ValueError, family):
          await self.features[module].update_firmware(path, expected_version=version)
        self.assertEqual(self.instrument.commands, [])
    for module in ("PX", "WX", "H1", "P0"):
      with self.subTest(module=module), self.assertRaisesRegex(ValueError, "Unsupported"):
        _FirmwareDownloader(self.driver, module)

  async def test_incompatible_generation_cannot_be_overridden(self):
    for module, family, version in (COMPONENT_IMAGES[0], COMPONENT_IMAGES[2], COMPONENT_IMAGES[5]):
      with self.subTest(module=module):
        path = self.prepare(module, family, version)
        self.instrument.previous_version = "5.0S 2020-01-01"
        with self.assertRaisesRegex(ValueError, "controller generations"):
          await self.features[module].update_firmware(
            path, expected_version=version, allow_downgrade=True
          )
        self.assertEqual(self.mutations(), [])

  async def test_preflight_errors_prevent_mode_switch(self):
    module, family, version = COMPONENT_IMAGES[6]
    for fault in (
      "malformed_version",
      "status",
      "eeprom",
      "empty_calibration",
      "head_type",
      "repeated_calibration",
      "concatenated_calibration",
    ):
      with self.subTest(fault=fault):
        path = self.prepare(module, family, version, fault)
        if fault == "head_type":
          self.instrument.head_type = 0  # Legacy head with an XE167 image.
        with self.assertRaises((ValueError, RuntimeError)):
          await self.features[module].update_firmware(path, expected_version=version)
        self.assertEqual(self.mutations(), [])

  async def test_downgrade_requires_opt_in(self):
    module, family, version = COMPONENT_IMAGES[4]
    path = self.prepare(module, family, version)
    self.instrument.previous_version = version.replace("2020", "2021")
    with self.assertRaisesRegex(ValueError, "allow_downgrade"):
      await self.features[module].update_firmware(path, expected_version=version)
    self.assertEqual(self.mutations(), [])
    result = await self.features[module].update_firmware(
      path, expected_version=version, allow_downgrade=True
    )
    self.assertEqual(result.version, version)

  async def test_heads_never_send_application_motion_opcodes(self):
    for module, family, version in COMPONENT_IMAGES[5:]:
      with self.subTest(module=module):
        path = self.prepare(module, family, version, "download")
        with self.assertRaisesRegex(RuntimeError, "download mode"):
          await self.features[module].update_firmware(path, expected_version=version)
        self.assertEqual(self.mutations(), [module + "AP"])

  async def test_uncertain_write_leaves_no_cached_version_or_restart(self):
    for module, family, version in COMPONENT_IMAGES:
      for fault in ("erase", "timeout", "error", "cancel", "missing_ack", "wrong_ack"):
        with self.subTest(module=module, fault=fault):
          path = self.prepare(module, family, version, fault)
          feature = self.features[module]
          feature.configuration.firmware_version = "old"
          with self.assertRaises(
            (RuntimeError, TimeoutError, STARFirmwareError, asyncio.CancelledError)
          ):
            await feature.update_firmware(path, expected_version=version)
          self.assertEqual(sum(cmd[2:4] == "AP" for cmd in self.mutations()), 1)
          self.assertIsNone(feature.configuration.firmware_version)
          self.assertIsNone(self.driver._firmware_update_task)
          if module == "X0":
            assert self.driver.configuration and self.driver.configuration.right_arm
            self.assertIsNone(self.driver.configuration.right_arm.firmware_version)

  async def test_persistent_state_and_master_are_verified(self):
    for module, family, version in COMPONENT_IMAGES[4:]:
      for fault in ("calibration", "configuration", "eeprom_after", "master"):
        with self.subTest(module=module, fault=fault):
          path = self.prepare(module, family, version, fault)
          with self.assertRaises(RuntimeError):
            await self.features[module].update_firmware(path, expected_version=version)
          self.assertIsNone(self.features[module].configuration.firmware_version)
    module, family, version = COMPONENT_IMAGES[4]
    path = self.prepare(module, family, version, "init_track")
    with self.assertRaisesRegex(RuntimeError, "configuration changed"):
      await self.features[module].update_firmware(path, expected_version=version)

  async def test_counter_changes_are_reported_without_writes(self):
    for module, family, version in COMPONENT_IMAGES[2:]:
      with self.subTest(module=module):
        path = self.prepare(module, family, version, "counters")
        with self.assertLogs("pylabrobot.hamilton.star.driver.firmware_update", level="WARNING"):
          result = await self.features[module].update_firmware(path, expected_version=version)
        self.assertNotEqual(result.cycle_counts_before, result.cycle_counts_after)
        self.assertFalse(any(cmd[2:4] == "AV" for cmd in self.instrument.commands))

  async def test_component_update_owns_device_lock(self):
    module, family, version = COMPONENT_IMAGES[4]
    path = self.prepare(module, family, version, "pause")
    async with self.driver._lock.subsystem("X0"):
      task = asyncio.create_task(
        self.features[module].update_firmware(path, expected_version=version)
      )
      await asyncio.sleep(0)
      self.assertEqual(self.instrument.commands, [])
    await asyncio.wait_for(self.instrument.program_started.wait(), timeout=2)
    try:
      with self.assertRaisesRegex(RuntimeError, "firmware update"):
        await self.driver.send_command("H0", "DP")
    finally:
      self.instrument.continue_programming.set()
      await task


MASTER_VERSION = "7.5S 00 2017_02_02 (GRU C0 PLE)"
MASTER_FILENAME = "GRUC0S75_0_2017_02_02.ACH"


def master_image_records(date: str = "2017_02_02", controller: str = "(GRU C0 PLE)") -> list[str]:
  """Build synthetic C0 records with separate date and controller metadata strings."""
  payload = b"\x00" + controller.encode() + b"\x00" + date.encode() + b"\x00synthetic firmware"
  return (
    [ach_record(4, b"\x00\x00")]
    + [ach_record(0, payload[i : i + 32], address=i) for i in range(0, len(payload), 32)]
    + [ach_record(1, address=65535)]
  )


class MasterFirmwareInstrument(FirmwareInstrument):
  """A master whose USB handle becomes invalid on each mode switch."""

  def __init__(self, target: str, fault: Optional[str] = None):
    """Start with a connected application and the requested protocol fault."""
    super().__init__(target, fault)
    self.connected = True

  async def send(self, cmd: str, id_: Optional[int], **kwargs) -> Optional[str]:
    """Model C0's stored settings, board identity, and download acknowledgements."""
    if not self.connected:
      raise OSError("C0 USB handle disconnected during restart")
    module, command = cmd[:2], cmd[2:4]
    restarted = self.programmed > 0 and not self.download
    if module != "C0" or command in ("AP", "DE", "DP", "RQ", "AO", "RO"):
      reply = await super().send(cmd, id_, **kwargs)
      if module == "C0" and command == "AP":
        self.connected = False
      if (
        module == "C0" and command in ("DE", "DP") and reply is not None and reply.endswith("er00")
      ):
        reply += "/01" if self.fault == "trace_ack" else "/00"
      if module != "C0" and command == "RF" and restarted and self.fault == "peer":
        assert reply is not None
        return reply + " changed"
      return reply
    self.commands.append(cmd)
    assert id_ is not None
    prefix = "C0" + command + f"id{id_:04}"
    if command == "RF":
      return (
        prefix
        + "er00/00rf"
        + (
          MASTER_VERSION
          if restarted and self.fault != "version"
          else "7.5S 00 2016_02_02 (GRU C0 PLE)"
        )
      )
    if command == "QV":
      invalid = self.fault == "eeprom_high_bit" or (restarted and self.fault == "eeprom_after")
      return prefix + ("qv8000000000000000" if invalid else "qv0000000000000000")
    fields = {
      "QB": "qb2" if self.fault == "board" else "qb1",
      "RM": "kb0Bkp01 C00000 X00000 P10000",
      "QM": "ka010003xt54xa54"
      + ("xw00000" if restarted and self.fault == "configuration" else "xw13400"),
      "RI": "si2009-05-28snA083",
      "QT": "qt186882/04/3210 173000/00/0000",
      "RK": "xkC0 X0 I0 W1 W2 T1 T2 R0 P1",
      "RJ": "tq1",
      "RS": "ts2026-04-01 12:01",
    }
    if command not in fields:
      raise AssertionError(f"Unexpected master command: {cmd}")
    if command == "RM" and self.fault == "nodes":
      return prefix + "er00/00kb0Bkp01"
    return prefix + "er00/00" + fields[command]


class TestMasterFirmwareUpdate(unittest.IsolatedAsyncioTestCase):
  """Exercise master-only compatibility, acknowledgements, settings, and date handling."""

  async def asyncSetUp(self) -> None:
    """Use a synthetic master image and the real command assembler, with reboot delays skipped."""
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.path = Path(self.directory.name) / MASTER_FILENAME
    self.records = master_image_records()
    self.path.write_text("\n".join(self.records))
    self.io = Mock(spec=USB)
    self.io.reconnect = AsyncMock(side_effect=self.reconnect)
    self.driver = STARDriver(io=self.io)
    self.driver._connected = True
    self.driver.configuration = DeviceConfiguration()
    self.instrument = MasterFirmwareInstrument("C0")
    self.driver._replies.start = Mock()  # type: ignore[method-assign]
    self.driver._replies.stop = Mock()  # type: ignore[method-assign]
    self.driver._replies.send = AsyncMock(side_effect=self.instrument.send)  # type: ignore[method-assign]
    patcher = patch("pylabrobot.hamilton.star.driver.firmware_update.asyncio", wraps=asyncio)
    patched = patcher.start()
    self.addCleanup(patcher.stop)
    patched.sleep = AsyncMock()

  async def reconnect(self) -> None:
    """Make the replacement USB handle usable after C0's mode switch."""
    self.assertFalse(self.instrument.connected)
    self.instrument.connected = True

  async def update(self):
    """Run the public master API with its complete expected version."""
    return await self.driver.update_firmware(self.path, expected_version=MASTER_VERSION)

  def mutations(self) -> list[str]:
    """Select writes capable of changing firmware or stored state."""
    return [cmd for cmd in self.instrument.commands if cmd[2:4] in ("AP", "DE", "DP", "AO", "AV")]

  async def test_master_programs_verifies_peers_and_records_date(self):
    result = await self.update()
    self.assertEqual(result.version, MASTER_VERSION)
    self.assertEqual(result.records_programmed, len(self.records))
    self.assertEqual(
      [cmd[2:4] for cmd in self.mutations()], ["AP", "DE", "DP", "DP", "DP", "DP", "AP", "AO"]
    )
    self.assertTrue(all(cmd.startswith("C0") for cmd in self.mutations()))
    self.assertEqual(sum(cmd.startswith("P1RF") for cmd in self.instrument.commands), 2)
    self.assertEqual(sum(cmd.startswith("X0RF") for cmd in self.instrument.commands), 2)
    self.assertEqual(result.download_date, datetime.date.today())
    self.assertIsNone(result.cycle_counts_before)
    self.assertIsNone(result.cycle_counts_after)
    self.assertEqual(self.driver.firmware["master"], MASTER_VERSION)
    self.assertFalse(any(cmd.startswith("C0RV") for cmd in self.instrument.commands))
    self.assertEqual(self.io.reconnect.await_count, 2)

  async def test_failed_reconnect_does_not_erase_or_retry_mode_switch(self):
    """A missing replacement device leaves the original application untouched."""
    self.io.reconnect.side_effect = RuntimeError("USB device not found")
    with self.assertRaisesRegex(RuntimeError, "USB device not found"):
      await self.update()
    self.assertEqual(self.mutations(), ["C0AP"])

  async def test_master_rejects_wrong_image_metadata_before_io(self):
    for date, controller in (("2018_02_02", "(GRU C0 PLE)"), ("2017_02_02", "(OTHER C0)")):
      with self.subTest(date=date, controller=controller):
        self.path.write_text("\n".join(master_image_records(date, controller)))
        with self.assertRaisesRegex(ValueError, "build date or controller"):
          await self.update()
        self.assertEqual(self.instrument.commands, [])
    self.path.write_text("\n".join(self.records))
    wrong_name = self.path.with_name("GRUC0S75_14_2017_09_26.ACH")
    wrong_name.write_text(self.path.read_text())
    with self.assertRaisesRegex(ValueError, "original versioned filename"):
      await self.driver.update_firmware(wrong_name, expected_version=MASTER_VERSION)
    self.assertEqual(self.instrument.commands, [])

  async def test_master_rejects_lpc_and_installer_formats_before_io(self):
    self.path.write_text("f0lBUgAAAAAACPA5")
    with self.assertRaises(ValueError):
      await self.update()
    with self.assertRaises(ValueError):
      await self.driver.update_firmware(
        self.path, expected_version="8.5S 00 2017_02_02 (GRU C0 PLE)"
      )
    self.assertEqual(self.instrument.commands, [])

  async def test_master_preflight_checks_board_entire_eeprom_and_node_list(self):
    for fault in ("board", "eeprom_high_bit", "nodes", "status"):
      with self.subTest(fault=fault):
        self.instrument = MasterFirmwareInstrument("C0", fault)
        self.driver._replies.send = AsyncMock(side_effect=self.instrument.send)  # type: ignore[method-assign]
        with self.assertRaises((ValueError, RuntimeError)):
          await self.update()
        self.assertEqual(self.mutations(), [])

  async def test_master_uncertain_write_does_not_restart_or_date(self):
    for fault in ("timeout", "cancel", "missing_ack", "wrong_ack", "trace_ack"):
      with self.subTest(fault=fault):
        self.instrument = MasterFirmwareInstrument("C0", fault)
        self.driver._replies.send = AsyncMock(side_effect=self.instrument.send)  # type: ignore[method-assign]
        with self.assertRaises((RuntimeError, TimeoutError, asyncio.CancelledError)):
          await self.update()
        self.assertEqual(sum(cmd[2:4] == "AP" for cmd in self.mutations()), 1)
        self.assertFalse(any(cmd[2:4] == "AO" for cmd in self.mutations()))
        self.assertNotIn("master", self.driver.firmware)

  async def test_master_verification_precedes_date_write(self):
    for fault in ("version", "configuration", "peer", "eeprom_after"):
      with self.subTest(fault=fault):
        self.instrument = MasterFirmwareInstrument("C0", fault)
        self.driver._replies.send = AsyncMock(side_effect=self.instrument.send)  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
          await self.update()
        self.assertEqual(sum(cmd[2:4] == "AP" for cmd in self.mutations()), 2)
        self.assertFalse(any(cmd[2:4] == "AO" for cmd in self.mutations()))
        self.assertNotIn("master", self.driver.firmware)
