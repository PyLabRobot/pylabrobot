import json
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend
from pylabrobot.legacy.plate_reading.plate_reader import PlateReader
from pylabrobot.resources import Coordinate, Plate, Well
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb


class _RecordingPlateReaderBackend(PlateReaderBackend):
  """Small complete backend that preserves opaque results and records forwarded arguments."""

  def __init__(self) -> None:
    self.records: List[Dict] = [{"data": [[object()]], "opaque": object()}]
    self.failure: Optional[BaseException] = None
    self.calls: list[tuple[str, dict[str, Any]]] = []

  def _record(self, operation: str, **arguments: Any) -> None:
    self.calls.append((operation, arguments))
    if self.failure is not None:
      raise self.failure

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def open(self, **backend_kwargs: Any) -> None:
    self._record("open", backend_kwargs=backend_kwargs)

  async def close(self, plate: Optional[Plate], **backend_kwargs: Any) -> None:
    self._record("close", plate=plate, backend_kwargs=backend_kwargs)

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float,
    **backend_kwargs: Any,
  ) -> List[Dict]:
    self._record(
      "read_luminescence",
      plate=plate,
      wells=wells,
      focal_height=focal_height,
      backend_kwargs=backend_kwargs,
    )
    return self.records

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    **backend_kwargs: Any,
  ) -> List[Dict]:
    self._record(
      "read_absorbance",
      plate=plate,
      wells=wells,
      wavelength=wavelength,
      backend_kwargs=backend_kwargs,
    )
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
    self._record(
      "read_fluorescence",
      plate=plate,
      wells=wells,
      excitation_wavelength=excitation_wavelength,
      emission_wavelength=emission_wavelength,
      focal_height=focal_height,
      backend_kwargs=backend_kwargs,
    )
    return self.records


class PlateReaderEventTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.backend = _RecordingPlateReaderBackend()
    self.reader = PlateReader(
      name="reader",
      size_x=1,
      size_y=1,
      size_z=1,
      backend=self.backend,
    )
    self.plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.reader.assign_child_resource(self.plate, location=Coordinate.zero())
    await self.reader.setup()

  @staticmethod
  def _assert_correlated_pairs(events: list[PLREvent]) -> None:
    for started, terminal in zip(events[::2], events[1::2]):
      assert started.name.endswith(".started")
      assert terminal.name.removesuffix(".completed").removesuffix(
        ".failed"
      ) == started.name.removesuffix(".started")
      assert started.context["operation_id"] == terminal.context["operation_id"]

  async def test_public_operations_emit_bounded_correlated_success_lifecycles(self) -> None:
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)
    wells = [self.plate.get_well("A1"), self.plate.get_well("B2")]

    with use_event_bus(event_bus):
      await self.reader.open(slow=True)
      await self.reader.close(slow=True)
      luminescence = await self.reader.read_luminescence(
        4.5,
        wells,
        True,
        vendor_flag="lum",
      )
      absorbance = await self.reader.read_absorbance(
        wavelength=450,
        wells=wells[:1],
        use_new_return_type=True,
        vendor_flag="abs",
      )
      fluorescence = await self.reader.read_fluorescence(
        485,
        528,
        6.0,
        wells,
        True,
        vendor_flag="flu",
      )

    self.assertIs(luminescence, self.backend.records)
    self.assertIs(absorbance, self.backend.records)
    self.assertIs(fluorescence, self.backend.records)
    self.assertEqual(
      [event.name for event in events],
      [
        "plate_reader.open.started",
        "plate_reader.open.completed",
        "plate_reader.close.started",
        "plate_reader.close.completed",
        "plate_reader.read_luminescence.started",
        "plate_reader.read_luminescence.completed",
        "plate_reader.read_absorbance.started",
        "plate_reader.read_absorbance.completed",
        "plate_reader.read_fluorescence.started",
        "plate_reader.read_fluorescence.completed",
      ],
    )
    self._assert_correlated_pairs(events)

    self.assertEqual(events[0].data["device"]["name"], "reader")
    self.assertEqual([resource["name"] for resource in events[0].data["resources"]], ["plate"])
    luminescence_started, luminescence_completed = events[4:6]
    self.assertEqual(
      [resource["name"] for resource in luminescence_started.data["resources"]],
      [well.name for well in wells],
    )
    self.assertEqual(luminescence_started.data["resources"][0]["ancestors"][0]["name"], "plate")
    self.assertEqual(luminescence_started.data["well_count"], 2)
    self.assertEqual(luminescence_started.data["return_format"], "records")
    self.assertEqual(luminescence_started.data["focal_height"], 4.5)
    self.assertNotIn("record_count", luminescence_started.data)
    self.assertEqual(luminescence_completed.data["record_count"], 1)

    self.assertEqual(events[6].data["wavelength_nm"], 450)
    self.assertEqual(events[8].data["excitation_wavelength_nm"], 485)
    self.assertEqual(events[8].data["emission_wavelength_nm"], 528)
    self.assertEqual(events[8].data["focal_height"], 6.0)
    for event in events:
      json.dumps(event.as_dict(), allow_nan=False)
      self.assertNotIn("backend_kwargs", event.data)
      self.assertNotIn("result", event.data)
      self.assertNotIn("measurements", event.data)

    self.assertEqual(self.backend.calls[0][1]["backend_kwargs"], {"slow": True})
    self.assertEqual(self.backend.calls[2][1]["backend_kwargs"], {"vendor_flag": "lum"})

  async def test_open_and_close_without_a_plate_emit_empty_resources(self) -> None:
    self.plate.unassign()
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.reader.open()
      await self.reader.close()

    self.assertEqual(events[0].data["resources"], [])
    self.assertEqual(events[2].data["resources"], [])

  async def test_legacy_projection_and_well_fallbacks_are_preserved(self) -> None:
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      none_result = await self.reader.read_luminescence(4.0, None, False)
      empty_result = await self.reader.read_luminescence(4.0, [], False)

    self.assertIs(none_result, self.backend.records[0]["data"])
    self.assertIs(empty_result, self.backend.records[0]["data"])
    self.assertEqual(self.backend.calls[0][1]["wells"], self.plate.get_all_items())
    self.assertEqual(self.backend.calls[1][1]["wells"], self.plate.get_all_items())
    self.assertEqual(events[0].data["return_format"], "legacy_matrix")
    self.assertEqual(events[0].data["well_count"], self.plate.num_items)
    self.assertEqual(events[1].data["record_count"], 1)
    self.assertEqual(events[2].data["well_count"], self.plate.num_items)

  async def test_backend_failure_emits_failed_and_preserves_exception_identity(self) -> None:
    error = RuntimeError("reader failed")
    self.backend.failure = error
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaises(RuntimeError) as raised:
        await self.reader.read_absorbance(450, [self.plate.get_well("A1")], True)

    self.assertIs(raised.exception, error)
    self.assertEqual(
      [event.name for event in events],
      ["plate_reader.read_absorbance.started", "plate_reader.read_absorbance.failed"],
    )
    self._assert_correlated_pairs(events)
    self.assertEqual(events[1].data["error_type"], "RuntimeError")
    self.assertEqual(events[1].data["error_message"], "reader failed")

  async def test_legacy_projection_failure_is_not_reported_as_completed(self) -> None:
    self.backend.records = []
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaises(IndexError):
        await self.reader.read_luminescence(4.0)

    self.assertEqual(
      [event.name for event in events],
      ["plate_reader.read_luminescence.started", "plate_reader.read_luminescence.failed"],
    )

  async def test_metadata_is_not_built_without_an_interested_listener(self) -> None:
    with patch(
      "pylabrobot.legacy.plate_reading.plate_reader.resource_reference",
      side_effect=AssertionError("metadata should be lazy"),
    ):
      first = await self.reader.read_absorbance(450, use_new_return_type=True)
      with use_event_bus(EventBus()):
        second = await self.reader.read_absorbance(450, use_new_return_type=True)

    self.assertIs(first, self.backend.records)
    self.assertIs(second, self.backend.records)

  async def test_listener_failure_does_not_change_reader_result(self) -> None:
    events: list[PLREvent] = []
    event_bus = EventBus()

    def failing_listener(event: PLREvent) -> None:
      raise RuntimeError(f"listener rejected {event.name}")

    event_bus.subscribe(failing_listener)
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      result = await self.reader.read_absorbance(450, use_new_return_type=True)

    self.assertIs(result, self.backend.records)
    self.assertEqual(len(events), 2)
