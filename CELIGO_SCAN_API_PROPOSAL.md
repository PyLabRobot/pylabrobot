# Celigo scan API proposal

## 1. Scan wells directly

The common one-channel case needs no scan-specific objects:

```python
result = await celigo.scan_wells(
  plate,
  ["A1", "B2"],
  channel="brightfield",
  block_shape=(2, 3),
  exposure_ms=1.0,
  gain=1.0,
  autofocus="image",
)
```

`block_shape=(columns, rows)` is the galvo grid captured from one stationary coarse-stage
position. It must fit within the calibrated galvo reach.

## 2. Build a reusable specification

Use `ScanSpec` for inspection, reuse, multichannel work, or non-well geometry.

```python
# Wells
well_spec = ScanSpec.wells(
  plate,
  ["A1", "B2"],
  block_shape=(2, 3),
  channel="brightfield",
  exposure_ms=1.0,
  gain=1.0,
  autofocus="image",
)

# Physical points
point_spec = ScanSpec.points(
  centers_mm=[(25.0, 20.0), (63.5, 43.0)],
  block_shape=(4, 4),
  channel="brightfield",
)

# Reproducible random blocks inside physical bounds
random_spec = ScanSpec.random(
  bounds=ScanRegion.from_bounds_mm(left=5, top=5, right=122, bottom=81),
  count=10,
  block_shape=(4, 4),
  seed=42,
  non_overlapping=True,
  channel="brightfield",
)

# Complete physical coverage; the planner creates as many stage blocks as needed
coverage_spec = ScanSpec.full_coverage(
  bounds=ScanRegion.from_bounds_mm(left=5, top=5, right=122, bottom=81),
  channel="brightfield",
)
```

`ScanSpec.wells()` converts well names once into labeled sample-relative coordinates.
The resulting specification does not retain or depend on the `Plate`.

`ScanSpec.points()` needs no enclosing bounds. Each center and `block_shape` fully
defines its footprint.

## 3. One capture or many

For one capture, use the shorthand accepted by every `ScanSpec` constructor:

```python
channel="brightfield"
exposure_ms=1.0
gain=1.0
```

For multiple captures, use typed `Capture` values:

```python
spec = ScanSpec.wells(
  plate,
  ["A1", "B2"],
  block_shape=(2, 3),
  captures=[
    Capture(channel="brightfield", exposure_ms=1.0, gain=1.0),
    Capture(channel="green", exposure_ms=20.0, gain=2.0),
  ],
  autofocus="image",
)
```

`channel=...` and `captures=...` are mutually exclusive. The shorthand is normalized
into one `Capture` when the specification is created.

## 4. Plan, execute, or do both

```python
# Plan and execute immediately
result = await celigo.scan(spec)

# Inspect before executing
plan = celigo.plan(spec)
print(plan)
result = await celigo.execute(plan)
```

```text
ScanSpec ── plan() ──> ScanPlan ── execute() ──> ScanResult
   └──────────────── scan() ────────────────────────┘
```

- `plan(spec)` is offline.
- `execute(plan)` performs exactly the compiled operations.
- `scan(spec)` is `await celigo.execute(celigo.plan(spec))`.
- `scan_wells(...)` is thin sugar over `ScanSpec.wells(...)` and `scan(spec)`.

`execute()` accepts no channel, exposure, gain, autofocus, or geometry overrides.

## 5. Public values

Existing driver types retained by this proposal:

```python
Celigo
Plate
CameraFrame
FocusResult
ScanBlock
ScanEstimateModel
ScanPosition
ScanRegion
```

Complete definitions of the new scan values:

```python
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Sequence


CoordinateMM = tuple[float, float]
BlockShape = tuple[int, int]
AutofocusMethod = Literal["image", "hardware"]


@dataclass(frozen=True)
class Capture:
  channel: str
  exposure_ms: float | None = None
  gain: float | None = None


@dataclass(frozen=True)
class _PointGeometry:
  centers_mm: tuple[CoordinateMM, ...]
  labels: tuple[str | None, ...]
  block_shape: BlockShape


@dataclass(frozen=True)
class _RandomGeometry:
  bounds: ScanRegion
  count: int
  block_shape: BlockShape
  seed: int
  non_overlapping: bool


@dataclass(frozen=True)
class _FullCoverageGeometry:
  bounds: ScanRegion


_ScanGeometry = _PointGeometry | _RandomGeometry | _FullCoverageGeometry


@dataclass(frozen=True)
class ScanSpec:
  geometry: _ScanGeometry
  captures: tuple[Capture, ...]
  autofocus: AutofocusMethod | None

  @classmethod
  def wells(
    cls,
    plate: Plate,
    wells: Sequence[str],
    *,
    block_shape: BlockShape = (1, 1),
    channel: str | None = None,
    exposure_ms: float | None = None,
    gain: float | None = None,
    captures: Sequence[Capture] | None = None,
    autofocus: AutofocusMethod | None = None,
  ) -> "ScanSpec": ...

  @classmethod
  def points(
    cls,
    centers_mm: Sequence[CoordinateMM],
    *,
    block_shape: BlockShape = (1, 1),
    labels: Sequence[str] | None = None,
    channel: str | None = None,
    exposure_ms: float | None = None,
    gain: float | None = None,
    captures: Sequence[Capture] | None = None,
    autofocus: AutofocusMethod | None = None,
  ) -> "ScanSpec": ...

  @classmethod
  def random(
    cls,
    bounds: ScanRegion,
    *,
    count: int,
    block_shape: BlockShape,
    seed: int = 0,
    non_overlapping: bool = True,
    channel: str | None = None,
    exposure_ms: float | None = None,
    gain: float | None = None,
    captures: Sequence[Capture] | None = None,
    autofocus: AutofocusMethod | None = None,
  ) -> "ScanSpec": ...

  @classmethod
  def full_coverage(
    cls,
    bounds: ScanRegion,
    *,
    channel: str | None = None,
    exposure_ms: float | None = None,
    gain: float | None = None,
    captures: Sequence[Capture] | None = None,
    autofocus: AutofocusMethod | None = None,
  ) -> "ScanSpec": ...


@dataclass(frozen=True)
class PlannedFrame:
  index: int
  block: ScanBlock
  position: ScanPosition
  capture: Capture


@dataclass(frozen=True)
class ScanPlan:
  spec: ScanSpec
  blocks: tuple[ScanBlock, ...]
  frames: tuple[PlannedFrame, ...]
  estimate_model: ScanEstimateModel
  frame_count: int
  stage_position_count: int
  autofocus_count: int
  sampled_area_mm2: float
  estimated_duration: timedelta
  estimated_storage_bytes: int


@dataclass(frozen=True)
class FrameResult:
  planned: PlannedFrame
  frame: CameraFrame
  actual_stage_mm: CoordinateMM
  actual_z_mm: float
  galvo_hardware_voltages: tuple[float, float]
  focus: FocusResult | None


@dataclass(frozen=True)
class ScanResult:
  plan: ScanPlan
  frames: tuple[FrameResult, ...]
  elapsed: timedelta
```

The underscored geometry values are normalized implementation details, not additional
user-facing concepts. `ScanSpec` is created by `wells()`, `points()`, `random()`, or
`full_coverage()`.

## 6. Celigo methods

```python
class Celigo:
  def plan(
    self,
    spec: ScanSpec,
    *,
    estimate_model: ScanEstimateModel | None = None,
  ) -> ScanPlan: ...

  async def execute(self, plan: ScanPlan) -> ScanResult: ...

  async def scan(
    self,
    spec: ScanSpec,
    *,
    estimate_model: ScanEstimateModel | None = None,
  ) -> ScanResult: ...

  async def scan_wells(
    self,
    plate: Plate,
    wells: Sequence[str],
    *,
    channel: str,
    block_shape: BlockShape = (1, 1),
    exposure_ms: float | None = None,
    gain: float | None = None,
    autofocus: AutofocusMethod | None = None,
    estimate_model: ScanEstimateModel | None = None,
  ) -> ScanResult: ...
```

## 7. Contracts

- Public coordinates are sample-relative millimeters from a top-left origin.
- A `ScanSpec` contains all geometry, captures, and autofocus policy.
- A `ScanPlan` contains the exact ordered and validated hardware operations.
- Plans validate stage limits, galvo reach, channel calibration, and camera geometry.
- Random specifications are reproducible from their seed.
- Multichannel plans move the coarse stage once per block.
- Every returned frame links to its exact `PlannedFrame`.
- Illumination is extinguished after each capture and on every failure path.

## 8. Remove from the current draft

```text
ScanSelection
FullCoverage
RandomRegions
StratifiedRegions
ExplicitRegions
plan_scan()
acquire_scan()
acquire_selection()
```
