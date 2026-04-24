# Micronic

PyLabRobot includes a `v1b1` Micronic integration built on the generic `rack_reading`
and `barcode_scanning` capabilities.

This integration targets the `IO Monitor` HTTP server exposed by the Micronic Code Reader
Windows application.

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

## Example

```python
from pylabrobot.micronic import MicronicCodeReader

reader = MicronicCodeReader(host="localhost", port=2500)
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

## Notes

- The Micronic server is path-based. Use `POST /scanbox`, not `POST /` with raw text.
- The Micronic application must have the HTTP server enabled in `IO Monitor`.
- The reader only supports one external client at a time.
- `localhost` is typically safer than `127.0.0.1` on the Windows host.
- `scan_rack` reads every tube barcode and finishes by reading the rack ID, so
  it typically takes tens of seconds. `scan_rack_id` only reads the rack
  barcode and completes in a few seconds.
