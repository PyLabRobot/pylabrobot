# Celigo

A direct PyLabRobot driver for the Revvity Celigo image cytometer (formerly sold by
Nexcelom/Cyntellect). `Celigo` is the main entry point and owns its camera, galvo, and
laser components. It talks to the FTDI USB-IO controller without requiring the Celigo
application.

## Major components

| Component | Role |
|---|---|
| `Celigo` | Top-level coordinator. Owns connection lifecycle, hardware components, illumination, acquisition, autofocus, scanning, and diagnostics. |
| `CeligoConfig` | Complete per-instrument configuration assembled from the vendor files, including motor limits, optical calibration, navigation geometry, and channel recipes. |
| FTDI transport and `MotorController` | Carry controller-board commands and tunneled EZStepper commands. |
| `LinearAxis`, `Axis`, and `FilterWheel` | Represent the configured stage, focus, and optical mechanisms. `Celigo` constructs and owns these objects from `CeligoConfig`. |
| `CeligoCamera` and `CameraFrame` | Manage the Lumenera camera lifecycle and return calibrated, dependency-free monochrome frames. |
| `Galvo` | Converts sample-relative offsets through the installed optical calibration and positions both galvo axes. |
| `Laser` | Owns the separate laser UART protocol, safety checks, targeting, and firing operations. |
| `CoordinateSystems` and navigation helpers | Convert between pixels, top-left sample millimeters, stage millimeters, plate wells, and galvo field positions. |
| `ScanSpec`, `ScanPlan`, and `ScanResult` | Form the scan pipeline. A specification contains geometry and captures, a plan contains validated hardware operations, and a result links every frame to its planned operation. |
| `AcquisitionResult` and `FocusResult` | Record direct single-field acquisition metadata and autofocus measurements. |

`Celigo` is the only component that coordinates hardware. Scan specifications and plans
are immutable values; planning uses configuration and coordinate math without connecting
to the instrument. Execution passes the compiled stage, galvo, channel, camera, and
focus operations back through the owning `Celigo`.

## Scan execution and custom acquisition

Use `await celigo.execute(plan)` to run a complete plan. An optional async `on_frame`
callback receives each `FrameResult` after capture and illumination cleanup, before
execution continues. This supports saving images and reporting progress during a scan:

```python
import logging

from pylabrobot.revvity.celigo import FrameResult

logger = logging.getLogger(__name__)


async def report_frame(result: FrameResult) -> None:
  logger.info("Completed frame %s of %s", result.planned.index + 1, plan.frame_count)


result = await celigo.execute(plan, on_frame=report_frame, initial_brightfield_z_mm=2.5)
```

`initial_brightfield_z_mm` seeds each block's focus with channel offsets applied;
autofocus, when requested by the plan, updates focus within that block. Callback
exceptions or cancellation stop the remaining plan and propagate to the caller.
`execute` still retains every frame in its returned `ScanResult`, including when
a callback is supplied.

For custom acquisition loops and previews, use `move_to_scan_block(block)` followed
by `acquire_scan_position(...)`. Pass the returned settled XY position to
`settled_stage_position_mm`. The caller owns block/frame sequencing and must use a
plan matching the current configuration. `acquire_scan_position` does not move or
verify XY; it handles focus, channel selection, galvos, optional auto-exposure,
capture, and illumination cleanup. Serialize operations on an instrument.

## Package

| Module | Responsibility |
|---|---|
| `celigo.py` | Device lifecycle, FTDI protocol, illumination, acquisition, autofocus, and diagnostics |
| `motion.py` | Stepper motors, linear axes, filter wheels, homing, and encoder motion |
| `camera.py` | Async Lumenera SDK capture and dependency-free raw image frames |
| `galvo.py` | Galvo positioning, calibration, status, and calibrated voltage conversion |
| `laser.py` | Guarded laser UART commands, firing, galvo targeting, and laser optics |
| `config.py` | Typed loaders for the vendor hardware, optical calibration, and channel configuration |
| `coordinates.py` | Pixel, sample-mm, and stage-mm coordinate frames |
| `navigation.py` | Plate/well navigation and galvo FOV planning |
| `scan.py` | Scan specifications, physical planning, execution results, and offline estimates |
| [`grpc/`](grpc/README.md) | Optional device service, versioned protobuf contract, Python client, simulator, and durable jobs/images |

Tutorials, safety guidance, and hardware workflows live in the
[Celigo user guide](../../../docs/user_guide/revvity/celigo/index.md).
