"""Exercise the hello-world prompts with a fake reader, without opening a serial port."""

import ast
import asyncio
import inspect
import json
import unittest
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

from pylabrobot.molecular_devices.filtermax import (
  FilterMaxF5,
  FilterMaxReadCancelled,
  PlateGeometry,
)

NOTEBOOK = (
  Path(__file__).resolve().parents[4]
  / "docs/user_guide/molecular_devices/filtermax/hello-world.ipynb"
)


class HelloWorldTests(unittest.IsolatedAsyncioTestCase):
  """Check measurement opt-in and loading gates in the runnable notebook cells."""

  async def run_notebook(
    self,
    answers: list[str],
    well_selections: Optional[dict[str, list[str]]] = None,
    reader: Optional[AsyncMock] = None,
  ) -> AsyncMock:
    """Execute the connected walkthrough with scripted input and mocked hardware."""
    if reader is None:
      reader = AsyncMock(spec=FilterMaxF5)
    cancelled = asyncio.Event()
    real_sleep = asyncio.sleep

    async def read_absorbance(plate: PlateGeometry, **kwargs: Any) -> list:
      """Keep the cancellation example active until it receives a cancel request."""
      kinetic = kwargs.get("kinetic")
      if kinetic is not None and kinetic.reads == 10:
        await cancelled.wait()
        raise FilterMaxReadCancelled(140, "Cancelled", "read")
      return []

    async def skip_sleep(seconds: float) -> None:
      """Yield to the read task without the demonstration's two-second delay."""
      await real_sleep(0)

    reader.read_absorbance.side_effect = read_absorbance
    reader.cancel_read.side_effect = cancelled.set
    namespace = {"reader": reader}
    notebook = json.loads(NOTEBOOK.read_text())
    connected = False
    with (
      patch("builtins.input", side_effect=answers) as prompt,
      patch("builtins.print"),
      patch("asyncio.sleep", side_effect=skip_sleep),
    ):
      for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
          continue
        source = "".join(cell["source"])
        compiled = compile(
          source, f"{NOTEBOOK}:{cell['id']}", "exec", ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        )
        connected = connected or cell["id"] == "connect"
        if connected:
          result = eval(compiled, namespace)
          if inspect.isawaitable(result):
            await result
          if cell["id"] == "measurement-wells-command" and well_selections is not None:
            namespace["measurement_wells"].update(well_selections)
      self.assertEqual(prompt.call_count, len(answers))

    reader.setup.assert_awaited_once()
    reader.get_instrument_info.assert_awaited_once()
    reader.get_status.assert_awaited_once()
    reader.get_filter_slides.assert_awaited_once()
    reader.set_temperature.assert_awaited_once_with(25.0)
    reader.deactivate_temperature_control.assert_awaited_once()
    reader.start_shaking.assert_awaited_once_with(pattern="linear", speed="medium", duration=5)
    reader.get_error_log.assert_awaited_once()
    reader.clear_error_log.assert_awaited_once()
    reader.stop.assert_awaited_once()
    return reader

  async def test_default_skips_measurements(self) -> None:
    """Empty opt-in runs the default controls without extra tray motions or reads."""
    reader = await self.run_notebook(["yes", ""])
    reader.read_absorbance.assert_not_awaited()
    reader.read_luminescence.assert_not_awaited()
    reader.cancel_read.assert_not_awaited()
    reader.move_plate_tray_out.assert_awaited_once()
    reader.move_plate_tray_in.assert_awaited_once()

  async def test_each_example_runs_independently(self) -> None:
    """Each example works even when all earlier measurement examples were skipped."""
    for index in range(7):
      with self.subTest(example=index):
        selections = ["yes" if choice == index else "" for choice in range(7)]
        examples = ["endpoint", "partial", "reference", "kinetic", "scan", "luminescence", "cancel"]
        reader = await self.run_notebook(
          ["yes", "yes", *selections, "yes", "yes"],
          {examples[index]: [" c4 ", "C5", "c4"]},
        )
        if index == 5:
          reader.read_absorbance.assert_not_awaited()
          reader.read_luminescence.assert_awaited_once()
          measurement = reader.read_luminescence
        else:
          reader.read_absorbance.assert_awaited_once()
          reader.read_luminescence.assert_not_awaited()
          measurement = reader.read_absorbance
        if index == 0:
          self.assertNotIn("wells", measurement.call_args.kwargs)
        else:
          self.assertEqual(measurement.call_args.kwargs["wells"], ["C4", "C5"])
        if index == 6:
          reader.cancel_read.assert_awaited_once()
        else:
          reader.cancel_read.assert_not_awaited()
        self.assertEqual(reader.move_plate_tray_out.await_count, 2)
        self.assertEqual(reader.move_plate_tray_in.await_count, 2)
        operations = [entry[0] for entry in reader.mock_calls]
        openings = [i for i, name in enumerate(operations) if name == "move_plate_tray_out"]
        closings = [i for i, name in enumerate(operations) if name == "move_plate_tray_in"]
        read_name = "read_luminescence" if index == 5 else "read_absorbance"
        self.assertLess(openings[1], closings[1])
        self.assertLess(closings[1], operations.index(read_name))

  async def test_no_examples_selected(self) -> None:
    """Opt-in alone does not open the loading tray or start measurements."""
    reader = await self.run_notebook(["yes", "yes", *([""] * 7)])
    reader.move_plate_tray_out.assert_awaited_once()
    reader.read_absorbance.assert_not_awaited()
    reader.read_luminescence.assert_not_awaited()

  async def test_declining_plate_loading_skips_all_reads(self) -> None:
    """No selected example can run or close the loading tray without confirmation."""
    reader = await self.run_notebook(["yes", "yes", *(["yes"] * 7), ""])
    self.assertEqual(reader.move_plate_tray_out.await_count, 2)
    reader.move_plate_tray_in.assert_awaited_once()
    reader.read_absorbance.assert_not_awaited()
    reader.read_luminescence.assert_not_awaited()
    reader.cancel_read.assert_not_awaited()

  async def test_declining_example_readiness_skips_all_reads(self) -> None:
    """A loaded plate alone does not authorize any of the selected examples."""
    reader = await self.run_notebook(["yes", "yes", *(["yes"] * 7), "yes", *([""] * 7)])
    reader.read_absorbance.assert_not_awaited()
    reader.read_luminescence.assert_not_awaited()
    reader.cancel_read.assert_not_awaited()

  async def test_multiple_examples_use_configured_wells(self) -> None:
    """Use separate edited well lists without asking for well input."""
    reader = await self.run_notebook(
      ["yes", "yes", "", "yes", "", "", "", "yes", "", "yes", "yes", "yes"],
      {"partial": ["c4", "c5", "C4"], "luminescence": ["d6"]},
    )
    reader.read_absorbance.assert_awaited_once()
    reader.read_luminescence.assert_awaited_once()
    self.assertEqual(reader.read_absorbance.call_args.kwargs["wells"], ["C4", "C5"])
    self.assertEqual(reader.read_luminescence.call_args.kwargs["wells"], ["D6"])

  async def test_invalid_wells_stop_without_reading_or_reprompting(self) -> None:
    """Empty and invalid settings fail before a read instead of looping on stdin."""
    for wells in ([], [""], ["A0"], ["A13"], ["I1"], ["C4", ""], ["C4, C5"]):
      with self.subTest(wells=wells):
        reader = AsyncMock(spec=FilterMaxF5)
        with self.assertRaisesRegex(ValueError, "measurement_wells"):
          await self.run_notebook(
            ["yes", "yes", "", "yes", "", "", "", "", "", "yes", "yes"],
            {"partial": wells},
            reader,
          )
        reader.read_absorbance.assert_not_awaited()
        reader.read_luminescence.assert_not_awaited()
        reader.cancel_read.assert_not_awaited()
