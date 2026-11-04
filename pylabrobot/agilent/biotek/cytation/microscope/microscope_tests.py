import unittest
import unittest.mock
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional, cast

import anyio
import pytest

pytest.importorskip("numpy")

import numpy as np

from pylabrobot.agilent.biotek.cytation.microscope.microscope import (
  COLOR_BRIGHTFIELD_LED_CODES,
  CytationImagingConfig,
  CytationMicroscope,
)
from pylabrobot.agilent.biotek.cytation.microscope.models import ImagingMode, Objective
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.resources import CellVis_24_wellplate_3600uL_Fb
from pylabrobot.testing.concurrency import AnyioTestBase


def _make_backend(version: str = "2.07"):
  """A microscope with a fully mocked serial driver (no hardware)."""
  driver = unittest.mock.MagicMock()
  driver.version = version
  driver.send_command = unittest.mock.AsyncMock()
  backend = CytationMicroscope(driver=driver, use_cam=False)
  return backend, driver


class TestColorBrightfield(AnyioTestBase):
  """Color brightfield illuminates with three LEDs (codes 6/7/8) and stacks the frames."""

  def test_imaging_mode_code_uses_brightfield_light_path(self):
    backend, _ = _make_backend()
    # Color brightfield shares the brightfield filter cube (5); colors come from the LEDs.
    assert backend._imaging_mode_code(ImagingMode.COLOR_BRIGHTFIELD) == 5

  async def test_acquire_cycles_three_leds_and_stacks_rgb(self):
    backend, driver = _make_backend()
    frame = np.zeros((4, 5), dtype=np.uint8)
    backend.acquire_image = unittest.mock.AsyncMock(return_value=frame)

    image = await backend._acquire_color_brightfield_image(led_intensity=10)

    # One mono frame per color, stacked into an (H, W, 3) RGB image.
    assert image.shape == (4, 5, 3)
    assert backend.acquire_image.await_count == 3

    # Per color, in cycle order: LED on (L0{code}10) then strobe (l{code}).
    expected = []
    for code in COLOR_BRIGHTFIELD_LED_CODES:
      expected.append(unittest.mock.call("i", f"L0{code}10"))
      expected.append(unittest.mock.call("i", f"l{code}"))
    assert driver.send_command.await_args_list == expected

  async def test_set_imaging_mode_matches_brightfield_setup_without_static_led(self):
    backend, driver = _make_backend(version="2.07")
    backend._imaging_mode = None

    await backend.set_imaging_mode(ImagingMode.COLOR_BRIGHTFIELD, led_intensity=10)

    sent = [call.args for call in driver.send_command.await_args_list]
    # Same optics setup as plain brightfield (filter cube 5).
    for cmd in (("Y", "P1101"), ("Y", "P0d05"), ("Y", "P1002")):
      assert cmd in sent
    # No single LED is enabled (colors are cycled during acquisition): only led_off (L0001).
    assert [p for (ch, p) in sent if ch == "i"] == ["L0001"]
    assert backend._imaging_mode == ImagingMode.COLOR_BRIGHTFIELD

  async def test_color_brightfield_not_supported_on_cytation1(self):
    backend, _ = _make_backend(version="1.05")
    backend._imaging_mode = None
    with pytest.raises(NotImplementedError):
      await backend.set_imaging_mode(ImagingMode.COLOR_BRIGHTFIELD, led_intensity=10)


class RecordingCamera(HasLifespan):
  """Software camera resource for microscope ownership tests."""

  def __init__(self, events: list[str], entry_error: Optional[BaseException] = None) -> None:
    super().__init__()
    self.events = events
    self.entry_error = entry_error
    self.acquiring = False
    self.scoped = False

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    *,
    serial_number: Optional[str] = None,
    **kwargs: Any,
  ) -> None:
    if kwargs:
      raise TypeError("RecordingCamera only accepts serial_number")
    self.events.append(f"camera.enter:{serial_number}")
    stack.push_shielded_async_callback(self._close)
    if self.entry_error is not None:
      raise self.entry_error

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("camera.exit")

  async def get_device_info(self) -> str:
    return "fake camera"

  @property
  def is_acquiring(self) -> bool:
    return self.acquiring

  @property
  def is_acquisition_scoped(self) -> bool:
    return self.scoped

  async def start_acquisition(self) -> None:
    self.events.append("acquisition.start")
    self.acquiring = True

  async def stop_acquisition(self) -> None:
    self.events.append("acquisition.stop")
    self.acquiring = False

  @asynccontextmanager
  async def acquisition(self) -> AsyncIterator[None]:
    self.events.append("acquisition.enter")
    self.scoped = True
    self.acquiring = True
    try:
      yield
    finally:
      self.acquiring = False
      self.scoped = False
      self.events.append("acquisition.exit")


def make_microscope(
  *, use_cam: bool = True
) -> tuple[CytationMicroscope, RecordingCamera, list[str]]:
  events: list[str] = []
  driver = unittest.mock.MagicMock()
  driver.version = "2.07"
  driver.send_command = unittest.mock.AsyncMock()
  driver.set_plate = unittest.mock.AsyncMock()
  microscope = CytationMicroscope(
    driver=driver,
    camera_serial="camera-123",
    imaging_config=CytationImagingConfig(filters=[], objectives=[]),
    use_cam=use_cam,
  )
  camera = RecordingCamera(events)
  microscope.camera = camera  # type: ignore[assignment]
  return microscope, camera, events


class TestMicroscopeLifecycle(AnyioTestBase):
  def test_uses_managed_lifecycle_methods(self) -> None:
    assert CytationMicroscope.__aenter__ is HasLifespan.__aenter__
    assert CytationMicroscope.__aexit__ is HasLifespan.__aexit__
    assert CytationMicroscope.setup is HasLifespan.setup
    assert CytationMicroscope.stop is HasLifespan.stop

  async def test_owns_camera_for_normal_exit_body_failure_and_reuse(self) -> None:
    microscope, camera, events = make_microscope()
    failure = RuntimeError("body failed")
    with pytest.raises(RuntimeError) as error_info:
      async with microscope:
        assert microscope.is_active and camera.is_active
        raise failure
    assert error_info.value is failure
    assert events == ["camera.enter:camera-123", "camera.exit"]

    async with microscope:
      pass
    assert events == [
      "camera.enter:camera-123",
      "camera.exit",
      "camera.enter:camera-123",
      "camera.exit",
    ]

  async def test_camera_entry_failure_rolls_back(self) -> None:
    microscope, _, events = make_microscope()
    failure = RuntimeError("camera entry failed")
    microscope.camera = RecordingCamera(events, entry_error=failure)  # type: ignore[assignment]
    with pytest.raises(RuntimeError) as error_info:
      async with microscope:
        pytest.fail("failed entry reached body")
    assert error_info.value is failure
    assert events == ["camera.enter:camera-123", "camera.exit"]

  async def test_discovery_failure_closes_camera(self) -> None:
    microscope, _, events = make_microscope()
    microscope._filters = None
    microscope._load_filters = unittest.mock.AsyncMock(  # type: ignore[method-assign]
      side_effect=RuntimeError("filter discovery failed")
    )
    with pytest.raises(RuntimeError, match="filter discovery failed"):
      async with microscope:
        pytest.fail("failed entry reached body")
    assert events[-1] == "camera.exit"

  async def test_no_camera_mode_does_not_enter_camera(self) -> None:
    microscope, _, events = make_microscope(use_cam=False)
    async with microscope:
      pass
    assert events == []

  async def test_acquisition_scope_delegates_without_microscope_token(self) -> None:
    microscope, _, events = make_microscope()
    async with microscope:
      async with microscope.acquisition():
        with pytest.raises(AttributeError):
          _ = microscope._acquisition_scope_token  # type: ignore[attr-defined]
    assert events == [
      "camera.enter:camera-123",
      "acquisition.enter",
      "acquisition.exit",
      "camera.exit",
    ]

  async def test_rejects_lifespan_options_before_camera_entry(self) -> None:
    microscope, _, events = make_microscope()
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with microscope.lifespan(unknown=True):
        pytest.fail("invalid options reached body")
    assert events == []


class TestCaptureCleanup(AnyioTestBase):
  def _prepared_capture(self) -> tuple[CytationMicroscope, anyio.Event]:
    microscope, _, _ = make_microscope(use_cam=False)
    microscope._objective = Objective.O_4X_PL_FL
    microscope._focal_height = 1.0
    cast(RecordingCamera, microscope.camera).acquiring = True
    microscope.set_objective = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.set_imaging_mode = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.select = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.set_exposure = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.set_gain = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.set_focus = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.set_position = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    capture_started = anyio.Event()

    async def block_capture() -> np.ndarray:
      capture_started.set()
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    microscope.acquire_image = block_capture  # type: ignore[method-assign]
    return microscope, capture_started

  async def test_capture_cancellation_shields_led_off(self) -> None:
    microscope, capture_started = self._prepared_capture()
    led_off_finished = anyio.Event()

    async def led_off() -> None:
      await anyio.sleep(0.01)
      led_off_finished.set()

    microscope.led_off = led_off  # type: ignore[method-assign]
    plate = CellVis_24_wellplate_3600uL_Fb(name="plate")

    async def capture() -> None:
      await microscope.capture(
        row=1,
        column=1,
        mode=ImagingMode.BRIGHTFIELD,
        objective=Objective.O_4X_PL_FL,
        exposure_time=1.0,
        focal_height=1.0,
        gain=1.0,
        plate=plate,
        auto_stop_acquisition=False,
      )

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(capture)
      await capture_started.wait()
      task_group.cancel_scope.cancel()

    assert led_off_finished.is_set()

  async def test_led_off_failure_propagates(self) -> None:
    microscope, _ = self._prepared_capture()
    microscope.acquire_image = unittest.mock.AsyncMock(side_effect=RuntimeError("capture failed"))  # type: ignore[method-assign]
    cleanup_failure = RuntimeError("LED off failed")
    microscope.led_off = unittest.mock.AsyncMock(side_effect=cleanup_failure)  # type: ignore[method-assign]
    plate = CellVis_24_wellplate_3600uL_Fb(name="plate")

    with pytest.raises(RuntimeError) as error_info:
      await microscope.capture(
        row=1,
        column=1,
        mode=ImagingMode.BRIGHTFIELD,
        objective=Objective.O_4X_PL_FL,
        exposure_time=1.0,
        focal_height=1.0,
        gain=1.0,
        plate=plate,
        auto_stop_acquisition=False,
      )

    assert error_info.value is cleanup_failure
    assert str(error_info.value.__context__) == "capture failed"

  async def test_capture_borrows_scoped_acquisition_without_stopping(self) -> None:
    microscope, camera, events = make_microscope()
    microscope._objective = Objective.O_4X_PL_FL
    microscope._focal_height = 1.0
    microscope.set_objective = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.set_imaging_mode = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.select = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.set_exposure = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.set_gain = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.set_focus = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.set_position = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.led_off = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    microscope.acquire_image = unittest.mock.AsyncMock(return_value=np.zeros((2, 2)))  # type: ignore[method-assign]
    microscope.get_exposure = unittest.mock.AsyncMock(return_value=1.0)  # type: ignore[method-assign]
    plate = CellVis_24_wellplate_3600uL_Fb(name="plate")

    async with microscope:
      async with microscope.acquisition():
        result = await microscope.capture(
          1,
          1,
          ImagingMode.BRIGHTFIELD,
          Objective.O_4X_PL_FL,
          1.0,
          1.0,
          1.0,
          plate,
          auto_stop_acquisition=True,
        )
        assert camera.acquiring
        assert len(result.images) == 1

    assert "acquisition.stop" not in events

  async def test_scoped_frame_retry_does_not_cycle_acquisition(self) -> None:
    microscope, camera, events = make_microscope()
    microscope.imaging_config.max_image_read_attempts = 2
    microscope.imaging_config.image_read_delay = 0
    attempts = 0

    async def trigger(timeout_ms: int) -> np.ndarray:
      nonlocal attempts
      assert timeout_ms == 5000
      attempts += 1
      if attempts == 1:
        raise RuntimeError("frame failed")
      return np.zeros((1, 1))

    camera.trigger = trigger  # type: ignore[attr-defined]
    async with microscope:
      async with microscope.acquisition():
        await microscope.acquire_image()

    assert attempts == 2
    assert "acquisition.start" not in events
    assert "acquisition.stop" not in events


if __name__ == "__main__":
  unittest.main()
