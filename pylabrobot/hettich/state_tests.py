"""Mocked integration tests for Hettich workflow ownership and recovery."""

import asyncio
from unittest.mock import AsyncMock, patch

from pylabrobot.hettich._errors import HettichCentrifugeError, HettichCommunicationError
from pylabrobot.hettich._state import HettichMachineState
from pylabrobot.hettich.centrifuge_tests import (
  HettichAsyncTestCase,
  enquiry_reply,
  make_device,
  telegram_parameters,
  telegrams,
  writes,
)

ACK_REPLY = b"]\x06"


def identity_replies() -> list[bytes]:
  """Return a valid MIKRO startup identity exchange."""
  return [
    enquiry_reply("00685", 0),
    enquiry_reply("00600", 0x1234),
    enquiry_reply("00537", 0xE800),
    enquiry_reply("00636", 0x0121),
  ]


def standstill_replies() -> list[bytes]:
  """Return an error-free, remotely controlled standstill status."""
  return [enquiry_reply("00634", 0x0162), enquiry_reply("00635", 0xA292)]


def start_replies() -> list[bytes]:
  """Return the complete preflight and START exchange for a finite spin."""
  return [
    enquiry_reply("00614", 30),
    *standstill_replies(),
    enquiry_reply("00528", 0x1800),
    enquiry_reply("00605", 5000),
    ACK_REPLY,
    ACK_REPLY,
    ACK_REPLY,
    ACK_REPLY,
  ]


def recovery_replies(speed: int = 0, hatch: int = 0x1800) -> list[bytes]:
  """Return recovery observations with configurable physical speed and hatch state."""
  return [
    *identity_replies()[:3],
    *standstill_replies(),
    enquiry_reply("00604", speed),
    enquiry_reply("00528", hatch),
  ]


class HettichStateTests(HettichAsyncTestCase):
  """Exercise state transitions through public device workflows."""

  async def test_connection_lifecycle_and_snapshots(self) -> None:
    """Setup and stop are idempotent and leave earlier state snapshots unchanged."""
    device = make_device(identity_replies(), connected=False)
    initial = device.state
    self.assertEqual(initial, HettichMachineState())
    with self.assertRaisesRegex(HettichCentrifugeError, "not connected"):
      await device.open_hatch()
    with self.assertRaisesRegex(HettichCentrifugeError, "not connected"):
      await device.request_speed()
    self.assertEqual(telegrams(device), [])

    async def connect() -> None:
      """Observe the in-progress lifecycle at the transport boundary."""
      self.assertEqual(device.state.connection, "connecting")
      self.assertEqual(device.state.activity, "setting_up")

    with patch.object(device.io, "setup", side_effect=connect):
      await device.setup()
    connected = device.state
    self.assertEqual(connected, HettichMachineState(connection="connected"))
    await device.setup()
    self.assertEqual(len(telegrams(device)), 4)
    with patch.object(device.io, "stop", new_callable=AsyncMock) as close:
      await device.stop()
      await device.stop()
      close.assert_awaited_once()
    self.assertEqual(device.state.connection, "disconnected")
    self.assertEqual(initial.connection, "disconnected")
    self.assertEqual(connected.connection, "connected")

  async def test_preflight_failure_does_not_require_recovery(self) -> None:
    """A rejected request before actuation leaves the device ready for another operation."""
    device = make_device([enquiry_reply("00528", 0xA000)])
    with self.assertRaises(ValueError):
      await device.spin(duration=0, speed=500)
    self.assertEqual(device.state, HettichMachineState(connection="connected"))
    await device.open_hatch()
    self.assertEqual(telegram_parameters(device), [b"00528"])

  async def test_failed_hatch_actuation_blocks_motion_but_allows_status(self) -> None:
    """A command with lost replies leaves a persistent recovery requirement."""
    device = make_device(
      [
        enquiry_reply("00528", 0x1800),
        *standstill_replies(),
        b"",
        b"",
        b"",
        enquiry_reply("00604", 0),
      ]
    )
    with self.assertRaises(HettichCommunicationError):
      await device.open_hatch()
    self.assertTrue(device.state.recovery_required)
    self.assertEqual(device.state.activity, "idle")
    self.assertEqual(await device.request_speed(), 0)
    before = len(telegrams(device))
    for operation in (
      device.open_hatch(),
      device.close_hatch(),
      device.move_to_position(1),
      device.end_positioning(),
      device.select_program(1),
      device.spin(30, 500),
    ):
      with self.assertRaisesRegex(HettichCentrifugeError, "requires recovery"):
        await operation
    self.assertEqual(len(telegrams(device)), before)

  async def test_cancellation_after_hatch_command_requires_recovery(self) -> None:
    """Cancellation after possible hatch movement must not make the device ready."""
    device = make_device([enquiry_reply("00528", 0x1800), *standstill_replies(), ACK_REPLY])
    waiting = asyncio.Event()

    async def wait_for_hatch(desired: str, timeout: float) -> None:
      """Hold the operation after its command has been acknowledged."""
      waiting.set()
      await asyncio.Event().wait()

    with patch.object(device, "_wait_for_hatch", side_effect=wait_for_hatch):
      task = asyncio.create_task(device.open_hatch())
      await asyncio.wait_for(waiting.wait(), 1)
      self.assertEqual(device.state.activity, "opening_hatch")
      task.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await task
    self.assertTrue(device.state.recovery_required)
    self.assertEqual(device.state.activity, "idle")

  async def test_spin_owns_operations_while_status_remains_available(self) -> None:
    """A second spin or lifecycle/motion operation cannot interleave with an active spin."""
    device = make_device([*start_replies(), enquiry_reply("00604", 500), *standstill_replies()])
    waiting = asyncio.Event()

    async def wait_for_speed(speed: int, timeout: float) -> tuple[int, float]:
      """Suspend a spin after its START exchange."""
      waiting.set()
      await asyncio.Event().wait()
      return 0, 0.0

    with patch.object(device, "_wait_for_target_speed", side_effect=wait_for_speed):
      task = asyncio.create_task(device.spin(30, 500, timeout=60))
      await asyncio.wait_for(waiting.wait(), 1)
      self.assertEqual(device.state.activity, "accelerating")
      before = len(telegrams(device))
      for operation in (
        device.spin(30, 500),
        device.open_hatch(),
        device.close_hatch(),
        device.move_to_position(1),
        device.end_positioning(),
        device.select_program(1),
        device.setup(),
        device.stop(),
        device.recover(),
      ):
        with self.assertRaisesRegex(HettichCentrifugeError, "is active"):
          await operation
      self.assertEqual(len(telegrams(device)), before)
      self.assertEqual(await device.request_speed(), 500)
      task.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await task
    self.assertTrue(device.state.recovery_required)

  async def test_stop_request_uses_spin_owner_and_waits_for_standstill(self) -> None:
    """stop_spin must interrupt the owning workflow without deadlock or duplicate STOP."""
    device = make_device(
      [
        *start_replies(),
        enquiry_reply("00634", 0x01E4),
        enquiry_reply("00635", 0xA292),
        ACK_REPLY,
        *standstill_replies(),
        *standstill_replies(),
      ]
    )
    waiting, release = asyncio.Event(), asyncio.Event()
    original_wait = device._wait_for_target_speed

    async def wait_for_speed(speed: int, timeout: float) -> tuple[int, float]:
      """Allow a stop request to arrive before the owner's next polling iteration."""
      waiting.set()
      await release.wait()
      return await original_wait(speed, timeout)

    with patch.object(device, "_wait_for_target_speed", side_effect=wait_for_speed):
      spin = asyncio.create_task(device.spin(30, 500, timeout=60))
      await asyncio.wait_for(waiting.wait(), 1)
      stop = asyncio.create_task(device.stop_spin())
      await asyncio.sleep(0)
      self.assertFalse(stop.done())
      release.set()
      with self.assertRaisesRegex(HettichCentrifugeError, "interrupted by stop_spin"):
        await asyncio.wait_for(spin, 1)
      await asyncio.wait_for(stop, 1)
    self.assertEqual(telegrams(device).count(device._build_select("00521", 1)), 1)
    self.assertEqual(device.state.activity, "idle")
    self.assertTrue(device.state.recovery_required)

  async def test_stop_during_preflight_prevents_start_without_recovery(self) -> None:
    """A stop request before any SELECT prevents actuation and preserves readiness."""
    device = make_device([enquiry_reply("00614", 30), *standstill_replies()])
    waiting, release = asyncio.Event(), asyncio.Event()
    original_write = writes(device).side_effect

    async def write(data: bytes) -> None:
      """Pause a preparation query while the caller requests a stop."""
      await original_write(data)
      if data == device._build_enquiry("00614"):
        waiting.set()
        await release.wait()

    writes(device).side_effect = write
    spin = asyncio.create_task(device.spin(30, 500, timeout=60))
    await asyncio.wait_for(waiting.wait(), 1)
    self.assertEqual(device.state.activity, "preparing_to_spin")
    stop = asyncio.create_task(device.stop_spin())
    await asyncio.sleep(0)
    release.set()
    with self.assertRaisesRegex(HettichCentrifugeError, "interrupted by stop_spin"):
      await asyncio.wait_for(spin, 1)
    await asyncio.wait_for(stop, 1)
    self.assertEqual(device.state, HettichMachineState(connection="connected"))
    self.assertEqual(telegram_parameters(device), [b"00614", b"00634", b"00635"])

  async def test_failed_spin_cleanup_preserves_failure_and_requires_recovery(self) -> None:
    """Failed emergency stopping must not mask the original error or enable more motion."""
    device = make_device([*start_replies(), b"", b"", b"", enquiry_reply("00604", 0)])
    failure = TimeoutError("target speed timed out")
    with patch.object(device, "_wait_for_target_speed", side_effect=failure):
      with self.assertLogs("pylabrobot.hettich.centrifuge", level="ERROR"):
        with self.assertRaises(TimeoutError) as raised:
          await device.spin(30, 500, timeout=60)
    self.assertIs(raised.exception, failure)
    self.assertTrue(device.state.recovery_required)
    self.assertEqual(device.state.activity, "idle")
    self.assertEqual(await device.request_speed(), 0)
    with self.assertRaisesRegex(HettichCentrifugeError, "requires recovery"):
      await device.spin(30, 500)

  async def test_successful_spin_records_each_workflow_phase(self) -> None:
    """State follows confirmed spin phases and returns to idle on success."""
    device = make_device(
      [
        *start_replies(),
        enquiry_reply("00634", 0x01E8),
        enquiry_reply("00635", 0xA292),
        enquiry_reply("00604", 500),
        enquiry_reply("00602", 12),
        ACK_REPLY,
        ACK_REPLY,
        enquiry_reply("00634", 0x01F0),
        enquiry_reply("00635", 0xA292),
        *standstill_replies(),
      ]
    )
    self.schedule_spin_states(device, [30, 40])
    original_write = writes(device).side_effect
    snapshots: list[HettichMachineState] = []

    async def write(data: bytes) -> None:
      """Observe workflow phases at serial boundaries without modifying them."""
      snapshots.append(device.state)
      await original_write(data)

    writes(device).side_effect = write
    await device.spin(30, 500, timeout=60)
    activities = list(dict.fromkeys(snapshot.activity for snapshot in snapshots))
    self.assertEqual(activities, ["preparing_to_spin", "accelerating", "at_speed", "braking"])
    self.assertEqual(device.state, HettichMachineState(connection="connected"))

  async def test_recovery_is_explicit_and_read_only(self) -> None:
    """Closing, reconnecting, and stopping preserve the flag until fresh checks pass."""
    device = make_device([*identity_replies(), *standstill_replies(), *recovery_replies()])
    device._machine.require_recovery()
    await device.stop()
    await device.setup()
    self.assertTrue(device.state.recovery_required)
    await device.stop_spin()
    self.assertTrue(device.state.recovery_required)
    await device.recover()
    self.assertEqual(device.state, HettichMachineState(connection="connected"))
    self.assertTrue(all(frame[-1] == 5 for frame in telegrams(device)))

  async def test_recovery_rejects_moving_or_faulted_hardware(self) -> None:
    """Neither a live speed nor an unstable/faulted hatch can clear recovery."""
    for speed, hatch in (
      (10, 0x1800),
      (0, 0x1801),
      (0, 0x1C00),
      (0, 0x5800),
      (0, 0x1810),
      (0, 0x3800),
    ):
      with self.subTest(speed=speed, hatch=hatch):
        device = make_device(recovery_replies(speed=speed, hatch=hatch))
        device._machine.require_recovery()
        with self.assertRaises(HettichCentrifugeError):
          await device.recover()
        self.assertTrue(device.state.recovery_required)
        self.assertEqual(device.state.activity, "idle")

  async def test_recovery_rejects_identity_changes_and_communication_failures(self) -> None:
    """Unverified identity or missing replies cannot clear uncertainty."""
    for replies in (
      [enquiry_reply("00685", 0), enquiry_reply("00600", 0x1234), enquiry_reply("00537", 0xC901)],
      [b"", b"", b""],
    ):
      with self.subTest(replies=replies):
        device = make_device(replies)
        device._machine.require_recovery()
        with self.assertRaises(HettichCentrifugeError):
          await device.recover()
        self.assertTrue(device.state.recovery_required)

  async def test_failed_recovery_checks_block_motion_even_without_an_existing_flag(self) -> None:
    """A failed explicit check cannot leave an unverified machine ready for motion."""
    device = make_device(recovery_replies(speed=100))
    with self.assertRaisesRegex(HettichCentrifugeError, "zero measured rotor speed"):
      await device.recover()
    with self.assertRaisesRegex(HettichCentrifugeError, "requires recovery"):
      await device.open_hatch()

  async def test_recovery_allows_a_confirmed_stationary_positioning_hold(self) -> None:
    """A held rotor at its target position is a known state suitable for recovery."""
    device = make_device(recovery_replies(hatch=0x2006))
    device._machine.require_recovery()
    await device.recover()
    self.assertFalse(device.state.recovery_required)

  async def test_failed_close_does_not_claim_disconnection_or_clear_recovery(self) -> None:
    """An uncertain transport close requires another successful close before setup."""
    device = make_device([])
    with patch.object(device.io, "stop", side_effect=OSError("close failed")):
      with self.assertRaises(OSError):
        await device.stop()
    self.assertEqual(device.state.connection, "unknown")
    self.assertTrue(device.state.recovery_required)
    with self.assertRaisesRegex(HettichCentrifugeError, "closure is uncertain"):
      await device.setup()
    await device.stop()
    self.assertEqual(device.state.connection, "disconnected")
    self.assertTrue(device.state.recovery_required)
