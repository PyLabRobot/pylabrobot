# Micronic

PyLabRobot includes a `v1b1` Micronic integration built on the generic `rack_reading`
capability.

This integration targets the `IO Monitor` HTTP server exposed by the Micronic Code Reader
Windows application.

## Supported operations

- `GET /state`
- `POST /scanbox`
- `POST /scantube`
- `GET /scanresult`
- `GET /rackid`
- `GET /layoutlist`
- `GET /currentlayout`
- `PUT /currentlayout`

## Example

```python
from pylabrobot.micronic import MicronicCodeReader

reader = MicronicCodeReader(host="localhost", port=2500)
await reader.setup()

try:
  result = await reader.scan_rack(timeout=60.0, poll_interval=1.0)
  print(result.rack_id)
  print(result.entries[0].position, result.entries[0].tube_id)
finally:
  await reader.stop()
```

## Notes

- The Micronic server is path-based. Use `POST /scanbox`, not `POST /` with raw text.
- The Micronic application must have the HTTP server enabled in `IO Monitor`.
- The reader only supports one external client at a time.
- `localhost` is typically safer than `127.0.0.1` on the Windows host.
