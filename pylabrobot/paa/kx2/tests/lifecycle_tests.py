"""Connection and operation restoration through a stateful CANopen transport double."""

import asyncio
import struct
import unittest
from typing import Dict, List, Sequence, Tuple
from unittest.mock import AsyncMock

from pylabrobot.io.canopen import CANopen, FrameCallback
from pylabrobot.paa.kx2 import KX2, Axis
from pylabrobot.paa.kx2.config import AxisConfig, KX2Config, ServoGripperConfig
from pylabrobot.paa.kx2.protocol import CanError, JointMoveDirection


def configuration() -> KX2Config:
  """Use explicit synthetic calibration, unrelated to a real arm's limits."""
  return KX2Config(
    wrist_offset=10,
    elbow_offset=20,
    elbow_zero_offset=5,
    axes={
      axis: AxisConfig(
        1000, 200, -200, False, True, 100, 200, JointMoveDirection.Normal, {}, {}, {}
      )
      for axis in Axis
      if axis != Axis.RAIL
    },
    base_to_gripper_clearance_z=0,
    base_to_gripper_clearance_arm=0,
    robot_on_rail=False,
    servo_gripper=ServoGripperConfig(0, 1000, 2000, 100, 500, 100, 50, 500, 1000, 5000, 1.2, 2.4),
  )


class MemoryCAN(CANopen):
  """Simulate register persistence, NMT, BI replies and a one-shot failing write."""

  def __init__(self) -> None:
    """Seed non-default registers so restoring assumed defaults cannot pass."""
    super().__init__("virtual")
    self.is_open = False
    self.states = {node: 127 if node % 2 else 5 for node in (1, 2, 3, 4, 6)}
    self.registers: Dict[Tuple[int, int, int], bytes] = {}
    self.values: Dict[Tuple[int, str, int], float] = {}
    self.listeners: Dict[int, List[FrameCallback]] = {}
    self.frames: List[Tuple[int, bytes]] = []
    self.events: List[Tuple[str, int]] = []
    self.write_count = 0
    self.fail_at = -1
    for node in self.states:
      self.values.update({(node, "MS", 0): 0, (node, "MO", 0): 0, (node, "SR", 0): 0xC000})
      for transmit, numbers in ((True, (1, 3, 4)), (False, (1, 3))):
        for number in numbers:
          comm = (0x1800 if transmit else 0x1400) + number - 1
          mapping = (0x1A00 if transmit else 0x1600) + number - 1
          for sub, value in {
            1: bytes([node, number, 0, 0xC0]),
            2: b"\xfe",
            3: b"\x09\x00",
            5: b"\x33\x00",
          }.items():
            self.registers[node, comm, sub] = value
          self.registers[node, mapping, 0] = b"\x01"
          self.registers[node, mapping, 1] = b"\x20\x00\x64\x60"
          self.registers[node, mapping, 2] = b"\x20\x00\x6c\x60"
          self.registers[node, 0x2F20, number] = b"\x01\x00\x00\x00"
      for index, sub, size in (
        (0x6040, 0, 2),
        (0x60C0, 0, 2),
        (0x60C4, 2, 4),
        (0x60C4, 3, 1),
        (0x60C4, 5, 1),
        (0x60C2, 1, 1),
        (0x60C2, 2, 1),
        (0x605D, 0, 2),
        (0x6060, 0, 1),
        (0x6061, 0, 1),
        (0x60F2, 0, 2),
        (0x6081, 0, 4),
        (0x6082, 0, 4),
        (0x6083, 0, 4),
        (0x6084, 0, 4),
      ):
        self.registers[node, index, sub] = (3).to_bytes(size, "little")

  @property
  def connected(self) -> bool:
    """Whether the simulated adapter is open."""
    return self.is_open

  async def setup(self, node_ids: Sequence[int]) -> None:
    """Open without changing any drive state."""
    self.is_open = True

  async def stop(self) -> None:
    """Close the simulated adapter."""
    self.is_open = False

  def subscribe(self, cob_id: int, callback: FrameCallback) -> None:
    """Record subscription order and retain reply consumers."""
    self.events.append(("subscribe", cob_id))
    self.listeners.setdefault(cob_id, []).append(callback)

  async def request_nmt_state(self, node_id: int) -> int:
    """Return the simulated node state."""
    return self.states[node_id]

  async def set_nmt_state(self, node_id: int, state: int) -> None:
    """Record transitions without broadcasting a reset."""
    self.events.append(("nmt", node_id))
    self.states[node_id] = state

  async def upload(self, node_id: int, index: int, subindex: int) -> bytes:
    """Read an initialized object; unexpected objects fail the test."""
    return self.registers[node_id, index, subindex]

  async def download(self, node_id: int, index: int, subindex: int, data: bytes) -> None:
    """Persist each write and optionally fail after a partial operation."""
    self.registers[node_id, index, subindex] = bytes(data)
    if index == 0x6060:
      self.registers[node_id, 0x6061, subindex] = bytes(data)
    self.write_count += 1
    if self.write_count == self.fail_at:
      raise CanError("injected write failure")

  async def write(
    self, cob_id: int, data: bytes, *, remote: bool = False, dlc: int | None = None
  ) -> None:
    """Reply to binary-interpreter requests with typed register values."""
    self.frames.append((cob_id, data))
    if not 0x301 <= cob_id <= 0x306:
      return
    node = cob_id - 0x300
    command = data[:2].decode("ascii")
    index = data[2] | ((data[3] & 0x3F) << 8)
    key = (node, command, index)
    floating = bool(data[3] & 0x80)
    if not data[3] & 0x40 and len(data) == 8:
      self.values[key] = struct.unpack("<f" if floating else "<i", data[4:])[0]
    value = self.values.get(key, 0)
    reply = (
      data[:3]
      + bytes([data[3] & ~0x40])
      + struct.pack("<f" if floating else "<i", value if floating else int(value))
    )
    for callback in self.listeners.get(0x280 + node, []):
      callback(0x280 + node, reply, 0.0)


def driver() -> Tuple[KX2, MemoryCAN]:
  """Construct a KX2 with simulated transport and explicit calibration."""
  arm, io = KX2(), MemoryCAN()
  arm.io = io
  arm._read_config = AsyncMock(return_value=configuration())  # type: ignore[method-assign]
  return arm, io


class RestorationTests(unittest.IsolatedAsyncioTestCase):
  """Exercise production setup, teardown and configuration scopes."""

  async def test_empty_pdo_slots_support_streaming_and_restore_active_configuration(self) -> None:
    """Unused descriptors may stay initialized; mapping counts and communication restore."""
    arm, io = driver()
    for node in io.states:
      for mapping in (0x1A02, 0x1A03, 0x1602):
        io.registers[node, mapping, 0] = b"\x00"
        for sub in (1, 2):
          io.registers[node, mapping, sub] = bytes(4)
      io.registers[node, 0x6060, 0] = b"\x00"
    before = dict(io.registers)
    await arm.setup()
    self.assertEqual(io.registers[1, 0x6060, 0], b"\x00")
    self.assertTrue(arm._has_session_pdo(1, 3, transmit=True))
    self.assertTrue(arm._has_session_pdo(1, 3, transmit=False))
    await arm._ipm_select_mode(True)
    await arm._ipm_select_mode(False)
    await arm.stop()
    for key, value in before.items():
      node, index, sub = key
      if index in (0x1A02, 0x1A03, 0x1602) and sub:
        continue
      if index in (0x6060, 0x6061) and node in arm.motion_node_ids:
        self.assertEqual(io.registers[key], b"\x01")
      else:
        self.assertEqual(io.registers[key], value, key)

  async def test_disabled_gold_drives_are_stationary(self) -> None:
    """Gold MS=3 permits setup without enabling the motors."""
    arm, io = driver()
    for node in io.states:
      io.values[node, "MS", 0] = 3
    await arm.setup()
    await arm._stop_axes([1])
    await arm.stop()
    self.assertTrue(all(io.values[node, "MO", 0] == 0 for node in io.states))

  async def test_interpreter_serializes_different_commands_on_the_same_drive(self) -> None:
    """Input polling cannot overwrite a pending motion-status reply on the same node."""
    arm, io = driver()
    await arm.setup()
    started, release = asyncio.Event(), asyncio.Event()
    original_write = io.write
    commands = []

    async def delayed_write(
      cob_id: int, data: bytes, *, remote: bool = False, dlc: int | None = None
    ) -> None:
      """Hold one drive's status reply while other requests contend for its channel."""
      commands.append((cob_id, data[:2]))
      if cob_id == 0x302 and data[:2] == b"MS":
        started.set()
        await release.wait()
      await original_write(cob_id, data, remote=remote, dlc=dlc)

    io.write = delayed_write  # type: ignore[method-assign]
    status = asyncio.create_task(arm._query_int(2, "MS", 0))
    await started.wait()
    sensor = asyncio.create_task(arm._query_int(2, "IB", 20))
    await arm._query_int(1, "PX", 0)
    await asyncio.sleep(0.01)
    self.assertNotIn((0x302, b"IB"), commands)
    self.assertIn((0x301, b"PX"), commands)
    release.set()
    await asyncio.gather(status, sensor)
    self.assertIn((0x302, b"IB"), commands)
    await arm.stop()

  async def test_binary_interpreter_error_is_not_a_value(self) -> None:
    """A rejected command carries an error flag, even for a floating-point request."""
    arm, _ = driver()
    future = asyncio.get_running_loop().create_future()
    arm._pending_bi[1, "AC", 0] = future
    arm._dispatch_bi_response(1, b"AC\x00\xc0\x15\x00\x00\x00")
    with self.assertRaisesRegex(CanError, r"Node 1 rejected AC\[0\]: Elmo error 21"):
      await future

  async def test_lifecycle_restores_nondefault_registers_and_nmt(self) -> None:
    """Every changed register, PDO map and node state returns byte-for-byte."""
    arm, io = driver()
    before, states = dict(io.registers), dict(io.states)
    await arm.setup()
    self.assertTrue(arm.setup_finished)
    self.assertNotEqual(io.registers, before)
    self.assertFalse(any(cmd[:2] in (b"MO", b"BG") and not cmd[3] & 0x40 for _, cmd in io.frames))
    for node in (1, 3):
      self.assertLess(io.events.index(("subscribe", 0x80 + node)), io.events.index(("nmt", node)))
    await arm.stop()
    self.assertEqual(io.registers, before)
    self.assertEqual(io.states, states)
    self.assertFalse(io.connected)
    await arm.stop()

  async def test_partial_setup_failure_restores_and_disconnects(self) -> None:
    """A write that reaches a drive before failing still gets restored."""
    for failing_write in (1, 23, 110):
      arm, io = driver()
      before, states = dict(io.registers), dict(io.states)
      io.fail_at = failing_write
      with self.assertRaisesRegex(CanError, "injected"):
        await arm.setup()
      self.assertEqual(io.registers, before)
      self.assertEqual(io.states, states)
      self.assertFalse(io.connected)

  async def test_acknowledged_but_ignored_restore_is_reported(self) -> None:
    """An SDO acknowledgement does not prove the requested setting was restored."""
    arm, io = driver()
    await arm.setup()
    write = io.download

    async def ignore_mode_restore(node, index, sub, data):
      """Model a drive acknowledging a mode value it cannot select."""
      if index == 0x6060 and data == b"\x03":
        return
      await write(node, index, sub, data)

    io.download = ignore_mode_restore  # type: ignore[method-assign]
    io.registers[1, 0x6060, 0] = b"\x03"
    with self.assertRaisesRegex(CanError, "did not restore 0x6060"):
      async with arm._preserve_settings(sdo=[(1, 0x6060, 0), (1, 0x6081, 0)]):
        await io.download(1, 0x6060, 0, b"\x01")
        await io.download(1, 0x6081, 0, b"\x04\x00\x00\x00")
    self.assertEqual(io.registers[1, 0x6081, 0], b"\x03\x00\x00\x00")
    self.assertTrue(arm._faulted)
    with self.assertRaisesRegex(CanError, "did not restore 0x6060"):
      await arm.stop()
    self.assertFalse(io.connected)

  async def test_interpolation_buffer_access_enabled_after_clear_and_mode_selection(self) -> None:
    """A cleared but disabled buffer discards the preload and underflows on start."""
    arm, io = driver()
    await arm.setup()
    writes = AsyncMock(wraps=io.download)
    io.download = writes  # type: ignore[method-assign]
    await arm._ipm_select_mode(True)
    for node in arm.motion_node_ids:
      sequence = [
        (call.args[1], call.args[2], call.args[3])
        for call in writes.call_args_list
        if call.args[0] == node and call.args[1] in (0x6060, 0x60C4)
      ]
      self.assertEqual(
        sequence,
        [
          (0x6060, 0, b"\x01"),
          (0x60C4, 6, b"\x00"),
          (0x6060, 0, b"\x07"),
          (0x60C4, 6, b"\x01"),
        ],
      )
    await arm._ipm_select_mode(False)
    await arm.stop()

  async def test_interpolation_leaves_nonparticipating_drives_untouched(self) -> None:
    """A Z-only stream cannot enable or select interpolation on the wrist."""
    arm, io = driver()
    await arm.setup()
    before = dict(io.registers)
    io.frames.clear()
    await arm._ipm_set_time_interval(8, [Axis.Z])
    await arm._ipm_select_mode(True, [Axis.Z])
    await arm._ipm_select_mode(False)
    for key, data in before.items():
      if key[0] != Axis.Z:
        self.assertEqual(io.registers[key], data, key)
    self.assertEqual([cob for cob, _ in io.frames if 0x201 <= cob <= 0x204], [0x202])
    await arm.stop()

  async def test_cancelled_scope_restores_current_interlock_and_settings(self) -> None:
    """Restore exact current limits after cancellation without violating CL <= PL."""
    arm, io = driver()
    await arm.setup()
    io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
    entered = asyncio.Event()

    async def operation() -> None:
      """Cancel while temporary settings are active."""
      async with arm._preserve_settings(floats=[(6, "PL", 1), (6, "CL", 1)]):
        await arm._set_servo_gripper_force_limit(100)
        entered.set()
        await asyncio.Future()

    task = asyncio.create_task(operation())
    await entered.wait()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertAlmostEqual(io.values[6, "CL", 1], 0.4)
    self.assertAlmostEqual(io.values[6, "PL", 1], 0.8)
    current_writes = [
      (frame[:2], struct.unpack("<f", frame[4:])[0])
      for _, frame in io.frames
      if len(frame) == 8 and frame[:2] in (b"PL", b"CL") and not frame[3] & 0x40
    ]
    self.assertEqual([command for command, _ in current_writes[-3:]], [b"PL", b"CL", b"PL"])
    self.assertGreaterEqual(current_writes[-3][1], 1.2)
    await arm.stop()

  async def test_stop_closes_even_when_active_motion_cleanup_fails(self) -> None:
    """Do not strand transports when joining a failed motion raises."""
    arm, io = driver()
    await arm.setup()
    entered = asyncio.Event()

    async def motion() -> None:
      """Fail during cancellation cleanup."""
      async with arm._motion_guard():
        entered.set()
        try:
          await asyncio.Future()
        finally:
          raise CanError("motion cleanup failed")

    task = asyncio.create_task(motion())
    await entered.wait()
    with self.assertRaisesRegex(CanError, "cleanup failed"):
      await arm.stop()
    self.assertTrue(task.done())
    self.assertFalse(io.connected)

  async def test_concurrent_version_reads_do_not_change_configuration(self) -> None:
    """Version queries use the standard read-only software-version object."""
    arm, io = driver()
    await arm.setup()
    io.registers[1, 0x100A, 0] = b"Elmo firmware\x00"
    before = dict(io.registers)
    versions = await asyncio.gather(arm.request_drive_version(1), arm.request_drive_version(1))
    self.assertEqual(versions, ["Elmo firmware", "Elmo firmware"])
    self.assertEqual(io.registers, before)
    await arm.stop()
