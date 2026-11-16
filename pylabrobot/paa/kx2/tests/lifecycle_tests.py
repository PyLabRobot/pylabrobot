"""Connection and operation restoration through a stateful CANopen transport double."""

from __future__ import annotations

import struct
from typing import Dict, List, Optional, Sequence, Tuple
from unittest.mock import AsyncMock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.canopen import CANopen, FrameCallback
from pylabrobot.paa.kx2 import KX2, Axis
from pylabrobot.paa.kx2.config import AxisConfig, KX2Config, ServoGripperConfig
from pylabrobot.paa.kx2.kinematics import CartesianPose
from pylabrobot.paa.kx2.kx2 import _BIWaiter, _Registers
from pylabrobot.paa.kx2.protocol import CanError, JointMoveDirection
from pylabrobot.resources import Coordinate, Rotation
from pylabrobot.testing import AnyioTestBase


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
        (0x6041, 0, 2),
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

  def _close(self) -> None:
    self.is_open = False
    self.listeners.clear()

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    *,
    node_ids: Optional[Sequence[int]] = None,
  ) -> None:
    """Open without changing any drive state."""
    del node_ids
    self.is_open = True
    stack.callback(self._close)

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
    self,
    cob_id: int,
    data: bytes,
    *,
    remote: bool = False,
    dlc: Optional[int] = None,
  ) -> None:
    """Reply to binary-interpreter requests with typed register values."""
    del remote, dlc
    self.frames.append((cob_id, data))
    if 0x201 <= cob_id <= 0x204 and len(data) == 2:
      node = cob_id - 0x200
      cw = int.from_bytes(data, "little")
      if cw == 0x0F:
        self.values[node, "MO", 0] = 1
      elif cw == 0x06:
        self.values[node, "MO", 0] = 0
      return
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


class TestRestoration(AnyioTestBase):
  """Exercise production lifespan entry, teardown and configuration scopes."""

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
    async with arm:
      assert io.registers[1, 0x6060, 0] == b"\x00"
      assert arm._has_session_pdo(1, 3, transmit=True)
      assert arm._has_session_pdo(1, 3, transmit=False)
      await arm._ipm_select_mode(True)
      await arm._ipm_select_mode(False)
    for key, value in before.items():
      node, index, sub = key
      if index in (0x1A02, 0x1A03, 0x1602) and sub:
        continue
      if index in (0x6060, 0x6061) and node in arm.motion_node_ids:
        assert io.registers[key] == b"\x01"
      else:
        assert io.registers[key] == value, key

  async def test_disabled_gold_drives_are_stationary(self) -> None:
    """Gold MS=3 permits lifespan entry without enabling the motors."""
    arm, io = driver()
    for node in io.states:
      io.values[node, "MS", 0] = 3
    async with arm:
      await arm._stop_axes([1])
    assert all(io.values[node, "MO", 0] == 0 for node in io.states)

  async def test_interpreter_serializes_different_commands_on_the_same_drive(self) -> None:
    """Input polling cannot overwrite a pending motion-status reply on the same node."""
    arm, io = driver()
    async with arm:
      started, release = anyio.Event(), anyio.Event()
      sensor_scheduled = anyio.Event()
      original_write = io.write
      commands: List[Tuple[int, bytes]] = []

      async def delayed_write(
        cob_id: int,
        data: bytes,
        *,
        remote: bool = False,
        dlc: Optional[int] = None,
      ) -> None:
        """Hold one drive's status reply while other requests contend for its channel."""
        commands.append((cob_id, data[:2]))
        if cob_id == 0x302 and data[:2] == b"MS":
          started.set()
          await release.wait()
        await original_write(cob_id, data, remote=remote, dlc=dlc)

      io.write = delayed_write  # type: ignore[method-assign]

      async def run_status() -> None:
        await arm._query_int(2, "MS", 0)

      async def run_sensor() -> None:
        sensor_scheduled.set()
        await arm._query_int(2, "IB", 20)

      with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
          tg.start_soon(run_status)
          await started.wait()
          tg.start_soon(run_sensor)
          await sensor_scheduled.wait()
          await arm._query_int(1, "PX", 0)
          await anyio.sleep(0.01)
          assert (0x302, b"IB") not in commands
          assert (0x301, b"PX") in commands
          release.set()
        assert (0x302, b"IB") in commands

  async def test_binary_interpreter_error_is_not_a_value(self) -> None:
    """A rejected command carries an error flag, even for a floating-point request."""
    arm, _ = driver()
    waiter = _BIWaiter()
    arm._pending_bi[1, "AC", 0] = waiter
    arm._dispatch_bi_response(1, b"AC\x00\xc0\x15\x00\x00\x00")
    with pytest.raises(CanError, match=r"Node 1 rejected AC\[0\]: Elmo error 21"):
      await waiter.wait()

  async def test_lifecycle_restores_nondefault_registers_and_nmt(self) -> None:
    """Every changed register, PDO map and node state returns byte-for-byte."""
    arm, io = driver()
    before, states = dict(io.registers), dict(io.states)
    async with arm:
      assert arm.setup_finished
      assert io.registers != before
      assert not any(cmd[:2] in (b"MO", b"BG") and not cmd[3] & 0x40 for _, cmd in io.frames)
      for node in (1, 3):
        assert io.events.index(("subscribe", 0x80 + node)) < io.events.index(("nmt", node))
    assert io.registers == before
    assert io.states == states
    assert not io.connected

  async def test_partial_setup_failure_restores_and_disconnects(self) -> None:
    """A write that reaches a drive before failing still gets restored."""
    for failing_write in (1, 23, 110):
      arm, io = driver()
      before, states = dict(io.registers), dict(io.states)
      io.fail_at = failing_write
      with pytest.raises(CanError, match="injected"):
        async with arm:
          pass
      assert io.registers == before
      assert io.states == states
      assert not io.connected

  async def test_acknowledged_but_ignored_restore_is_reported(self) -> None:
    """An SDO acknowledgement does not prove the requested setting was restored."""
    arm, io = driver()
    write = io.download
    with pytest.raises(CanError, match="did not restore 0x6060"):
      async with arm:

        async def ignore_mode_restore(node_id: int, index: int, subindex: int, data: bytes) -> None:
          """Model a drive acknowledging a mode value it cannot select."""
          if index == 0x6060 and data == b"\x03":
            return
          await write(node_id, index, subindex, data)

        io.download = ignore_mode_restore  # type: ignore[method-assign]
        io.registers[1, 0x6060, 0] = b"\x03"
        with pytest.raises(CanError, match="did not restore 0x6060"):
          async with arm._preserve_settings(sdo=[(1, 0x6060, 0), (1, 0x6081, 0)]):
            await io.download(1, 0x6060, 0, b"\x01")
            await io.download(1, 0x6081, 0, b"\x04\x00\x00\x00")
        assert io.registers[1, 0x6081, 0] == b"\x03\x00\x00\x00"
        assert arm._faulted
    assert not io.connected

  async def test_interpolation_buffer_access_enabled_after_clear_and_mode_selection(self) -> None:
    """A cleared but disabled buffer discards the preload and underflows on start."""
    arm, io = driver()
    async with arm:
      writes = AsyncMock(wraps=io.download)
      io.download = writes  # type: ignore[method-assign]
      await arm._ipm_select_mode(True)
      for node in arm.motion_node_ids:
        sequence = [
          (call.args[1], call.args[2], call.args[3])
          for call in writes.call_args_list
          if call.args[0] == node and call.args[1] in (0x6060, 0x60C4)
        ]
        assert sequence == [
          (0x6060, 0, b"\x01"),
          (0x60C4, 6, b"\x00"),
          (0x6060, 0, b"\x07"),
          (0x60C4, 6, b"\x01"),
        ]
      await arm._ipm_select_mode(False)

  async def test_interpolation_leaves_nonparticipating_drives_untouched(self) -> None:
    """A Z-only stream cannot enable or select interpolation on the wrist."""
    arm, io = driver()
    async with arm:
      before = dict(io.registers)
      io.frames.clear()
      await arm._ipm_set_time_interval(8, [Axis.Z])
      await arm._ipm_select_mode(True, [Axis.Z])
      await arm._ipm_select_mode(False)
      for key, data in before.items():
        if key[0] != Axis.Z:
          assert io.registers[key] == data, key
      assert [cob for cob, _ in io.frames if 0x201 <= cob <= 0x204] == [0x202]

  async def test_cancelled_scope_restores_current_interlock_and_settings(self) -> None:
    """Restore exact current limits after cancellation without violating CL <= PL."""
    arm, io = driver()
    async with arm:
      io.values[6, "CL", 1], io.values[6, "PL", 1] = 0.4, 0.8
      entered = anyio.Event()
      cancel_scope = anyio.CancelScope()

      async def operation() -> None:
        """Cancel while temporary settings are active."""
        with cancel_scope:
          async with arm._preserve_settings(floats=[(6, "PL", 1), (6, "CL", 1)]):
            await arm._set_servo_gripper_force_limit(100)
            entered.set()
            await anyio.sleep_forever()

      with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
          tg.start_soon(operation)
          await entered.wait()
          cancel_scope.cancel()
      assert cancel_scope.cancelled_caught
      assert io.values[6, "CL", 1] == pytest.approx(0.4)
      assert io.values[6, "PL", 1] == pytest.approx(0.8)
      current_writes = [
        (frame[:2], struct.unpack("<f", frame[4:])[0])
        for _, frame in io.frames
        if len(frame) == 8 and frame[:2] in (b"PL", b"CL") and not frame[3] & 0x40
      ]
      assert [command for command, _ in current_writes[-3:]] == [b"PL", b"CL", b"PL"]
      assert current_writes[-3][1] >= 1.2

  async def test_stop_closes_even_when_active_motion_cleanup_fails(self) -> None:
    """Do not strand transports when joining a failed motion raises."""
    arm, io = driver()
    entered = anyio.Event()
    finished = anyio.Event()
    cancelled_in_motion = False

    async def motion() -> None:
      """Fail during cancellation cleanup."""
      nonlocal cancelled_in_motion
      try:
        with pytest.raises(CanError, match="motion cleanup failed"):
          async with arm._motion_guard():
            entered.set()
            try:
              await anyio.sleep(0.3)
            except anyio.get_cancelled_exc_class():
              cancelled_in_motion = True
              raise
            finally:
              if cancelled_in_motion:
                raise CanError("motion cleanup failed")
      finally:
        finished.set()

    with anyio.fail_after(5):
      async with anyio.create_task_group() as tg:
        with pytest.raises(CanError, match="motion cleanup failed"):
          async with arm:
            tg.start_soon(motion)
            await entered.wait()
        assert cancelled_in_motion
        assert finished.is_set()
        assert not io.connected

  async def test_fatal_emcy_cancels_active_motion_and_disables_axes(self) -> None:
    """A fatal EMCY frame cancels an in-flight guarded motion and disables positioning axes."""
    arm, io = driver()
    async with arm:
      for node in arm.motion_node_ids:
        io.values[node, "MO", 0] = 1
        io.values[node, "MS", 0] = 0
      begun = anyio.Event()

      async def begin(plan: object) -> None:
        del plan
        begun.set()

      async def wait_done(node_ids: List[int], timeout: float) -> None:
        del node_ids, timeout
        await anyio.sleep(0.3)

      arm._start_joint_plan = begin  # type: ignore[method-assign]
      arm._wait_for_moves_done = wait_done  # type: ignore[method-assign]

      async def run_move() -> None:
        with pytest.raises(CanError, match="interrupted by shutdown or halt"):
          await arm.move_to_joint_state({Axis.Z: 60.0})

      with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
          tg.start_soon(run_move)
          await begun.wait()
          emcy_payload = struct.pack("<HBBHH", 0x5441, 0, 0, 0, 0)
          for cb in list(io.listeners[0x81]):
            cb(0x81, emcy_payload, 0.0)
      assert (0x302, b"ST\x00\x00") in io.frames
      for node in arm.motion_node_ids:
        assert io.values[node, "MO", 0] == 0

  async def test_concurrent_version_reads_do_not_change_configuration(self) -> None:
    """Version queries use the standard read-only software-version object."""
    arm, io = driver()
    async with arm:
      io.registers[1, 0x100A, 0] = b"Elmo firmware\x00"
      before = dict(io.registers)
      versions: List[str] = []

      async def read_version() -> None:
        versions.append(await arm.request_drive_version(1))

      async with anyio.create_task_group() as tg:
        tg.start_soon(read_version)
        tg.start_soon(read_version)
      assert versions == ["Elmo firmware", "Elmo firmware"]
      assert io.registers == before


class TestFreedriveScope(AnyioTestBase):
  """Verify operation-scope ownership, conflicts, and restoration for ``KX2.freedrive``."""

  async def test_freedrive_scope_disables_and_restores_positioning_axes(self) -> None:
    """Scoped freedrive disables requested axes and restores prior enable states on exit."""
    arm, io = driver()
    io.values[1, "MO", 0] = 1
    io.values[2, "MO", 0] = 1
    async with arm:
      arm.parking_position = {Axis.Z: 50.0}
      frames_before: int = 0
      async with arm.freedrive([Axis.SHOULDER, Axis.Z]):
        assert io.values[1, "MO", 0] == 0
        assert io.values[2, "MO", 0] == 0
        frames_before = len(io.frames)
        with pytest.raises(RuntimeError, match="freedrive"):
          await arm.start_freedrive_mode()
        with pytest.raises(RuntimeError, match="freedrive"):
          await arm.stop_freedrive_mode()
        with pytest.raises(RuntimeError, match="freedrive"):
          async with arm.freedrive():
            pass
        with pytest.raises(RuntimeError, match="freedrive"):
          await arm.move_to_location(Coordinate(10, 10, 10), 0)
        with pytest.raises(RuntimeError, match="freedrive"):
          await arm.move_to_joint_state({Axis.Z: 20})
        with pytest.raises(RuntimeError, match="freedrive"):
          await arm.park()
        with pytest.raises(RuntimeError, match="freedrive"):
          await arm.find_z_with_proximity_sensor(100, 80)
        with pytest.raises(RuntimeError, match="freedrive"):
          await arm.find_with_proximity_sensor(Coordinate(0, 0, 0), Coordinate(1, 0, 0))
        with pytest.raises(RuntimeError, match="freedrive"):
          await arm.move_parametric(
            lambda t: CartesianPose(Coordinate(0, 0, t), Rotation(z=0)),
            duration=0.1,
          )
        with pytest.raises(RuntimeError, match="freedrive"):
          await arm.move_through_waypoints(
            [
              CartesianPose(Coordinate(0, 0, 0), Rotation(z=0)),
              CartesianPose(Coordinate(1, 0, 0), Rotation(z=0)),
            ],
            speed=10,
            acceleration=20,
          )
        # Rejected positioning calls must fail before issuing any CAN frames.
        assert len(io.frames) == frames_before

        # Queries and independent gripper moves remain permitted during freedrive.
        state = await arm.request_joint_state()
        assert Axis.Z in state
        io.values[6, "PX", 0] = 30000
        io.values[6, "MO", 0] = 1
        await arm.gripper.move_to_jaw_position(30, force_sensing=False)

      assert io.values[1, "MO", 0] == 1
      assert io.values[2, "MO", 0] == 1

  async def test_freedrive_scope_rejects_durable_freedrive_or_inactive_lifespan(self) -> None:
    """Scope refuses to adopt durable freedrive state and requires an active lifespan."""
    arm, io = driver()
    with pytest.raises(RuntimeError, match="active KX2 lifespan"):
      async with arm.freedrive():
        pass
    io.values[2, "MO", 0] = 1
    async with arm:
      await arm.start_freedrive_mode([Axis.Z])
      with pytest.raises(RuntimeError, match="already active"):
        async with arm.freedrive([Axis.Z]):
          pass
      await arm.stop_freedrive_mode()
      assert io.values[2, "MO", 0] == 1

  async def test_elmo_error_reply_does_not_poison_register_or_fault_session(self) -> None:
    """An explicit Elmo error frame resolves the waiter cleanly without invalidating rollback."""
    arm, io = driver()
    original_write = io.write

    async def reject_one_write(
      cob_id: int,
      data: bytes,
      *,
      remote: bool = False,
      dlc: Optional[int] = None,
    ) -> None:
      if cob_id == 0x302 and data[:2] == b"SP" and not (data[3] & 0x40):
        err_reply = data[:3] + bytes([data[3] | 0x40]) + struct.pack("<i", 57)
        for callback in io.listeners.get(0x282, []):
          callback(0x282, err_reply, 0.0)
        return
      await original_write(cob_id, data, remote=remote, dlc=dlc)

    io.write = reject_one_write  # type: ignore[method-assign]
    async with arm:
      io.values[2, "SP", 0] = 1234
      with pytest.raises(CanError, match="rejected"):
        await arm._write(2, "SP", 0, 9999)
      assert not arm._faulted
      assert (2, "SP", 0) not in arm._invalid_bi_keys
      assert await arm._query_int(2, "SP", 0) == 1234

  async def test_restore_bus_session_does_not_swallow_cancellation_and_keeps_emcy_active(
    self,
  ) -> None:
    """EMCY motor auto-disable remains live during _restore_bus_session and cancellation propagates."""
    arm, io = driver()
    io.values[1, "MO", 0] = 1
    io.values[2, "MO", 0] = 1
    original_write = io.write
    emcy_fired = False

    async def fault_during_restore(
      cob_id: int,
      data: bytes,
      *,
      remote: bool = False,
      dlc: Optional[int] = None,
    ) -> None:
      nonlocal emcy_fired
      await original_write(cob_id, data, remote=remote, dlc=dlc)
      if (
        arm._stopping
        and not emcy_fired
        and cob_id == 0x301
        and data[:2] == b"MO"
        and not (data[3] & 0x40)
      ):
        emcy_fired = True
        for cb in io.listeners.get(0x82, []):
          cb(0x82, struct.pack("<HBBHH", 0x5441, 0, 0, 0, 0), 0.0)

    io.write = fault_during_restore  # type: ignore[method-assign]
    with pytest.raises(CanError, match="while faulted"):
      async with arm:
        await arm._motor_enable(1, False, use_ds402=False)
        await arm._motor_enable(2, False, use_ds402=False)
    assert emcy_fired
    # Axis 1 was re-enabled first during restoration, then disabled again by _emcy_tg!
    assert io.values[1, "MO", 0] == 0

  async def test_reset_session_state_clears_stale_gripper_hold_settings_and_freedrive_retries_all(
    self,
  ) -> None:
    """Failed hold-limit restore cannot leak into Session 2; freedrive restore tries all axes."""
    arm, io = driver()
    with pytest.raises(CanError):
      async with arm:
        hold = _Registers()
        hold.floats[(6, "CL", 1)] = 0.25
        # Missing PL key causes _restore_registers to record KeyError -> CanError on exit,
        # leaving _hold_settings set until _reset_session_state clears it!
        arm.gripper._hold_settings = hold
        original_restore = arm.gripper._restore_current_limits

        async def fail_restore() -> None:
          raise CanError("simulated hold restore failure")

        arm.gripper._restore_current_limits = fail_restore  # type: ignore[method-assign]
    assert arm.gripper._hold_settings is None
    arm.gripper._restore_current_limits = original_restore  # type: ignore[method-assign]

    io.values[1, "MO", 0] = 1
    io.values[2, "MO", 0] = 1
    async with arm:
      await arm.start_freedrive_mode([Axis.SHOULDER, Axis.Z])
      original_enable = arm._motor_enable

      async def fail_shoulder(node_id: int, state: bool, *, use_ds402: bool = False) -> None:
        if node_id == Axis.SHOULDER and state:
          raise CanError("shoulder enable failed")
        await original_enable(node_id, state, use_ds402=use_ds402)

      arm._motor_enable = fail_shoulder  # type: ignore[method-assign,assignment]
      with pytest.raises(CanError, match="shoulder enable failed"):
        await arm.stop_freedrive_mode()
      # Axis.Z was still restored even though Axis.SHOULDER failed first!
      assert io.values[2, "MO", 0] == 1
      arm._motor_enable = original_enable  # type: ignore[method-assign]
      await arm.stop_freedrive_mode()
      assert io.values[1, "MO", 0] == 1
