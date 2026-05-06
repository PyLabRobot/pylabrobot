# Micronic

PyLabRobot includes `v1b1` Micronic integrations built on the generic
`rack_reading` and `barcode_scanning` capabilities.

There are two rack-reader drivers:

- `MicronicIOMonitorDriver`
  targets the `IO Monitor` HTTP server exposed by the Micronic Code Reader
  Windows application. It supports rack reading and single-tube barcode
  scanning.
- `MicronicDirectDriver`
  controls the local Windows hardware directly. It acquires the rack image
  through the Avision TWAIN source, reads the side rack barcode through the
  serial reader, decodes tube DataMatrix codes locally, and returns the same
  `RackScanResult` shape through the standard `rack_reading` capability. It
  does not call Micronic Code Reader or IO Monitor.

Both drivers plug into `MicronicCodeReader` through the same `rack_reading`
capability. `MicronicDirectCodeReader` is a convenience frontend that constructs
`MicronicCodeReader` with `MicronicDirectDriver`.

## Supported operations

Rack reading (large scanner that decodes 96 tubes plus the side rack barcode):

- `GET /state`
- `POST /scanbox` to trigger a full rack scan
- `GET /scanresult` to read the decoded grid
- `GET /rackid` for a rack-barcode-only read on the side reader (one-shot trigger+result)
- `GET /layoutlist`
- `GET /currentlayout`
- `PUT /currentlayout`

Single-tube barcode scanning (small spot, separate from the rack scanner):

- `GET /state`
- `POST /scantube`
- `GET /scanresult`
- `GET /rackid` as a compatibility fallback for server variants that expose the decoded
  tube value there

## IO Monitor example

```python
from pylabrobot.micronic import MicronicCodeReader, MicronicIOMonitorDriver

reader = MicronicCodeReader(driver=MicronicIOMonitorDriver(host="localhost", port=2500))
await reader.setup()

try:
  rack_result = await reader.rack_reading.scan_rack(timeout=60.0, poll_interval=1.0)
  print(rack_result.rack_id)
  print(rack_result.entries[0].position, rack_result.entries[0].tube_id)

  rack_id = await reader.rack_reading.scan_rack_id(timeout=10.0, poll_interval=0.5)
  print(rack_id)

  barcode = await reader.barcode_scanning.scan()
  print(barcode.data)
finally:
  await reader.stop()
```

## Direct hardware example

Use `MicronicDirectDriver` when the Windows host should own scanner
acquisition, rack-ID reads, and tube decoding without the Micronic application.
The direct path exposes `rack_reading`; it does not expose `barcode_scanning`.

```python
from pylabrobot.micronic import MicronicCodeReader, MicronicDirectDriver

reader = MicronicCodeReader(
  driver=MicronicDirectDriver(
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

## Notes

- The Micronic server is path-based. Use `POST /scanbox`, not `POST /` with raw text.
- The Micronic application must have the HTTP server enabled in `IO Monitor`.
- The reader only supports one external client at a time.
- `localhost` is typically safer than `127.0.0.1` on the Windows host.
- `scan_rack` reads every tube barcode and finishes by reading the rack ID, so
  it typically takes tens of seconds. `scan_rack_id` only reads the rack
  barcode and completes in a few seconds.
- The direct reader is Windows-only for live hardware scans because it calls the
  installed TWAIN stack and the Windows serial-port APIs. Use `image_input` for
  offline decode checks.
