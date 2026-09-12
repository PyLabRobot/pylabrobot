import threading
from typing import Any, Optional
from unittest.mock import patch

import anyio
import pytest

pytest.importorskip("numpy")

from pylabrobot.agilent.biotek.cytation.microscope import aravis_camera as camera_module
from pylabrobot.agilent.biotek.cytation.microscope.aravis_camera import AravisCamera
from pylabrobot.concurrency import HasLifespan
from pylabrobot.testing.concurrency import AnyioTestBase


class FakeBuffer:
  def __init__(self, size: int) -> None:
    self.size = size

  def get_data(self) -> bytes:
    return bytes(range(self.size))


class FakeStream:
  def __init__(self, events: list[str], fail_push: Optional[int] = None) -> None:
    self.events = events
    self.fail_push = fail_push
    self.buffers: list[FakeBuffer] = []
    self.pushes = 0
    self.capture_buffer: Optional[FakeBuffer] = FakeBuffer(4)
    self.pop_started: Optional[threading.Event] = None
    self.release_pop: Optional[threading.Event] = None

  def push_buffer(self, buffer: FakeBuffer) -> None:
    self.pushes += 1
    self.events.append("buffer.push")
    if self.pushes == self.fail_push:
      raise RuntimeError("buffer allocation failed")
    self.buffers.append(buffer)

  def try_pop_buffer(self) -> Optional[FakeBuffer]:
    if not self.buffers:
      return None
    self.events.append("buffer.drain")
    return self.buffers.pop()

  def timeout_pop_buffer(self, timeout_us: int) -> Optional[FakeBuffer]:
    self.events.append(f"buffer.wait:{timeout_us}")
    if self.pop_started is not None and self.release_pop is not None:
      self.pop_started.set()
      self.release_pop.wait(timeout=2)
    buffer = self.capture_buffer
    self.capture_buffer = None
    return buffer


class FakeDevice:
  def __init__(self, events: list[str]) -> None:
    self.events = events

  def set_string_feature_value(self, name: str, value: str) -> None:
    self.events.append(f"feature:{name}:{value}")

  def get_string_feature_value(self, name: str) -> str:
    values = {
      "DeviceSerialNumber": "camera-123",
      "DeviceModelName": "BlackFly",
      "DeviceVendorName": "FLIR",
      "DeviceFirmwareVersion": "1.0",
    }
    return values[name]

  def execute_command(self, name: str) -> None:
    self.events.append(f"execute:{name}")


class FakeCamera:
  def __init__(
    self,
    events: list[str],
    device: FakeDevice,
    stream: FakeStream,
    fail_phase: Optional[str] = None,
  ) -> None:
    self.events = events
    self.device = device
    self.stream = stream
    self.fail_phase = fail_phase
    self.start_error: Optional[BaseException] = None
    self.stop_error: Optional[BaseException] = None
    self.streaming = False

  def get_device(self) -> FakeDevice:
    self.events.append("camera.get_device")
    if self.fail_phase == "camera_open":
      raise RuntimeError("camera open follow-up failed")
    return self.device

  def get_region(self) -> tuple[int, int, int, int]:
    self.events.append("camera.get_region")
    if self.fail_phase == "trigger_change":
      raise RuntimeError("trigger follow-up failed")
    return (0, 0, 2, 2)

  def get_payload(self) -> int:
    return 4

  def create_stream(self, callback: Any, data: Any) -> FakeStream:
    del callback, data
    self.events.append("stream.create")
    if self.fail_phase == "stream_creation":
      raise RuntimeError("stream creation failed")
    return self.stream

  def start_acquisition(self) -> None:
    self.events.append("acquisition.start")
    self.streaming = True
    if self.start_error is not None:
      raise self.start_error

  def stop_acquisition(self) -> None:
    self.events.append("acquisition.stop")
    if self.stop_error is not None:
      raise self.stop_error
    self.streaming = False

  def set_exposure_time(self, value: float) -> None:
    self.events.append(f"exposure:{value}")

  def get_exposure_time(self) -> float:
    return 2500.0

  def set_gain(self, value: float) -> None:
    self.events.append(f"gain:{value}")

  def get_gain(self) -> float:
    return 3.0

  def set_pixel_format(self, value: int) -> None:
    self.events.append(f"pixel_format:{value}")


class FakeBufferFactory:
  @staticmethod
  def new_allocate(size: int) -> FakeBuffer:
    return FakeBuffer(size)


class FakeAravis:
  PIXEL_FORMAT_MONO_8 = 8
  Buffer = FakeBufferFactory

  def __init__(self, fail_phase: Optional[str] = None) -> None:
    self.events: list[str] = []
    self.device = FakeDevice(self.events)
    fail_push = 3 if fail_phase == "buffers" else None
    self.stream = FakeStream(self.events, fail_push=fail_push)
    self.camera = FakeCamera(self.events, self.device, self.stream, fail_phase)
    parent = self

    class CameraFactory:
      @staticmethod
      def new(device_id: str) -> FakeCamera:
        parent.events.append(f"camera.open:{device_id}")
        return parent.camera

    self.Camera = CameraFactory

  def update_device_list(self) -> None:
    self.events.append("devices.update")

  def get_n_devices(self) -> int:
    return 1

  def get_device_id(self, index: int) -> str:
    assert index == 0
    return "camera-id"

  def get_device_serial_nbr(self, index: int) -> str:
    assert index == 0
    return "camera-123"


def fake_camera(fail_phase: Optional[str] = None) -> tuple[AravisCamera, FakeAravis]:
  aravis = FakeAravis(fail_phase)
  camera = AravisCamera()
  return camera, aravis


class TestAravisCameraLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert AravisCamera.__aenter__ is HasLifespan.__aenter__
    assert AravisCamera.__aexit__ is HasLifespan.__aexit__
    assert AravisCamera.setup is HasLifespan.setup
    assert AravisCamera.stop is HasLifespan.stop

  async def test_normal_exit_stops_acquisition_drains_buffers_and_resets_trigger(self) -> None:
    camera, aravis = fake_camera()
    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      async with camera.lifespan(serial_number="camera-123"):
        assert camera.is_active
        await camera.start_acquisition()

    assert "acquisition.stop" in aravis.events
    assert aravis.events.count("buffer.drain") == camera_module._BUFFER_COUNT
    assert "feature:TriggerMode:Off" in aravis.events
    assert aravis.events.index("acquisition.stop") < aravis.events.index("feature:TriggerMode:Off")
    assert camera._camera is None
    assert camera._stream is None

  async def test_body_failure_is_preserved_and_camera_is_reusable(self) -> None:
    camera, aravis = fake_camera()
    failure = RuntimeError("body failed")
    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      with pytest.raises(RuntimeError) as error_info:
        async with camera.lifespan(serial_number="camera-123"):
          raise failure
      assert error_info.value is failure
      async with camera.lifespan(serial_number="camera-123"):
        pass
    assert aravis.events.count("camera.open:camera-id") == 2

  async def test_rejects_unknown_options_before_discovery(self) -> None:
    camera, aravis = fake_camera()
    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      with pytest.raises(TypeError, match="unexpected keyword argument"):
        async with camera.lifespan(serial_number="camera-123", unknown=True):
          pytest.fail("invalid options reached body")
    assert aravis.events == []

  @pytest.mark.parametrize(
    ("phase", "message"),
    [
      ("camera_open", "camera open follow-up failed"),
      ("trigger_change", "trigger follow-up failed"),
      ("stream_creation", "stream creation failed"),
      ("buffers", "buffer allocation failed"),
    ],
  )
  async def test_partial_entry_rollback(self, phase: str, message: str) -> None:
    camera, aravis = fake_camera(phase)
    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      with pytest.raises(RuntimeError, match=message):
        async with camera.lifespan(serial_number="camera-123"):
          pytest.fail("failed entry reached body")

    if phase in ("trigger_change", "stream_creation", "buffers"):
      assert "feature:TriggerMode:Off" in aravis.events
    if phase == "buffers":
      assert aravis.events.count("buffer.drain") == 2
    assert camera._camera is None
    assert camera._device is None
    assert camera._stream is None


class TestAravisCameraOperations(AnyioTestBase):
  async def test_acquisition_scope_owns_transitions_and_supports_reuse(self) -> None:
    camera, aravis = fake_camera()
    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      async with camera.lifespan(serial_number="camera-123"):
        for _ in range(2):
          async with camera.acquisition():
            assert camera.is_acquiring
          assert not camera.is_acquiring

    assert aravis.events.count("acquisition.start") == 2
    assert aravis.events.count("acquisition.stop") == 2

  async def test_acquisition_requires_active_configured_camera_before_ownership(self) -> None:
    camera = AravisCamera()
    with pytest.raises(RuntimeError, match="active configured camera"):
      async with camera.acquisition():
        pytest.fail("inactive camera reached scope body")
    assert camera._acquisition_scope_token is None

  async def test_direct_transitions_are_rejected_while_scope_is_active(self) -> None:
    camera, aravis = fake_camera()
    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      async with camera.lifespan(serial_number="camera-123"):
        async with camera.acquisition():
          with pytest.raises(RuntimeError, match="acquisition scope"):
            await camera.start_acquisition()
          with pytest.raises(RuntimeError, match="acquisition scope"):
            await camera.stop_acquisition()

  async def test_scope_start_and_stop_failures_preserve_transition_state(self) -> None:
    camera, aravis = fake_camera()
    start_error = RuntimeError("start failed")
    aravis.camera.start_error = start_error
    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      async with camera.lifespan(serial_number="camera-123"):
        with pytest.raises(RuntimeError) as error_info:
          async with camera.acquisition():
            pytest.fail("failed start reached body")
        assert error_info.value is start_error
        assert camera._acquisition_scope_token is None
        assert not camera.is_acquiring
        assert not aravis.camera.streaming
        assert aravis.events.count("acquisition.stop") == 1

        aravis.camera.start_error = None
        stop_error = RuntimeError("stop failed")
        aravis.camera.stop_error = stop_error
        with pytest.raises(RuntimeError) as error_info:
          async with camera.acquisition():
            pass
        assert error_info.value is stop_error
        assert camera._acquisition_scope_token is None
        assert camera.is_acquiring
        assert camera._acquisition_state_unknown
        assert aravis.camera.streaming
        aravis.camera.stop_error = None
        await camera.stop_acquisition()
        assert not camera.is_acquiring
        assert not camera._acquisition_state_unknown
        assert not aravis.camera.streaming

        async with camera.acquisition():
          assert aravis.camera.streaming

  async def test_failed_start_rollback_retains_unknown_for_parent_retry(self) -> None:
    camera, aravis = fake_camera()
    start_error = RuntimeError("start failed after commit")
    stop_error = RuntimeError("rollback failed")
    aravis.camera.start_error = start_error
    aravis.camera.stop_error = stop_error
    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      async with camera.lifespan(serial_number="camera-123"):
        with pytest.raises(RuntimeError) as error_info:
          async with camera.acquisition():
            pytest.fail("failed start reached scope body")
        assert error_info.value is stop_error
        assert error_info.value.__context__ is start_error
        assert camera._acquisition_state_unknown
        assert aravis.camera.streaming
        aravis.camera.start_error = None
        aravis.camera.stop_error = None

      assert not aravis.camera.streaming
      assert aravis.events.count("acquisition.stop") == 2

      async with camera.lifespan(serial_number="camera-123"):
        async with camera.acquisition():
          assert aravis.camera.streaming

  async def test_cancelled_scope_contender_does_not_wait_under_shield_or_start(self) -> None:
    camera, aravis = fake_camera()
    contender_started = anyio.Event()
    contender_finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def contend() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          contender_started.set()
          async with camera.acquisition():
            pytest.fail("cancelled contender reached scope body")
      finally:
        contender_finished.set()

    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      async with camera.lifespan(serial_number="camera-123"):
        async with anyio.create_task_group() as task_group:
          async with camera._acquisition_transition_lock:
            task_group.start_soon(contend)
            await contender_started.wait()
            await anyio.sleep(0.01)
            scopes[0].cancel()
            await anyio.sleep(0.01)
            assert contender_finished.is_set()
            assert "acquisition.start" not in aravis.events

  async def test_cancelled_scope_before_worker_dispatch_does_not_touch_camera(self) -> None:
    camera, aravis = fake_camera()
    worker_started = anyio.Event()
    worker_release = threading.Event()
    entry_started = anyio.Event()
    entry_finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    def occupy_worker() -> None:
      anyio.from_thread.run_sync(worker_started.set)
      worker_release.wait()

    async def enter_scope() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          entry_started.set()
          async with camera.acquisition():
            pytest.fail("cancelled pre-dispatch entry reached scope body")
      finally:
        entry_finished.set()

    limiter = anyio.to_thread.current_default_thread_limiter()
    original_tokens = limiter.total_tokens
    limiter.total_tokens = 1
    try:
      with (
        patch.object(camera_module, "HAS_ARAVIS", True),
        patch.object(camera_module, "Aravis", aravis),
      ):
        async with camera.lifespan(serial_number="camera-123"):
          async with anyio.create_task_group() as task_group:
            try:
              task_group.start_soon(anyio.to_thread.run_sync, occupy_worker)
              await worker_started.wait()
              task_group.start_soon(enter_scope)
              await entry_started.wait()
              await anyio.wait_all_tasks_blocked()
              scopes[0].cancel()
              with anyio.move_on_after(1):
                await entry_finished.wait()
              returned_before_worker_release = entry_finished.is_set()
            finally:
              worker_release.set()

          assert returned_before_worker_release
          assert "acquisition.start" not in aravis.events
          assert "acquisition.stop" not in aravis.events
          assert camera._acquisition_scope_token is None
          assert not camera._acquisition_state_unknown
          assert not camera._acquiring
    finally:
      limiter.total_tokens = original_tokens

  async def test_scope_body_cancellation_stops_before_returning(self) -> None:
    camera, aravis = fake_camera()
    entered = anyio.Event()

    async def use_scope() -> None:
      async with camera.acquisition():
        entered.set()
        await anyio.sleep_forever()

    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      async with camera.lifespan(serial_number="camera-123"):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(use_scope)
          await entered.wait()
          task_group.cancel_scope.cancel()
        assert not camera.is_acquiring
        assert camera._acquisition_scope_token is None

  async def test_parent_cleanup_stops_and_invalidates_open_scope(self) -> None:
    camera, aravis = fake_camera()
    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      lifespan = camera.lifespan(serial_number="camera-123")
      await lifespan.__aenter__()
      scope = camera.acquisition()
      await scope.__aenter__()
      await lifespan.__aexit__(None, None, None)
      assert camera._acquisition_scope_token is None
      await scope.__aexit__(None, None, None)

    assert aravis.events.count("acquisition.stop") == 1

  async def test_blocking_operations_run_in_workers(self) -> None:
    camera, aravis = fake_camera()
    caller_thread = threading.get_ident()
    called_threads: list[int] = []
    original = aravis.device.set_string_feature_value

    def record_thread(name: str, value: str) -> None:
      called_threads.append(threading.get_ident())
      original(name, value)

    aravis.device.set_string_feature_value = record_thread  # type: ignore[method-assign]
    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      async with camera.lifespan(serial_number="camera-123"):
        await camera.set_exposure(2.5)
        await camera.set_gain(3.0)
        assert await camera.get_exposure() == 2.5
        assert await camera.get_gain() == 3.0

    assert called_threads
    assert all(thread_id != caller_thread for thread_id in called_threads)

  async def test_trigger_uses_finite_wait_and_returns_buffer(self) -> None:
    camera, aravis = fake_camera()
    capture_buffer = aravis.stream.capture_buffer
    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      async with camera.lifespan(serial_number="camera-123"):
        await camera.start_acquisition()
        image = await camera.trigger(timeout_ms=37)
        assert image.shape == (2, 2)
        assert capture_buffer in aravis.stream.buffers

    assert "buffer.wait:37000" in aravis.events

  async def test_trigger_cancellation_waits_for_pop_and_returns_buffer(self) -> None:
    camera, aravis = fake_camera()
    pop_started = threading.Event()
    release_pop = threading.Event()
    aravis.stream.pop_started = pop_started
    aravis.stream.release_pop = release_pop
    capture_buffer = aravis.stream.capture_buffer
    finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def capture() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await camera.trigger(timeout_ms=100)
        finally:
          finished.set()

    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      async with camera.lifespan(serial_number="camera-123"):
        await camera.start_acquisition()
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(capture)
          while not pop_started.is_set():
            await anyio.sleep(0.001)
          scopes[0].cancel()
          await anyio.sleep(0.01)
          assert not finished.is_set()
          release_pop.set()
          await finished.wait()
          assert capture_buffer in aravis.stream.buffers

  async def test_trigger_cancellation_after_dispatch_completes_frame_transaction(self) -> None:
    camera, aravis = fake_camera()
    trigger_started = threading.Event()
    release_trigger = threading.Event()
    capture_buffer = aravis.stream.capture_buffer
    finished = anyio.Event()
    returned_frame = False
    scopes: list[anyio.CancelScope] = []

    def blocking_trigger(name: str) -> None:
      assert name == "TriggerSoftware"
      aravis.events.append(f"execute:{name}")
      trigger_started.set()
      release_trigger.wait(2)

    aravis.device.execute_command = blocking_trigger  # type: ignore[method-assign]

    async def capture() -> None:
      nonlocal returned_frame
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await camera.trigger(timeout_ms=100)
        returned_frame = True
      finished.set()

    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      async with camera.lifespan(serial_number="camera-123"):
        await camera.start_acquisition()
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(capture)
          assert await anyio.to_thread.run_sync(trigger_started.wait, 1)
          scopes[0].cancel()
          release_trigger.set()
          await finished.wait()

          assert not returned_frame
          assert "buffer.wait:100000" in aravis.events
          assert aravis.stream.capture_buffer is None
          assert capture_buffer in aravis.stream.buffers

  async def test_trigger_transactions_are_serialized_and_stop_waits_for_return(self) -> None:
    camera, aravis = fake_camera()
    pop_started = threading.Event()
    release_pop = threading.Event()
    aravis.stream.pop_started = pop_started
    aravis.stream.release_pop = release_pop
    trigger_finished = anyio.Event()
    stop_finished = anyio.Event()

    async def trigger() -> None:
      await camera.trigger(timeout_ms=100)
      trigger_finished.set()

    async def stop() -> None:
      await camera.stop_acquisition()
      stop_finished.set()

    with (
      patch.object(camera_module, "HAS_ARAVIS", True),
      patch.object(camera_module, "Aravis", aravis),
    ):
      async with camera.lifespan(serial_number="camera-123"):
        await camera.start_acquisition()
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(trigger)
          assert await anyio.to_thread.run_sync(pop_started.wait, 1)
          task_group.start_soon(stop)
          await anyio.sleep(0.01)
          assert not stop_finished.is_set()
          release_pop.set()
          await trigger_finished.wait()
          await stop_finished.wait()

    assert aravis.events.index("buffer.push", camera_module._BUFFER_COUNT) < aravis.events.index(
      "acquisition.stop"
    )
