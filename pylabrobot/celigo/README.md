# Celigo — native Linux driver

A PyLabRobot driver for the Nexcelom/Cyntellect **Celigo** image cytometer. It talks
directly to the instrument over USB — no vendor software required.

## Architecture (low → high)

| Module | Responsibility |
|---|---|
| `packets.py` | USB-IO board wire protocol — framing (11-byte TX / 12-byte RX headers, fletcher-16), the 48 `IO_CTLR_CMDS` opcodes, `transact()` with retry |
| `transport.py` | Byte transports: `SerialTransport` (the board on `/dev/ttyUSB*` via the kernel ftdi_sio driver) and `FtdiTransport` (pyftdi). Board baud = **230400** |
| `controller.py` | `CeligoController` — board commands: status, galvo, analog/digital IO, autofocus, motor query, barcode, raw DAC |
| `ezstepper.py` | AllMotion EZStepper command strings (`/<addr>…R\r`) + OEM/WLEN framing + response parsing |
| `config.py` | Typed loaders for the `ConfigFiles/*.xml` (hardware axes, calibration, hardware defaults, channels, galvo cubic calibration) |
| `transforms.py` | Encoder-tick ↔ stage-mm; galvo-mm ↔ volts ↔ DAC (2D cubic) |
| `coordinates.py` | Pixel ↔ sample-mm ↔ stage-mm affine frames (pixel/sample/stage) |
| `navigation.py` | Plate/well navigation (`well_to_stage_mm`/`well_to_encoder_ticks`), the Corning-3603 96-well preset, galvo FOV grid |
| `device.py` | `Celigo` facade tying it together: setup, move-to-well, Z focus, brightfield, stage open/close |
| `demo.py` | `python -m pylabrobot.celigo.demo` — setup + load/unload against a simulated board |

## Status

The driver supports:
- Control board communication over serial (status queries, command transactions)
- X/Y/Z motion with encoder feedback and limit-protected moves
- Stage open/close
- Brightfield illumination
- Z focus control
- Well navigation

Camera image capture is not yet supported.

## Usage

```python
from pylabrobot.celigo.device import Celigo

cel = Celigo(port="/dev/ttyUSB3", config_dir="/path/to/Celigo/ConfigFiles")
cel.setup()
cel.set_brightfield(True)
cel.move_to_well("A1")     # well → stage mm → encoder ticks → move, polled to completion
cel.move_z(10337)          # focus
cel.set_brightfield(False)
cel.close()
```

## Tests

```
pytest pylabrobot/celigo/      # 101 tests; ruff + mypy clean
```
All tests run against a `MockBoard` that speaks the real protocol — no hardware needed.
