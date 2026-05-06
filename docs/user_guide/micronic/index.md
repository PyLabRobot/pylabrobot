# Micronic

PyLabRobot includes `v1b1` Micronic integrations built on the generic
`rack_reading` capability.

`MicronicDirectDriver` controls the local hardware directly. It acquires the
rack image through a configured scanner command, a Windows TWAIN helper
available on PATH, or Ubuntu/Linux SANE `scanimage`; reads the side rack barcode
through the serial reader; decodes tube DataMatrix codes locally; and returns a
standard `RackScanResult`. It does not call Micronic Code Reader or IO Monitor,
and PyLabRobot does not package any scanner helper executable.

`MicronicDirectCodeReader` is a convenience frontend that constructs
`MicronicCodeReader` with `MicronicDirectDriver`.

## Supported operations

Rack reading (large scanner that decodes 96 tubes plus the side rack barcode):

- `rack_reading.scan_rack()` to trigger image acquisition, decode all 96 tube
  positions, read the side rack barcode, and return a `RackScanResult`
- `rack_reading.scan_rack_id()` for a rack-barcode-only read on the side reader
- `rack_reading.get_layouts()`, `get_current_layout()`, and
  `set_current_layout()` for the fixed 8x12 rack layout

## Direct hardware example

The operator is responsible for installing any OS-level scanner bridge
(`twain_scan`, `scanimage`, or a custom command), pyserial, and the local Python
decode dependencies in the runtime environment.

```python
from pylabrobot.micronic import MicronicCodeReader, MicronicDirectDriver

reader = MicronicCodeReader(
  driver=MicronicDirectDriver(
    scanner_backend="twain",
    twain_scanner_path=r"C:\Tools\twain_scan.exe",
    twain_source="AVA6PlusG",
    image_dir=r"C:\ProgramData\Alakascan\data\direct-images",
    serial_port="COM4",
    keep_images=True,
  )
)
await reader.setup()

try:
  rack_result = await reader.rack_reading.scan_rack(timeout=90.0, poll_interval=1.0)
  print(rack_result.rack_id)
  print(len([entry for entry in rack_result.entries if entry.tube_id]))

  rack_id = await reader.rack_reading.scan_rack_id(timeout=5.0, poll_interval=0.5)
  print(rack_id)
finally:
  await reader.stop()
```

On Ubuntu/Linux, use SANE if the scanner is exposed by a SANE backend:

```python
reader = MicronicCodeReader(
  driver=MicronicDirectDriver(
    scanner_backend="sane",
    sane_device="avision:libusb:001:004",
    serial_port="/dev/ttyUSB0",
    image_extension="tiff",
  )
)
```

For any other acquisition stack, pass `scan_command`. Each command argument is
formatted with `{output_path}`, `{timeout_ms}`, `{twain_source}`, and
`{sane_device}` before execution. The command must write the rack image to
`{output_path}`.

## Notes

- `scan_rack` reads every tube barcode and finishes by reading the rack ID, so
  it typically takes tens of seconds. `scan_rack_id` only reads the rack
  barcode and completes in a few seconds.
- TWAIN is a Windows scanner-driver API. PyLabRobot does not ship a TWAIN
  bridge binary and does not install one for you; configure
  `twain_scanner_path`, set `MICRONIC_TWAIN_SCANNER_PATH`, or put a local helper
  named `twain_scan`/`twain_scan.exe` on PATH when using the `twain` backend.
- Ubuntu/Linux scanner control should use SANE `scanimage` or a custom
  `scan_command`. PyLabRobot does not install SANE or vendor scanner drivers.
  Rack-ID reads use `pyserial`.
- Direct image decoding imports `pillow`, `opencv-python-headless`, `numpy`, and
  `zxing-cpp` at runtime. Install them in the environment that runs PyLabRobot
  when using the direct driver.
- Use `image_input` for offline decode checks without touching scanner hardware.
