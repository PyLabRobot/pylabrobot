import contextlib
import unittest
from typing import Iterator, List
from unittest.mock import AsyncMock, patch

from pylabrobot.azenta.fluidx import (
  CartridgeProfile,
  ExtendedStatus,
  FluidXError,
  FluidXIntelliXcap96,
  get_error_message,
  is_recoverable_error,
)

ACK = "\x06"


class FakeXcapSerial:
  """In-memory serial stand-in for the STX/ETX framed decapper protocol.

  ``script`` is a list of turns, one per ``write``. Each turn is the list of
  frame payloads the device emits in response to that write; every payload is
  queued wrapped in STX (0x02) .. ETX (0x03), exactly as the firmware frames it.
  """

  def __init__(self, script: List[List[str]]) -> None:
    self.port = "FAKE"
    self.written: List[str] = []
    self._script = list(script)
    self._rx = bytearray()

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def reset_input_buffer(self) -> None:
    self._rx.clear()

  def get_read_timeout(self) -> float:
    return 5.0

  def set_read_timeout(self, timeout: float) -> None:
    pass

  @contextlib.contextmanager
  def temporary_timeout(self, timeout: float) -> Iterator[None]:
    yield

  async def write(self, data: bytes) -> None:
    self.written.append(data.decode("ascii").rstrip("\x03"))
    turn = self._script.pop(0) if self._script else []
    for payload in turn:
      self._rx += b"\x02" + payload.encode("ascii") + b"\x03"

  async def read(self, num_bytes: int = 1) -> bytes:
    if not self._rx:
      return b""
    out = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    return out


def status(word: str, echo: str = "a") -> List[str]:
  """A full status reply: ACK, command echo, and the status word."""
  return [ACK, f"{echo}OK", word]


def query(command: str, payload: str) -> List[str]:
  """A full query reply: ACK, command echo, and the data frame."""
  return [ACK, f"{command}OK", payload]


def extended(
  caps_on_pins: bool = False,
  cartridge_installed: bool = False,
  estop_active: bool = False,
) -> List[str]:
  """An extended status reply built from the flags a test cares about."""
  bits = ["0"] * 12
  bits[0] = "1" if caps_on_pins else "0"
  bits[6] = "1" if cartridge_installed else "0"
  bits[10] = "1" if estop_active else "0"
  return query("e", "".join(bits))


class TestIntelliXcap96(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    patcher = patch("pylabrobot.azenta.fluidx.intellixcap96.asyncio.sleep", new_callable=AsyncMock)
    patcher.start()
    self.addCleanup(patcher.stop)

  def _make(self, script: List[List[str]], auto_recover: bool = True) -> FluidXIntelliXcap96:
    device = FluidXIntelliXcap96(port="FAKE", auto_recover=auto_recover)
    device.io = FakeXcapSerial(script)  # type: ignore[assignment]
    return device

  def _written(self, device: FluidXIntelliXcap96) -> List[str]:
    return device.io.written  # type: ignore[attr-defined,no-any-return]

  # === Connection ===

  async def test_setup_ok(self):
    device = self._make([status("StatusOK")])
    await device.setup()
    self.assertEqual(self._written(device), ["a"])

  async def test_setup_busy_reports_engaged_estop(self):
    device = self._make([status("StatusBUSY"), extended(estop_active=True)])
    with self.assertRaises(FluidXError) as ctx:
      await device.setup()
    self.assertIn("E-STOP is engaged", str(ctx.exception))

  async def test_setup_busy_still_warns_when_estop_unreadable(self):
    device = self._make([status("StatusBUSY")])
    with self.assertRaises(FluidXError) as ctx:
      await device.setup()
    self.assertIn("E-STOP", str(ctx.exception))

  async def test_setup_error_reports_error_code(self):
    device = self._make([status("StatusError"), query("8", "137")])
    with self.assertRaises(FluidXError) as ctx:
      await device.setup()
    self.assertEqual(ctx.exception.error_code, 137)
    self.assertIn("Maximum recap attempts exceeded", str(ctx.exception))

  async def test_setup_error_raises_without_error_code(self):
    device = self._make([status("StatusError")])
    with self.assertRaises(FluidXError) as ctx:
      await device.setup()
    self.assertIsNone(ctx.exception.error_code)

  async def test_setup_manual_recovery_raises(self):
    device = self._make([status("StatusMANUAL")])
    with self.assertRaises(FluidXError) as ctx:
      await device.setup()
    self.assertIn("manual recovery", str(ctx.exception))

  async def test_request_status_returns_status_word(self):
    device = self._make([status("StatusOK")])
    self.assertEqual(await device.request_status(), "StatusOK")

  # === Error codes ===

  def test_get_error_message(self):
    self.assertEqual(
      get_error_message(145),
      "Light curtain calibration max retries exceeded.",
    )
    self.assertEqual(get_error_message(999), "Unknown IntelliXcap error code.")

  def test_fluidx_error_from_error_code(self):
    error = FluidXError.from_error_code(145)
    self.assertEqual(error.error_code, 145)
    self.assertEqual(
      str(error),
      "IntelliXcap error 145: Light curtain calibration max retries exceeded.",
    )

  def test_recoverable_errors_are_the_ones_homing_clears(self):
    self.assertTrue(is_recoverable_error(113))
    self.assertTrue(is_recoverable_error(144))
    self.assertFalse(is_recoverable_error(137))
    self.assertTrue(FluidXError.from_error_code(117).recoverable)
    self.assertFalse(FluidXError.from_error_code(137).recoverable)
    self.assertFalse(FluidXError(title="no code").recoverable)

  async def test_request_error_code_returns_latched_code(self):
    device = self._make([query("8", "142")])
    self.assertEqual(await device.request_error_code(), 142)
    self.assertEqual(self._written(device), ["8"])

  async def test_request_error_code_is_none_when_clear(self):
    device = self._make([query("8", "000")])
    self.assertIsNone(await device.request_error_code())

  async def test_request_error_code_raises_on_garbage(self):
    device = self._make([query("8", "nope")])
    with self.assertRaises(FluidXError):
      await device.request_error_code()

  # === Queries ===

  async def test_request_extended_status(self):
    device = self._make([extended(caps_on_pins=True, cartridge_installed=True)])
    result = await device.request_extended_status()
    self.assertTrue(result.caps_on_pins)
    self.assertTrue(result.cartridge_installed)
    self.assertFalse(result.estop_active)
    self.assertFalse(result.time_for_service)

  async def test_caps_on_pins(self):
    device = self._make([extended(caps_on_pins=True)])
    self.assertTrue(await device.caps_on_pins())

  def test_extended_status_rejects_an_unexpected_width(self):
    # The command list names 12 flags but shows an 11-character answer. Padding
    # either end would guess at CAPS_ON_PINS, so the mismatch is surfaced.
    with self.assertRaises(FluidXError) as ctx:
      ExtendedStatus.from_raw("00000100000")
    self.assertIn("11 bits, expected 12", str(ctx.exception))

  def test_extended_status_rejects_a_non_bitmask(self):
    with self.assertRaises(FluidXError):
      ExtendedStatus.from_raw("10201")

  async def test_request_firmware_versions(self):
    device = self._make([query("V", "0044,0014,0003")])
    versions = await device.request_firmware_versions()
    self.assertEqual(versions.unit, "0044")
    self.assertEqual(versions.touchscreen, "0014")
    self.assertEqual(versions.light_curtain, "0003")

  async def test_request_cartridge_info(self):
    device = self._make([query("N", "016,000123,00000000")])
    info = await device.request_cartridge_info()
    self.assertEqual(info.profile, 16)
    self.assertEqual(info.cycle_count, 123)
    self.assertEqual(info.serial, "00000000")

  async def test_request_profile(self):
    device = self._make([query("E", "16032")])
    profile = await device.request_profile()
    self.assertEqual(profile.number, 16)
    self.assertEqual(profile.communication_protocol, 0)
    self.assertEqual(profile.decap_max_retry, 3)
    self.assertEqual(profile.recap_max_retry, 2)

  def test_profile_rejects_a_malformed_reply(self):
    with self.assertRaises(FluidXError):
      CartridgeProfile.from_raw("160")

  # === Tray ===

  async def test_open_tray_moves_then_idle(self):
    device = self._make(
      [
        status("StatusOK"),  # _ensure_ready: not in error
        [ACK, "fOK"],  # f accepted
        status("StatusBUSY"),  # still moving
        status("StatusOK"),  # settled
      ]
    )
    await device.open_tray()
    self.assertEqual(self._written(device), ["a", "f", "a", "a"])

  async def test_open_tray_finishes_on_its_own_done_frame(self):
    device = self._make([status("StatusOK"), [ACK, "fOK"], [ACK, "aOK", "OpenDONE"]])
    await device.open_tray()
    self.assertEqual(self._written(device), ["a", "f", "a"])

  async def test_open_tray_already_open_is_noop(self):
    # CommandIgnore = tray already open = success; no status wait afterwards.
    device = self._make([status("StatusOK"), [ACK, "fOK", "CommandIgnore"]])
    await device.open_tray()
    self.assertEqual(self._written(device), ["a", "f"])

  async def test_close_tray_moves_then_idle(self):
    device = self._make(
      [status("StatusOK"), [ACK, "gOK"], status("StatusBUSY"), status("StatusOK")]
    )
    await device.close_tray()
    self.assertEqual(self._written(device), ["a", "g", "a", "a"])

  async def test_close_tray_preserves_caps_held_state(self):
    device = self._make(
      [status("StatusRECAP"), [ACK, "gOK"], status("StatusBUSY"), status("StatusRECAP")]
    )
    await device.close_tray()
    self.assertEqual(self._written(device), ["a", "g", "a", "a"])

  async def test_step_tray_out_and_in(self):
    device = self._make(
      [
        status("StatusOK"),
        [ACK, "sOK", "sOK"],
        status("StatusOK"),
        [ACK, "SOK", "SOK"],
      ]
    )
    await device.step_tray_out()
    await device.step_tray_in()
    self.assertEqual(self._written(device), ["a", "s", "a", "S"])

  async def test_tray_move_out_of_range_raises(self):
    device = self._make([status("StatusOK"), [ACK, "sOK", "sERROR"], query("8", "148")])
    with self.assertRaises(FluidXError) as ctx:
      await device.step_tray_out()
    self.assertEqual(ctx.exception.error_code, 148)

  async def test_tray_move_times_out_without_a_second_echo(self):
    device = self._make([status("StatusOK"), [ACK, "sOK"]])
    with self.assertRaises(FluidXError) as ctx:
      await device.step_tray_out(timeout=0.01)
    self.assertIn("timed out", str(ctx.exception))

  # === Homing and recovery ===

  async def test_home_moves_then_idle(self):
    device = self._make([[ACK, "ZOK"], status("StatusBUSY"), status("StatusOK")])
    await device.home()
    self.assertEqual(self._written(device), ["Z", "a", "a"])

  async def test_reset_error_recovers_manual_state_by_homing(self):
    device = self._make(
      [
        status("StatusMANUAL"),
        [ACK, "ZOK"],
        status("StatusBUSY"),
        status("StatusOK"),
      ]
    )
    await device.reset_error()
    self.assertEqual(self._written(device), ["a", "Z", "a", "a"])

  async def test_reset_error_recovers_error_state_by_homing(self):
    device = self._make(
      [
        status("StatusError"),
        [ACK, "ZOK"],
        status("StatusBUSY"),
        status("StatusOK"),
      ]
    )
    await device.reset_error()
    self.assertEqual(self._written(device), ["a", "Z", "a", "a"])

  async def test_reset_error_is_noop_when_healthy(self):
    device = self._make([status("StatusOK")])
    await device.reset_error()
    self.assertEqual(self._written(device), ["a"])

  async def test_initialize_keeping_caps_on_pins(self):
    device = self._make([status("StatusMANUAL"), [ACK, "zOK", "zDONE"]])
    await device.initialize_keeping_caps_on_pins()
    self.assertEqual(self._written(device), ["a", "z"])

  async def test_initialize_keeping_caps_on_pins_reports_a_home_failure(self):
    device = self._make([status("StatusMANUAL"), [ACK, "zOK", "HomeERROR"], query("8", "156")])
    with self.assertRaises(FluidXError) as ctx:
      await device.initialize_keeping_caps_on_pins()
    self.assertEqual(ctx.exception.error_code, 156)

  async def test_initialize_keeping_caps_on_pins_requires_manual_mode(self):
    device = self._make([status("StatusOK")])
    with self.assertRaises(FluidXError) as ctx:
      await device.initialize_keeping_caps_on_pins()
    self.assertIn("manual recovery mode", str(ctx.exception))
    self.assertEqual(self._written(device), ["a"])

  async def test_operation_auto_recovers_from_latched_error(self):
    device = self._make(
      [
        status("StatusError"),  # _ensure_ready: latched in error
        [ACK, "ZOK"],  # recovery home accepted
        status("StatusBUSY"),  # homing
        status("StatusOK"),  # homed
        status("StatusOK"),  # _ensure_ready re-check: clear
        [ACK, "fOK"],  # open accepted
        status("StatusBUSY"),
        status("StatusOK"),
      ]
    )
    await device.open_tray()
    self.assertEqual(
      self._written(device),
      ["a", "Z", "a", "a", "a", "f", "a", "a"],
    )

  async def test_latched_error_raises_when_auto_recover_disabled(self):
    device = self._make([status("StatusError"), query("8", "113")], auto_recover=False)
    with self.assertRaises(FluidXError) as ctx:
      await device.open_tray()
    self.assertEqual(ctx.exception.error_code, 113)
    self.assertEqual(self._written(device), ["a", "8"])

  async def test_operation_blocked_during_manual_recovery(self):
    device = self._make([status("StatusMANUAL")])
    with self.assertRaises(FluidXError) as ctx:
      await device.open_tray()
    self.assertIn("manual recovery", str(ctx.exception))
    self.assertEqual(self._written(device), ["a", "8"])

  # === Decap / recap ===

  async def test_decap_success(self):
    device = self._make(
      [
        status("StatusOK"),  # precheck: not recapped, no error
        [ACK, "hOK"],  # h accepted
        status("StatusBUSY"),
        status("StatusRECAP"),  # hardware terminal state: caps held
      ]
    )
    await device.decap()
    self.assertEqual(self._written(device), ["a", "h", "a", "a"])

  async def test_decap_blocked_when_already_decapped(self):
    device = self._make([status("StatusRecap")])
    with self.assertRaises(FluidXError):
      await device.decap()

  async def test_decap_reports_error_during_motion(self):
    device = self._make([status("StatusOK"), [ACK, "hOK"], [ACK, "aOK", "DecapERROR"]])
    with self.assertRaises(FluidXError):
      await device.decap()

  async def test_decap_decodes_error_code_when_firmware_includes_it(self):
    device = self._make([status("StatusOK"), [ACK, "hOK"], [ACK, "aOK", "DecapERROR 145"]])
    with self.assertRaises(FluidXError) as ctx:
      await device.decap()
    self.assertEqual(ctx.exception.error_code, 145)
    self.assertIn("Light curtain calibration max retries exceeded", str(ctx.exception))

  async def test_decap_queries_the_error_code_when_the_reply_omits_it(self):
    device = self._make(
      [
        status("StatusOK"),
        [ACK, "hOK"],
        [ACK, "aOK", "DecapERROR"],
        query("8", "114"),
      ]
    )
    with self.assertRaises(FluidXError) as ctx:
      await device.decap()
    self.assertEqual(ctx.exception.error_code, 114)
    self.assertTrue(ctx.exception.recoverable)
    self.assertIn("Invalid tube height", str(ctx.exception))
    self.assertEqual(self._written(device), ["a", "h", "a", "8"])

  async def test_manual_halt_during_motion_fails_immediately(self):
    device = self._make(
      [status("StatusOK"), [ACK, "hOK"], status("StatusMANUAL"), query("8", "167")]
    )
    with self.assertRaises(FluidXError) as ctx:
      await device.decap()
    self.assertEqual(ctx.exception.error_code, 167)
    self.assertFalse(ctx.exception.recoverable)

  async def test_retry_decap_finishes_with_caps_held(self):
    device = self._make(
      [
        status("StatusRECAP"),
        [ACK, "hOK"],
        status("StatusBUSY"),
        status("StatusRECAP"),
      ]
    )
    await device.retry_decap()
    self.assertEqual(self._written(device), ["a", "Q", "a", "a"])

  async def test_recap_blocked_when_not_decapped(self):
    device = self._make([status("StatusDecap")])
    with self.assertRaises(FluidXError):
      await device.recap()

  async def test_recap_success(self):
    device = self._make(
      [
        status("StatusRECAP"),  # precheck: caps held
        [ACK, "iOK"],  # i accepted
        status("StatusBUSY"),
        status("StatusOK"),
      ]
    )
    await device.recap()
    self.assertEqual(self._written(device), ["a", "i", "a", "a"])

  async def test_waste_blocked_when_no_caps_are_held(self):
    device = self._make([status("StatusOK")])
    with self.assertRaises(FluidXError):
      await device.waste()
    self.assertEqual(self._written(device), ["a"])

  async def test_waste_success(self):
    device = self._make(
      [
        status("StatusRECAP"),  # precheck: caps held
        [ACK, "bOK"],  # b accepted
        status("StatusBUSY"),
        status("StatusOK"),
      ]
    )
    await device.waste()
    self.assertEqual(self._written(device), ["a", "b", "a", "a"])

  # === Standby ===

  async def test_standby_reaches_sleep(self):
    device = self._make([status("StatusOK"), [ACK, "jOK"], status("StatusSLEEP")])
    await device.standby()
    self.assertEqual(self._written(device), ["a", "j", "a"])

  async def test_standby_is_noop_when_asleep(self):
    device = self._make([status("StatusSLEEP")])
    await device.standby()
    self.assertEqual(self._written(device), ["a"])

  async def test_ready_noop_when_awake(self):
    device = self._make([status("StatusOK")])
    await device.ready()
    self.assertEqual(self._written(device), ["a"])

  async def test_ready_wakes_from_sleep(self):
    device = self._make(
      [
        status("StatusSLEEP"),  # request_status: asleep
        [ACK, "kOK"],  # k accepted
        status("StatusBUSY"),  # waking
        status("StatusOK"),  # awake
      ]
    )
    await device.ready()
    self.assertEqual(self._written(device), ["a", "k", "a", "a"])

  # === Cartridge ===

  async def test_eject_cartridge(self):
    device = self._make(
      [
        extended(cartridge_installed=True),
        status("StatusOK"),
        [ACK, "cOK"],
        status("StatusCAREJECT"),
      ]
    )
    await device.eject_cartridge()
    self.assertEqual(self._written(device), ["e", "a", "c", "a"])

  async def test_eject_cartridge_is_noop_without_a_cartridge(self):
    device = self._make([extended(cartridge_installed=False)])
    await device.eject_cartridge()
    self.assertEqual(self._written(device), ["e"])

  async def test_eject_cartridge_blocked_while_caps_are_held(self):
    device = self._make([extended(cartridge_installed=True, caps_on_pins=True)])
    with self.assertRaises(FluidXError):
      await device.eject_cartridge()
    self.assertEqual(self._written(device), ["e"])

  async def test_load_cartridge_returns_the_profile(self):
    device = self._make(
      [extended(cartridge_installed=False), [ACK, "COK", "o16OK"], status("StatusOK")]
    )
    self.assertEqual(await device.load_cartridge(), 16)
    self.assertEqual(self._written(device), ["e", "C", "a"])

  async def test_load_cartridge_is_noop_when_already_loaded(self):
    device = self._make([extended(cartridge_installed=True)])
    self.assertIsNone(await device.load_cartridge())
    self.assertEqual(self._written(device), ["e"])

  async def test_reset_cartridge_counter(self):
    device = self._make([[ACK, "XOK", "CarResetDONE"]])
    await device.reset_cartridge_counter()
    self.assertEqual(self._written(device), ["X"])

  async def test_reset_cartridge_counter_requires_its_answer(self):
    device = self._make([[ACK, "XOK"]])
    with self.assertRaises(FluidXError):
      await device.reset_cartridge_counter()

  async def test_reset_cartridge_counter_is_idempotent(self):
    device = self._make([[ACK, "XOK", "CommandIgnore"]])
    await device.reset_cartridge_counter()
    self.assertEqual(self._written(device), ["X"])

  async def test_load_cartridge_reads_the_profile_from_the_end_of_the_motion(self):
    # onnOK is documented as an end-of-motion answer, not part of the ack.
    device = self._make(
      [
        extended(cartridge_installed=False),
        [ACK, "COK"],
        [ACK, "aOK", "o24OK", "StatusOK"],
      ]
    )
    self.assertEqual(await device.load_cartridge(), 24)

  async def test_load_cartridge_reports_a_missing_profile(self):
    device = self._make(
      [extended(cartridge_installed=False), [ACK, "COK", "ProfileLoadERROR"], query("8", "143")]
    )
    with self.assertRaises(FluidXError) as ctx:
      await device.load_cartridge()
    self.assertEqual(ctx.exception.error_code, 143)

  # === Settings ===

  async def test_set_error_detection_off_is_not_read_as_a_fault(self):
    device = self._make([[ACK, "lOK", "ErrorDetectOFF"]])
    await device.set_error_detection_enabled(False)
    self.assertEqual(self._written(device), ["l"])

  async def test_set_error_detection_on(self):
    device = self._make([[ACK, "LOK", "ErrorDetectON"]])
    await device.set_error_detection_enabled(True)
    self.assertEqual(self._written(device), ["L"])

  async def test_set_error_detection_is_idempotent(self):
    device = self._make([[ACK, "LOK", "CommandIgnore"]])
    await device.set_error_detection_enabled(True)
    self.assertEqual(self._written(device), ["L"])

  async def test_set_dry_run_enabled(self):
    device = self._make([[ACK, "dOK", "DryRunON"]])
    await device.set_dry_run_enabled(True)
    self.assertEqual(self._written(device), ["d"])

  async def test_set_dry_run_disabled(self):
    device = self._make([[ACK, "DOK", "DryRunOFF"]])
    await device.set_dry_run_enabled(False)
    self.assertEqual(self._written(device), ["D"])

  async def test_set_dry_run_is_idempotent(self):
    device = self._make([[ACK, "DOK", "CommandIgnore"]])
    await device.set_dry_run_enabled(False)
    self.assertEqual(self._written(device), ["D"])

  async def test_setting_raises_when_the_device_does_not_confirm(self):
    device = self._make([[ACK, "dOK"]])
    with self.assertRaises(FluidXError):
      await device.set_dry_run_enabled(True)

  async def test_set_safety_door_enabled(self):
    device = self._make([[ACK, "+OK", "DoorONDONE"]])
    await device.set_safety_door_enabled(True)
    self.assertEqual(self._written(device), ["+"])

  async def test_set_safety_door_disabled(self):
    device = self._make([[ACK, "-OK", "DoorOFFDONE"]])
    await device.set_safety_door_enabled(False)
    self.assertEqual(self._written(device), ["-"])

  async def test_set_safety_door_is_idempotent(self):
    device = self._make([[ACK, "+OK", "CommandIgnore"]])
    await device.set_safety_door_enabled(True)
    self.assertEqual(self._written(device), ["+"])

  # === Manual recovery commands ===

  async def test_eject_caps_requires_manual_mode(self):
    device = self._make([status("StatusOK")])
    with self.assertRaises(FluidXError):
      await device.eject_caps()
    self.assertEqual(self._written(device), ["a"])

  async def test_eject_caps(self):
    # Manual recovery pins the status word to StatusMANUAL, so the answer frame
    # is the only completion signal: no status polling here.
    device = self._make([status("StatusMANUAL"), [ACK, "5OK", "EjectDONE"]])
    await device.eject_caps()
    self.assertEqual(self._written(device), ["a", "5"])

  async def test_eject_caps_reports_a_failure(self):
    device = self._make([status("StatusMANUAL"), [ACK, "5OK", "EjectERROR"], query("8", "133")])
    with self.assertRaises(FluidXError) as ctx:
      await device.eject_caps()
    self.assertEqual(ctx.exception.error_code, 133)

  async def test_head_up(self):
    device = self._make([status("StatusMANUAL"), [ACK, "6OK", "HeadDONE"]])
    await device.head_up()
    self.assertEqual(self._written(device), ["a", "6"])

  async def test_open_safety_door(self):
    device = self._make([status("StatusMANUAL"), [ACK, "7OK", "DoorDONE"]])
    await device.open_safety_door()
    self.assertEqual(self._written(device), ["a", "7"])

  async def test_manual_command_times_out_without_an_answer(self):
    device = self._make([status("StatusMANUAL"), [ACK, "6OK"]])
    with self.assertRaises(FluidXError) as ctx:
      await device.head_up(timeout=0.01)
    self.assertIn("timed out", str(ctx.exception))


if __name__ == "__main__":
  unittest.main()
