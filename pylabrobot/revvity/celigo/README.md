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

Tutorials, safety guidance, and hardware workflows live in the
[Celigo user guide](../../../docs/user_guide/revvity/celigo/index.md).
