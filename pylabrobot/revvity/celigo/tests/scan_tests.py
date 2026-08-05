"""Tests for Celigo scan specifications, planning, and execution."""

import inspect
import itertools
import math
import unittest
from datetime import timedelta
from types import SimpleNamespace

from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb
from pylabrobot.revvity.celigo.camera import CameraFrame
from pylabrobot.revvity.celigo.config import IlluminationChannelConfig
from pylabrobot.revvity.celigo.navigation import well_to_sample_mm
from pylabrobot.revvity.celigo.scan import (
  Capture,
  FrameResult,
  ScanEstimateModel,
  ScanRegion,
  ScanResult,
  ScanSpec,
  build_scan_plan,
)
from pylabrobot.revvity.celigo.tests.helpers import (
  make_calibration_config,
  make_celigo,
  make_navigation_config,
  make_test_config,
  stub,
)


def _channel(
  name: str,
  *,
  x_correction: float = 1.0,
  y_correction: float = 1.0,
  z_offset_mm: float = 0.0,
) -> IlluminationChannelConfig:
  return IlluminationChannelConfig(
    name=name,
    display_name=name.title(),
    logical_filter=1,
    bit_value=None,
    intensity_percent=0,
    lighting_io_name=name,
    strobe=False,
    z_offset_to_brightfield_mm=z_offset_mm,
    mm_per_pixel_x_correction_to_brightfield=x_correction,
    mm_per_pixel_y_correction_to_brightfield=y_correction,
  )


def _config():
  config = make_test_config()
  config.calibration = make_calibration_config(
    microns_per_pixel_x=1.0,
    microns_per_pixel_y=1.0,
    image_width_pixels=1000,
    image_height_pixels=1000,
  )
  config.navigation = make_navigation_config(
    frame_overlap_x_mm=0.1,
    frame_overlap_y_mm=0.1,
    max_galvo_deflection_x_mm=1.7,
    max_galvo_deflection_y_mm=1.7,
  )
  config.channels_by_magnification[config.magnification] = {
    "brightfield": _channel("brightfield"),
    "green": _channel("green", z_offset_mm=0.2),
  }
  return config


def _frame() -> CameraFrame:
  return CameraFrame(
    data=b"\x00",
    width=1,
    height=1,
    bit_depth=8,
    exposure_ms=1.0,
    gain=1.0,
    captured_at=0.0,
  )


def _overlap_area(first: ScanRegion, second: ScanRegion) -> float:
  width = max(0.0, min(first.right, second.right) - max(first.left, second.left))
  height = max(0.0, min(first.bottom, second.bottom) - max(first.top, second.top))
  return width * height


class TestScanRegion(unittest.TestCase):
  def test_explicit_bounds_and_area(self):
    region = ScanRegion.from_bounds_mm(left=5, top=5, right=122, bottom=81)

    self.assertEqual(region.width_mm, 117)
    self.assertEqual(region.height_mm, 76)
    self.assertEqual(region.area_mm2, 8892)

  def test_invalid_bounds_are_rejected(self):
    with self.assertRaisesRegex(ValueError, "right"):
      ScanRegion.from_bounds_mm(left=5, top=5, right=5, bottom=81)
    with self.assertRaisesRegex(ValueError, "finite"):
      ScanRegion.from_bounds_mm(left=5, top=float("nan"), right=122, bottom=81)


class TestScanSpec(unittest.TestCase):
  def setUp(self):
    self.plate = Cor_96_wellplate_360ul_Fb(name="imaging_plate")

  def test_single_capture_shorthand_is_normalized(self):
    spec = ScanSpec.points(
      [(10, 20)],
      channel="brightfield",
      exposure_ms=1.25,
      gain=2,
      autofocus="image",
    )

    self.assertEqual(
      spec.captures,
      (Capture(channel="brightfield", exposure_ms=1.25, gain=2),),
    )
    self.assertEqual(spec.autofocus, "image")

  def test_capture_list_is_mutually_exclusive_with_shorthand(self):
    capture = Capture(channel="brightfield")
    with self.assertRaisesRegex(ValueError, "cannot be combined"):
      ScanSpec.points(
        [(10, 20)],
        channel="brightfield",
        captures=[capture],
      )
    with self.assertRaisesRegex(ValueError, "provide channel or captures"):
      ScanSpec.points([(10, 20)])

  def test_capture_values_are_validated_early(self):
    with self.assertRaisesRegex(ValueError, "exposure_ms"):
      Capture(channel="brightfield", exposure_ms=0)
    with self.assertRaisesRegex(ValueError, "gain"):
      Capture(channel="brightfield", gain=-1)
    with self.assertRaisesRegex(ValueError, "channel"):
      Capture(channel=" ")

  def test_points_are_anonymous_and_need_no_bounds(self):
    self.assertNotIn("labels", inspect.signature(ScanSpec.points).parameters)
    spec = ScanSpec.points(
      [(250.0, -10.0)],
      block_shape=(2, 3),
      channel="brightfield",
    )
    plan = build_scan_plan(_config(), spec)

    self.assertIsNone(plan.blocks[0].label)
    self.assertAlmostEqual(plan.blocks[0].center_x_mm, 250.0)
    self.assertAlmostEqual(plan.blocks[0].center_y_mm, -10.0)

  def test_wells_are_normalized_and_converted_once(self):
    spec = ScanSpec.wells(
      self.plate,
      ["a1", " B2 "],
      block_shape=(2, 3),
      channel="brightfield",
    )
    plan = build_scan_plan(_config(), spec)

    self.assertFalse(hasattr(spec.geometry, "plate"))
    self.assertEqual([block.label for block in plan.blocks], ["A1", "B2"])
    expected_x_mm, expected_y_mm = well_to_sample_mm(self.plate, "A1")
    self.assertAlmostEqual(plan.blocks[0].center_x_mm, expected_x_mm)
    self.assertAlmostEqual(plan.blocks[0].center_y_mm, expected_y_mm)

  def test_block_shape_may_be_smaller_than_the_calibrated_maximum(self):
    for block_shape in ((1, 1), (2, 3), (4, 4)):
      with self.subTest(block_shape=block_shape):
        plan = build_scan_plan(
          _config(),
          ScanSpec.points(
            [(10, 10)],
            block_shape=block_shape,
            channel="brightfield",
          ),
        )
        self.assertEqual(plan.blocks[0].block_shape, block_shape)
        self.assertEqual(plan.frame_count, block_shape[0] * block_shape[1])

  def test_block_shape_cannot_exceed_galvo_reach(self):
    spec = ScanSpec.points(
      [(10, 10)],
      block_shape=(5, 4),
      channel="brightfield",
    )
    with self.assertRaisesRegex(ValueError, "calibrated galvo limit is 4x4"):
      build_scan_plan(_config(), spec)


class TestScanPlanning(unittest.TestCase):
  def setUp(self):
    self.config = _config()
    self.bounds = ScanRegion.from_bounds_mm(left=0, top=0, right=20, bottom=20)

  def test_random_blocks_are_seeded_distinct_and_non_overlapping(self):
    spec = ScanSpec.random(
      self.bounds,
      count=10,
      block_shape=(4, 4),
      seed=42,
      channel="brightfield",
    )
    first = build_scan_plan(self.config, spec)
    second = build_scan_plan(self.config, spec)
    other = build_scan_plan(
      self.config,
      ScanSpec.random(
        self.bounds,
        count=10,
        block_shape=(4, 4),
        seed=43,
        channel="brightfield",
      ),
    )

    centers = [(block.center_x_mm, block.center_y_mm) for block in first.blocks]
    self.assertEqual(
      centers,
      [(block.center_x_mm, block.center_y_mm) for block in second.blocks],
    )
    self.assertNotEqual(
      centers,
      [(block.center_x_mm, block.center_y_mm) for block in other.blocks],
    )
    for index, block in enumerate(first.blocks):
      for other_block in first.blocks[index + 1 :]:
        self.assertAlmostEqual(_overlap_area(block.bounds, other_block.bounds), 0.0)

  def test_random_blocks_use_the_minimum_travel_order(self):
    plan = build_scan_plan(
      self.config,
      ScanSpec.random(
        self.bounds,
        count=5,
        block_shape=(4, 4),
        seed=42,
        channel="brightfield",
      ),
    )
    centers = [(block.center_x_mm, block.center_y_mm) for block in plan.blocks]

    route_length = sum(math.dist(start, end) for start, end in zip(centers, centers[1:]))
    minimum_length = min(
      sum(math.dist(start, end) for start, end in zip(order, order[1:]))
      for order in itertools.permutations(centers)
    )

    self.assertAlmostEqual(route_length, minimum_length)

  def test_too_many_non_overlapping_blocks_is_rejected(self):
    spec = ScanSpec.random(
      self.bounds,
      count=26,
      block_shape=(4, 4),
      channel="brightfield",
    )
    with self.assertRaisesRegex(ValueError, "only 25"):
      build_scan_plan(self.config, spec)

  def test_full_coverage_uses_as_many_blocks_as_needed(self):
    bounds = ScanRegion.from_bounds_mm(left=0, top=0, right=10, bottom=10)
    plan = build_scan_plan(
      self.config,
      ScanSpec.full_coverage(bounds, channel="brightfield"),
    )

    self.assertEqual(plan.frame_count, 169)
    self.assertEqual(plan.stage_position_count, 16)
    self.assertAlmostEqual(plan.sampled_area_mm2, bounds.area_mm2)
    self.assertAlmostEqual(plan.frames[0].position.sample_x_mm, 0.5)
    self.assertAlmostEqual(plan.blocks[-1].bounds.bottom, 10.0)

  def test_multiple_captures_share_each_stage_block(self):
    spec = ScanSpec.points(
      [(5, 5), (15, 15)],
      block_shape=(2, 3),
      captures=[
        Capture(channel="brightfield", exposure_ms=10, gain=1),
        Capture(channel="green", exposure_ms=20, gain=2),
      ],
      autofocus="image",
    )
    plan = build_scan_plan(self.config, spec)

    self.assertEqual(plan.stage_position_count, 2)
    self.assertEqual(plan.frame_count, 24)
    self.assertEqual(
      [frame.capture.channel for frame in plan.frames],
      ["brightfield"] * 6 + ["green"] * 6 + ["brightfield"] * 6 + ["green"] * 6,
    )
    self.assertTrue(all(frame.block is plan.blocks[0] for frame in plan.frames[:12]))
    self.assertTrue(all(frame.block is plan.blocks[1] for frame in plan.frames[12:]))

  def test_estimates_include_capture_exposure_and_autofocus(self):
    spec = ScanSpec.points(
      [(5, 5), (15, 15)],
      block_shape=(2, 3),
      captures=[
        Capture(channel="brightfield", exposure_ms=10),
        Capture(channel="green", exposure_ms=20),
      ],
      autofocus="image",
    )
    plan = build_scan_plan(
      self.config,
      spec,
      estimate_model=ScanEstimateModel(
        seconds_per_frame=1,
        seconds_per_stage_position=2,
        seconds_per_autofocus=3,
        bytes_per_pixel=2,
      ),
    )

    self.assertEqual(plan.frame_count, 24)
    self.assertEqual(plan.autofocus_count, 2)
    self.assertEqual(plan.estimated_duration, timedelta(seconds=34.36))
    self.assertEqual(plan.estimated_storage_bytes, 48_000_000)
    summary = str(plan)
    self.assertIn("geometry: points(count=2, block_shape=2x3)", summary)
    self.assertIn("channels: brightfield, green", summary)
    self.assertIn("frames: 24", summary)
    self.assertIn("estimated storage: 48 MB", summary)

  def test_unknown_channel_is_rejected_during_planning(self):
    spec = ScanSpec.points([(5, 5)], channel="ultraviolet")
    with self.assertRaisesRegex(ValueError, "Unknown channel 'ultraviolet'"):
      build_scan_plan(self.config, spec)


class TestCeligoScanMethods(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.celigo = make_celigo(config=_config())
    self.plate = Cor_96_wellplate_360ul_Fb(name="imaging_plate")

  async def test_execute_runs_the_exact_plan_and_moves_stage_once_per_block(self):
    spec = ScanSpec.wells(
      self.plate,
      ["A1", "B2"],
      block_shape=(2, 3),
      captures=[
        Capture(channel="brightfield", exposure_ms=1.25, gain=1),
        Capture(channel="green", exposure_ms=2.5, gain=2),
      ],
      autofocus="image",
    )
    plan = self.celigo.plan(spec)
    stage_moves = []
    acquisition_calls = []

    async def move_to_scan_block(block):
      stage_moves.append(block)
      return block.stage_x_mm + 0.01, block.stage_y_mm + 0.02

    async def acquire_scan_position(
      position,
      label,
      channel,
      exposure_ms,
      gain,
      autofocus,
      z_mm,
      settled_stage_position_mm,
    ):
      acquisition_calls.append(
        {
          "position": position,
          "label": label,
          "channel": channel,
          "exposure_ms": exposure_ms,
          "gain": gain,
          "autofocus": autofocus,
          "z_mm": z_mm,
          "stage": settled_stage_position_mm,
        }
      )
      return SimpleNamespace(
        frame=_frame(),
        x_mm=settled_stage_position_mm[0],
        y_mm=settled_stage_position_mm[1],
        z_mm=1.0,
        galvo_hardware_voltages=(0.1, 0.2),
        focus=None,
      )

    stub(
      self.celigo,
      _move_to_scan_block=move_to_scan_block,
      _acquire_scan_position=acquire_scan_position,
    )
    result = await self.celigo.execute(plan)

    self.assertIsInstance(result, ScanResult)
    self.assertIs(result.plan, plan)
    self.assertEqual(stage_moves, list(plan.blocks))
    self.assertEqual(len(acquisition_calls), 24)
    self.assertEqual(
      [call["label"] for call in acquisition_calls],
      ["A1"] * 12 + ["B2"] * 12,
    )
    self.assertEqual(
      [call["channel"] for call in acquisition_calls],
      [frame.capture.channel for frame in plan.frames],
    )
    self.assertEqual(
      [call["exposure_ms"] for call in acquisition_calls],
      [frame.capture.exposure_ms for frame in plan.frames],
    )
    self.assertEqual(
      [call["autofocus"] for call in acquisition_calls],
      ["image"] + [None] * 11 + ["image"] + [None] * 11,
    )
    self.assertTrue(
      all(isinstance(frame_result, FrameResult) for frame_result in result.frames)
    )
    self.assertTrue(
      all(
        frame_result.planned is planned
        for frame_result, planned in zip(result.frames, plan.frames)
      )
    )

  async def test_internal_execution_reports_frames_and_accepts_a_tuned_focus_seed(self):
    plan = self.celigo.plan(
      ScanSpec.points(
        [(25, 20)],
        captures=[Capture(channel="brightfield"), Capture(channel="green")],
      )
    )
    z_targets = []
    reported = []

    async def move_to_scan_block(block):
      return block.stage_x_mm, block.stage_y_mm

    async def acquire_scan_position(**kwargs):
      z_targets.append(kwargs["z_mm"])
      return SimpleNamespace(
        frame=_frame(),
        x_mm=1.0,
        y_mm=2.0,
        z_mm=kwargs["z_mm"],
        galvo_hardware_voltages=(0.1, 0.2),
        focus=None,
      )

    async def on_frame(frame):
      reported.append(frame)

    stub(
      self.celigo,
      _move_to_scan_block=move_to_scan_block,
      _acquire_scan_position=acquire_scan_position,
    )
    result = await self.celigo._execute_scan_plan(
      plan,
      on_frame=on_frame,
      initial_brightfield_z_mm=2.5,
    )

    self.assertEqual(z_targets, [2.5, 2.7])
    self.assertEqual(reported, list(result.frames))

  async def test_scan_is_plan_followed_by_execute(self):
    spec = ScanSpec.points([(5, 5)], channel="brightfield")
    compiled_plan = object()
    expected_result = object()
    planning_calls = []
    execution_calls = []

    def plan(actual_spec, *, estimate_model=None):
      planning_calls.append((actual_spec, estimate_model))
      return compiled_plan

    async def execute(actual_plan):
      execution_calls.append(actual_plan)
      return expected_result

    stub(self.celigo, plan=plan, execute=execute)
    estimates = ScanEstimateModel(seconds_per_frame=1)
    result = await self.celigo.scan(spec, estimate_model=estimates)

    self.assertIs(result, expected_result)
    self.assertEqual(planning_calls, [(spec, estimates)])
    self.assertEqual(execution_calls, [compiled_plan])

  async def test_scan_wells_builds_the_single_capture_spec(self):
    expected_result = object()
    scan_calls = []

    async def scan(spec, *, estimate_model=None):
      scan_calls.append((spec, estimate_model))
      return expected_result

    stub(self.celigo, scan=scan)
    estimates = ScanEstimateModel()
    result = await self.celigo.scan_wells(
      self.plate,
      ["a1", "B2"],
      channel="brightfield",
      block_shape=(2, 3),
      exposure_ms=1.25,
      gain=2,
      autofocus="image",
      estimate_model=estimates,
    )

    self.assertIs(result, expected_result)
    spec, actual_estimates = scan_calls[0]
    self.assertIsInstance(spec, ScanSpec)
    self.assertEqual(
      spec.captures,
      (Capture(channel="brightfield", exposure_ms=1.25, gain=2),),
    )
    self.assertEqual(spec.autofocus, "image")
    self.assertIs(actual_estimates, estimates)
    planned = build_scan_plan(_config(), spec)
    self.assertEqual([block.label for block in planned.blocks], ["A1", "B2"])
    self.assertEqual([block.block_shape for block in planned.blocks], [(2, 3), (2, 3)])


if __name__ == "__main__":
  unittest.main()
