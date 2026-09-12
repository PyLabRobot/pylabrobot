"""Software-only lifecycle and cancellation checks for Celigo's owned resources."""

import threading

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.revvity.celigo.camera import LumeneraCamera
from pylabrobot.revvity.celigo.celigo import ControllerInfo, ControllerStatus
from pylabrobot.revvity.celigo.config import CeligoHardwareConfig, DigitalIOConfig, IOConfig
from pylabrobot.revvity.celigo.motion import (
  Axis,
  FilterWheel,
  MagnificationChanger,
  MotorController,
  _EZResponse,
)
from pylabrobot.revvity.celigo.tests.helpers import (
  make_celigo,
  make_filter_wheel_config,
  make_galvo_config,
  make_linear_axis_config,
)
from pylabrobot.testing.concurrency import AnyioTestBase


class _Transport(HasLifespan):
  def __init__(self, events):
    super().__init__()
    self.events = events

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs):
    self.events.append("open")
    stack.callback(self.events.append, "close")

  async def set_baudrate(self, value):
    self.events.append("baud")

  async def set_line_property(self, *args):
    self.events.append("line")

  async def set_latency_timer(self, value):
    self.events.append("latency")

  async def usb_purge_rx_buffer(self):
    self.events.append("rx")

  async def usb_purge_tx_buffer(self):
    self.events.append("tx")


class _Camera(HasLifespan):
  def __init__(self, events):
    super().__init__()
    self.events = events
    self.width = 2048
    self.height = 2048

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs):
    self.events.append("camera open")
    stack.callback(self.events.append, "camera close")

  async def set_frame_format(self, width, height, x_offset=None, y_offset=None):
    self.events.append("camera format")
    self.width, self.height = width, height
    return width, height


class _FailingCamera(_Camera):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs):
    self.events.append("camera open")
    stack.callback(self._fail_close)

  def _fail_close(self):
    self.events.append("camera close")
    raise RuntimeError("camera close failed")


class TestCeligoLifespan(AnyioTestBase):
  async def test_cancelled_lamp_power_write_retains_committed_state(self):
    device = make_celigo(
      hardware=CeligoHardwareConfig(
        io=IOConfig(
          analog_ins=[],
          lighting_ios=[],
          digital_ios=[
            DigitalIOConfig(
              config_version=1000,
              io_type="Out",
              bit_index=7,
              invert=False,
              enabled=True,
              io_name="ExcitationLampPower",
            )
          ],
        )
      )
    )
    dispatched = anyio.Event()
    release = anyio.Event()

    async def write(bit_index, high):
      dispatched.set()
      await release.wait()

    device.set_digital_output = write  # type: ignore[method-assign]

    async def operation():
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.set_fluorescence_lamp_power(True)
        except anyio.get_cancelled_exc_class():
          cancellations.append(True)

    scopes: list[anyio.CancelScope] = []
    cancellations: list[bool] = []
    async with anyio.create_task_group() as group:
      group.start_soon(operation)
      await dispatched.wait()
      scopes[0].cancel()
      release.set()
    assert device._fluorescence_on_since is not None
    assert cancellations == [True]

  async def test_cancelled_command_before_dispatch_writes_no_packet(self):
    device = make_celigo()
    writes: list[bytes] = []

    class IO:
      async def write(self, data: bytes) -> int:
        writes.append(data)
        return len(data)

    device.io = IO()  # type: ignore[assignment]
    with anyio.CancelScope() as scope:
      scope.cancel()
      with pytest.raises(anyio.get_cancelled_exc_class()):
        await device.send_command(23)
    assert writes == []

  async def test_partial_entry_unwinds_ftdi_after_safe_outputs(self):
    events: list[str] = []
    device = make_celigo()
    device.io = _Transport(events)  # type: ignore[assignment]

    async def abort():
      events.append("abort")

    async def status():
      return ControllerStatus(0, 0)

    async def identity():
      raise ValueError("identity")

    async def safe():
      events.append("safe")

    device.abort_controller_operation = abort  # type: ignore[method-assign]
    device.request_controller_status = status  # type: ignore[method-assign]
    device.request_controller_info = identity  # type: ignore[method-assign]
    device._initialize_safe_outputs = safe  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="identity"):
      async with device:
        pass
    assert events[:5] == ["open", "baud", "line", "latency", "rx"]
    assert events[-3:] == ["abort", "safe", "close"]

  async def test_camera_closes_before_safe_controller_and_ftdi(self):
    events: list[str] = []
    device = make_celigo()
    device.io = _Transport(events)  # type: ignore[assignment]
    device.camera = _Camera(events)  # type: ignore[assignment]

    async def abort():
      events.append("abort")

    async def status():
      return ControllerStatus(0, 0)

    async def identity():
      return ControllerInfo(1, (1, 3, 0), 256)

    async def safe():
      events.append("safe")

    async def initialize():
      events.append("initialize")

    async def home():
      events.append("home")

    device.abort_controller_operation = abort  # type: ignore[method-assign]
    device.request_controller_status = status  # type: ignore[method-assign]
    device.request_controller_info = identity  # type: ignore[method-assign]
    device._initialize_hardware = initialize  # type: ignore[method-assign]
    device._initialize_safe_outputs = safe  # type: ignore[method-assign]
    device.home_imaging_axes = home  # type: ignore[method-assign]
    async with device:
      assert device.is_active
    assert events[-4:] == ["camera close", "abort", "safe", "close"]
    async with device:
      assert device.is_active
    assert events.count("close") == 2

  async def test_camera_cleanup_failure_still_runs_controller_safety_and_closes_ftdi(self):
    events: list[str] = []
    device = make_celigo()
    device.io = _Transport(events)  # type: ignore[assignment]
    device.camera = _FailingCamera(events)  # type: ignore[assignment]

    async def abort():
      events.append("abort")

    async def status():
      return ControllerStatus(0, 0)

    async def identity():
      return ControllerInfo(1, (1, 3, 0), 256)

    async def safe():
      events.append("safe")

    async def initialize():
      events.append("initialize")

    async def home():
      events.append("home")

    device.abort_controller_operation = abort  # type: ignore[method-assign]
    device.request_controller_status = status  # type: ignore[method-assign]
    device.request_controller_info = identity  # type: ignore[method-assign]
    device._initialize_hardware = initialize  # type: ignore[method-assign]
    device._initialize_safe_outputs = safe  # type: ignore[method-assign]
    device.home_imaging_axes = home  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="camera close failed") as error:
      async with device:
        raise ValueError("body failed")
    assert isinstance(error.value.__context__, ValueError)
    assert events[-4:] == ["camera close", "abort", "safe", "close"]

  async def test_committed_command_drains_reply_before_cancellation(self):
    events = []
    device = make_celigo()
    started = anyio.Event()
    release = anyio.Event()

    class IO:
      async def write(self, data):
        events.append("write")
        return len(data)

      async def read(self, count):
        started.set()
        await release.wait()
        events.append("read")
        return bytes.fromhex("0017000000020000000019d9")

      async def usb_purge_rx_buffer(self):
        events.append("rx")

      async def usb_purge_tx_buffer(self):
        events.append("tx")

    device.io = IO()  # type: ignore[assignment]
    device.reply_timeout = 0.2

    async def command():
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.send_command(23)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")

    scopes: list[anyio.CancelScope] = []
    async with anyio.create_task_group() as tg:
      tg.start_soon(command)
      await started.wait()
      scopes[0].cancel()
      release.set()
    assert events.index("read") < events.index("cancelled")


class TestCameraLifespan(AnyioTestBase):
  async def test_cancelled_property_write_updates_camera_state_after_sdk_commit(self):
    from pylabrobot.revvity.celigo.tests.feature_tests import _FakeLucamLibrary

    camera = LumeneraCamera(library=_FakeLucamLibrary())
    started = threading.Event()
    release = threading.Event()
    async with camera:
      original = camera._set_property_sync

      def set_property(property_id, value):
        started.set()
        release.wait(2)
        return original(property_id, value)

      camera._set_property_sync = set_property  # type: ignore[method-assign]

      async def operation():
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await camera.set_exposure(3.5)
          except anyio.get_cancelled_exc_class():
            cancellations.append(True)

      scopes: list[anyio.CancelScope] = []
      cancellations: list[bool] = []
      async with anyio.create_task_group() as group:
        group.start_soon(operation)
        await anyio.to_thread.run_sync(started.wait)
        scopes[0].cancel()
        release.set()
      assert camera.exposure_ms == 3.5
      assert cancellations == [True]

  async def test_camera_closes_after_native_capture_finishes_on_cancellation(self):
    from pylabrobot.revvity.celigo.tests.feature_tests import _FakeLucamLibrary

    library = _FakeLucamLibrary()
    camera = LumeneraCamera(library=library)
    started = threading.Event()
    release = threading.Event()
    events = []
    original = camera._capture_sync

    def capture(flush_frames):
      started.set()
      release.wait(2)
      events.append("capture done")
      return original(flush_frames)

    camera._capture_sync = capture  # type: ignore[method-assign]
    async with camera:

      async def operation():
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await camera.capture(flush_frames=0)
          except anyio.get_cancelled_exc_class():
            events.append("cancelled")

      scopes: list[anyio.CancelScope] = []
      async with anyio.create_task_group() as group:
        group.start_soon(operation)
        await anyio.to_thread.run_sync(started.wait)
        scopes[0].cancel()
        assert not library.closed
        release.set()
    assert events == ["capture done", "cancelled"]
    assert library.closed


class TestMotionReconciliation(AnyioTestBase):
  async def test_cancelled_magnification_move_publishes_settled_calibration(self):
    from unittest.mock import patch

    device = make_celigo()
    device.config.magnification = 3
    wheel = MagnificationChanger(MotorController(device), make_filter_wheel_config(), device.config)
    moving = anyio.Event()
    release = anyio.Event()
    events: list[str] = []

    async def physical_move(self, logical_position):
      moving.set()
      await release.wait()
      events.append("settled")
      return 5

    async def operation():
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await wheel.move_to(5)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")

    scopes: list[anyio.CancelScope] = []
    with patch.object(FilterWheel, "move_to", physical_move):
      async with anyio.create_task_group() as group:
        group.start_soon(operation)
        await moving.wait()
        scopes[0].cancel()
        release.set()
    assert device.config.magnification == 5
    assert events == ["settled", "cancelled"]

  async def test_cancelled_two_axis_galvo_move_does_not_dispatch_second_axis(self):
    from pylabrobot.revvity.celigo.galvo import GalvoControllerStatus

    events: list[str] = []
    first_sent = anyio.Event()
    release = anyio.Event()
    device = make_celigo()

    async def move(axis, voltage, wait_until_settled=False, timeout=6.0):
      events.append(axis)
      if axis == "x":
        first_sent.set()
        await release.wait()
      return voltage

    async def status():
      events.append("status")
      await release.wait()
      return GalvoControllerStatus(False, False, 0.0, 0.0, 0, 0, 0, 0, False, 0)

    device.galvo.move_single = move  # type: ignore[method-assign]
    device.galvo.request_controller_status = status  # type: ignore[method-assign]
    device.config.hardware.x_galvo = device.config.hardware.y_galvo = make_galvo_config()

    async def operation():
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await device.galvo.move_both(0.0, 0.0)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")

    scopes: list[anyio.CancelScope] = []
    async with anyio.create_task_group() as group:
      group.start_soon(operation)
      await first_sent.wait()
      scopes[0].cancel()
      release.set()
    assert events == ["x", "status", "cancelled"]

  async def test_cancelled_motor_command_waits_for_motion_status(self):
    events: list[str] = []
    sent = anyio.Event()
    release = anyio.Event()
    device = make_celigo()
    axis = Axis(
      MotorController(device),
      "test",
      make_linear_axis_config(max_velocity=10, max_acceleration=10),
    )

    async def send(tokens, execute=True):
      sent.set()
      await release.wait()
      events.append("command reply")
      return _EZResponse(True, 0, "")

    async def wait(timeout=None):
      events.append("settled")
      return 5

    axis.motor.send_command = send  # type: ignore[method-assign]
    axis.motor.wait_until_ready = wait  # type: ignore[method-assign]

    async def move():
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await axis.move_to_ticks(5)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")

    scopes: list[anyio.CancelScope] = []
    async with anyio.create_task_group() as group:
      group.start_soon(move)
      await sent.wait()
      scopes[0].cancel()
      release.set()
    assert events.index("settled") < events.index("cancelled")

  async def test_cancelled_move_waits_for_settled_encoder_before_restoring_current(self):
    events = []
    waiting = anyio.Event()
    release = anyio.Event()
    device = make_celigo()
    axis = Axis(
      MotorController(device),
      "test",
      make_linear_axis_config(
        max_velocity=10,
        max_acceleration=10,
        moving_current_percentage=50,
        holding_current_percentage=25,
      ),
    )

    async def send(tokens, execute=True):
      events.append(tokens)
      return _EZResponse(True, 0, "")

    async def wait(timeout=None):
      waiting.set()
      await release.wait()
      events.append("settled")
      return 5

    axis.motor.send_command = send  # type: ignore[method-assign]
    axis.motor.wait_until_ready = wait  # type: ignore[method-assign]

    async def move():
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await axis.move_to_ticks(5)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")

    scopes: list[anyio.CancelScope] = []
    async with anyio.create_task_group() as group:
      group.start_soon(move)
      await waiting.wait()
      scopes[0].cancel()
      release.set()
    assert events.index("settled") < events.index("cancelled")
