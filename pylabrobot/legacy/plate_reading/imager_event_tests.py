import json
import unittest
from typing import Any, Dict, List, Literal, Optional, cast
from unittest.mock import patch

from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.legacy.plate_reading.backend import ImagerBackend, ImageReaderBackend
from pylabrobot.legacy.plate_reading.image_reader import ImageReader
from pylabrobot.legacy.plate_reading.imager import Imager
from pylabrobot.legacy.plate_reading.standard import (
  AutoExposure,
  AutoFocus,
  Image,
  ImagingMode,
  ImagingResult,
  Objective,
)
from pylabrobot.resources import Coordinate, Plate, Resource, Well
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb


class _RecordingImagerBackend(ImagerBackend):
  def __init__(self) -> None:
    self.calls: list[dict[str, Any]] = []
    self.results: list[ImagingResult] = []
    self.failure: Optional[BaseException] = None

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def capture(
    self,
    row: int,
    column: int,
    mode: ImagingMode,
    objective: Objective,
    exposure_time,
    focal_height,
    gain,
    plate: Plate,
    **backend_kwargs: Any,
  ) -> ImagingResult:
    call = {
      "row": row,
      "column": column,
      "mode": mode,
      "objective": objective,
      "exposure_time": exposure_time,
      "focal_height": focal_height,
      "gain": gain,
      "plate": plate,
      "backend_kwargs": backend_kwargs,
    }
    self.calls.append(call)
    if self.failure is not None:
      raise self.failure
    result = ImagingResult(
      images=[cast(Image, {"focal_height": focal_height, "pixels": object()})],
      exposure_time=float(exposure_time) if isinstance(exposure_time, (int, float)) else 12.5,
      focal_height=float(focal_height) if isinstance(focal_height, (int, float)) else 1.25,
    )
    self.results.append(result)
    return result


class _RecordingImageReaderBackend(ImageReaderBackend):
  def __init__(self) -> None:
    self.records: List[Dict] = [{"data": [[1.0]]}]
    self.capture_calls = 0

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def open(self, **backend_kwargs: Any) -> None:
    pass

  async def close(self, plate: Optional[Plate], **backend_kwargs: Any) -> None:
    pass

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float,
    **backend_kwargs: Any,
  ) -> List[Dict]:
    return self.records

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    **backend_kwargs: Any,
  ) -> List[Dict]:
    return self.records

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
    **backend_kwargs: Any,
  ) -> List[Dict]:
    return self.records

  async def capture(
    self,
    row: int,
    column: int,
    mode: ImagingMode,
    objective: Objective,
    exposure_time,
    focal_height,
    gain,
    plate: Plate,
    **backend_kwargs: Any,
  ) -> ImagingResult:
    self.capture_calls += 1
    return ImagingResult(images=[cast(Image, object())], exposure_time=10.0, focal_height=1.0)


class ImagerEventTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.backend = _RecordingImagerBackend()
    self.imager = Imager(
      name="imager",
      size_x=1,
      size_y=1,
      size_z=1,
      backend=self.backend,
    )
    self.plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.imager.assign_child_resource(self.plate, location=Coordinate.zero())
    await self.imager.setup()

  def assert_capture_lifecycle(self, events: list[PLREvent], terminal: str) -> None:
    self.assertEqual(
      [event.name for event in events],
      ["imager.capture.started", f"imager.capture.{terminal}"],
    )
    self.assertEqual(events[0].context["operation_id"], events[1].context["operation_id"])

  async def test_fixed_capture_emits_direct_well_and_bounded_completion_metadata(self) -> None:
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)
    well = self.plate.get_well("B2")

    with use_event_bus(event_bus):
      result = await self.imager.capture(
        well,
        ImagingMode.BRIGHTFIELD,
        Objective.O_4X_PL_FL,
        10.0,
        1.0,
        2.0,
        vendor_flag="kept",
      )

    self.assert_capture_lifecycle(events, "completed")
    self.assertIs(result, self.backend.results[0])
    started, completed = events
    self.assertEqual(started.data["device"]["name"], "imager")
    self.assertEqual(started.data["plate"]["name"], "plate")
    self.assertEqual([resource["name"] for resource in started.data["resources"]], [well.name])
    self.assertEqual(started.data["resources"][0]["ancestors"][0]["name"], "plate")
    self.assertEqual(started.data["target"], {"row": 1, "column": 1})
    self.assertEqual(started.data["mode"], "BRIGHTFIELD")
    self.assertEqual(started.data["objective"], "O_4X_PL_FL")
    self.assertEqual(started.data["exposure"], {"mode": "fixed", "time_ms": 10.0})
    self.assertEqual(started.data["focus"], {"mode": "fixed", "height": 1.0})
    self.assertEqual(started.data["gain"], {"mode": "fixed", "value": 2.0})
    self.assertNotIn("image_count", started.data)
    self.assertEqual(completed.data["image_count"], 1)
    self.assertEqual(completed.data["reported_exposure_time_ms"], 10.0)
    self.assertEqual(completed.data["reported_focal_height"], 1.0)
    self.assertNotIn("images", completed.data)
    self.assertNotIn("backend_kwargs", completed.data)
    self.assertEqual(self.backend.calls[0]["backend_kwargs"], {"vendor_flag": "kept"})
    json.dumps(started.as_dict(), allow_nan=False)
    json.dumps(completed.as_dict(), allow_nan=False)

  async def test_tuple_target_and_machine_auto_settings_remain_compact(self) -> None:
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.imager.capture(
        well=(2, 3),
        mode=ImagingMode.PHASE_CONTRAST,
        objective=Objective.O_10X_PL_FL,
      )

    self.assert_capture_lifecycle(events, "completed")
    started = events[0]
    self.assertEqual(started.data["resources"], [])
    self.assertEqual(started.data["target"], {"row": 2, "column": 3})
    self.assertEqual(started.data["exposure"], {"mode": "machine_auto"})
    self.assertEqual(started.data["focus"], {"mode": "machine_auto"})
    self.assertEqual(started.data["gain"], {"mode": "machine_auto"})

  async def test_auto_exposure_has_one_lifecycle_for_multiple_backend_attempts(self) -> None:
    evaluations = 0

    async def evaluate_exposure(
      image: object,
    ) -> Literal["higher", "lower", "good"]:
      nonlocal evaluations
      evaluations += 1
      return "higher" if evaluations == 1 else "good"

    auto_exposure = AutoExposure(
      evaluate_exposure=evaluate_exposure,
      max_rounds=4,
      low=1.0,
      high=9.0,
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.imager.capture(
        self.plate.get_well("A1"),
        ImagingMode.BRIGHTFIELD,
        Objective.O_4X_PL_FL,
        exposure_time=auto_exposure,
        focal_height=1.0,
        gain=1.0,
      )

    self.assertGreater(len(self.backend.calls), 1)
    self.assert_capture_lifecycle(events, "completed")
    self.assertEqual(
      events[0].data["exposure"],
      {
        "mode": "software_auto",
        "minimum_time_ms": 1.0,
        "maximum_time_ms": 9.0,
        "max_rounds": 4,
      },
    )

  async def test_auto_focus_has_one_lifecycle_for_all_search_attempts(self) -> None:
    auto_focus = AutoFocus(
      evaluate_focus=lambda image: float(image["focal_height"]),
      timeout=10.0,
      low=0.0,
      high=1.0,
      tolerance=0.4,
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.imager.capture(
        self.plate.get_well("A1"),
        ImagingMode.BRIGHTFIELD,
        Objective.O_4X_PL_FL,
        exposure_time=10.0,
        focal_height=auto_focus,
        gain=1.0,
      )

    self.assertGreater(len(self.backend.calls), 1)
    self.assert_capture_lifecycle(events, "completed")
    self.assertEqual(
      events[0].data["focus"],
      {
        "mode": "software_auto",
        "minimum_height": 0.0,
        "maximum_height": 1.0,
        "tolerance": 0.4,
        "timeout": 10.0,
      },
    )

  async def test_combined_auto_exposure_and_focus_preserve_one_lifecycle(self) -> None:
    exposure_evaluations = 0

    async def accept_exposure(image: Image) -> Literal["higher", "lower", "good"]:
      nonlocal exposure_evaluations
      exposure_evaluations += 1
      return "good"

    auto_exposure = AutoExposure(accept_exposure, max_rounds=2, low=1.0, high=2.0)
    auto_focus = AutoFocus(
      evaluate_focus=lambda image: float(image["focal_height"]),
      timeout=10.0,
      low=0.0,
      high=1.0,
      tolerance=0.4,
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      result = await self.imager.capture(
        self.plate.get_well("A1"),
        ImagingMode.BRIGHTFIELD,
        Objective.O_4X_PL_FL,
        exposure_time=auto_exposure,
        focal_height=auto_focus,
        gain=1.0,
      )

    self.assertIs(result, self.backend.results[-1])
    self.assertEqual(exposure_evaluations, 1)
    self.assertGreater(len(self.backend.calls), 1)
    self.assert_capture_lifecycle(events, "completed")
    self.assertEqual(events[0].data["exposure"]["mode"], "software_auto")
    self.assertEqual(events[0].data["focus"]["mode"], "software_auto")

  async def test_backend_failure_emits_failed_and_preserves_exception_identity(self) -> None:
    error = RuntimeError("capture failed")
    self.backend.failure = error
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaises(RuntimeError) as raised:
        await self.imager.capture(
          self.plate.get_well("A1"),
          ImagingMode.BRIGHTFIELD,
          Objective.O_4X_PL_FL,
          exposure_time=10.0,
          focal_height=1.0,
          gain=1.0,
        )

    self.assertIs(raised.exception, error)
    self.assert_capture_lifecycle(events, "failed")
    self.assertEqual(events[1].data["error_type"], "RuntimeError")
    self.assertEqual(events[1].data["error_message"], "capture failed")

  async def test_failure_during_auto_exposure_emits_one_failed_lifecycle(self) -> None:
    error = RuntimeError("evaluation failed")

    async def fail_evaluation(image: object) -> Literal["higher", "lower", "good"]:
      raise error

    auto_exposure = AutoExposure(fail_evaluation, max_rounds=2, low=1.0, high=2.0)
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaises(RuntimeError) as raised:
        await self.imager.capture(
          self.plate.get_well("A1"),
          ImagingMode.BRIGHTFIELD,
          Objective.O_4X_PL_FL,
          exposure_time=auto_exposure,
          focal_height=1.0,
          gain=1.0,
        )

    self.assertIs(raised.exception, error)
    self.assert_capture_lifecycle(events, "failed")
    self.assertEqual(events[1].data["error_type"], "RuntimeError")
    self.assertEqual(events[1].data["error_message"], "evaluation failed")

  async def test_invalid_frontend_request_fails_before_operation_starts(self) -> None:
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaises(TypeError):
        await self.imager.capture(
          self.plate.get_well("A1"),
          ImagingMode.BRIGHTFIELD,
          Objective.O_4X_PL_FL,
          exposure_time=object(),  # type: ignore[arg-type]
        )

    self.assertEqual(events, [])
    self.assertEqual(self.backend.calls, [])

  async def test_metadata_is_not_built_without_an_interested_listener(self) -> None:
    with patch(
      "pylabrobot.legacy.plate_reading.imager.resource_reference",
      side_effect=AssertionError("metadata should be lazy"),
    ):
      first = await self.imager.capture(
        (0, 0),
        ImagingMode.BRIGHTFIELD,
        Objective.O_4X_PL_FL,
        10.0,
        1.0,
        1.0,
      )
      with use_event_bus(EventBus()):
        second = await self.imager.capture(
          (0, 0),
          ImagingMode.BRIGHTFIELD,
          Objective.O_4X_PL_FL,
          10.0,
          1.0,
          1.0,
        )

    self.assertIsInstance(first, ImagingResult)
    self.assertIsInstance(second, ImagingResult)


class ImageReaderEventCompositionTests(unittest.IsolatedAsyncioTestCase):
  async def test_mixed_children_reference_the_loaded_plate(self) -> None:
    backend = _RecordingImageReaderBackend()
    image_reader = ImageReader(
      name="image_reader",
      size_x=1,
      size_y=1,
      size_z=1,
      backend=backend,
    )
    adapter = Resource(name="adapter", size_x=1, size_y=1, size_z=1)
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    image_reader.assign_child_resource(adapter, location=Coordinate.zero())
    image_reader.assign_child_resource(plate, location=Coordinate.zero())
    await image_reader.setup()
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await image_reader.open()
      await image_reader.capture(
        plate.get_well("A1"),
        ImagingMode.BRIGHTFIELD,
        Objective.O_4X_PL_FL,
        10.0,
        1.0,
        1.0,
      )

    self.assertEqual([resource["name"] for resource in events[0].data["resources"]], ["plate"])
    self.assertEqual(events[2].data["plate"]["name"], "plate")

  async def test_inherited_read_and_capture_each_emit_one_lifecycle(self) -> None:
    backend = _RecordingImageReaderBackend()
    image_reader = ImageReader(
      name="image_reader",
      size_x=1,
      size_y=1,
      size_z=1,
      backend=backend,
    )
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    image_reader.assign_child_resource(plate, location=Coordinate.zero())
    await image_reader.setup()
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      records = await image_reader.read_absorbance(
        450,
        [plate.get_well("A1")],
        True,
      )
      image = await image_reader.capture(
        plate.get_well("B2"),
        ImagingMode.BRIGHTFIELD,
        Objective.O_4X_PL_FL,
        10.0,
        1.0,
        1.0,
      )

    self.assertIs(records, backend.records)
    self.assertEqual(len(image.images), 1)
    self.assertEqual(backend.capture_calls, 1)
    self.assertEqual(
      [event.name for event in events],
      [
        "plate_reader.read_absorbance.started",
        "plate_reader.read_absorbance.completed",
        "imager.capture.started",
        "imager.capture.completed",
      ],
    )
