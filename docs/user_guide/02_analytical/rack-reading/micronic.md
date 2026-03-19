# Micronic Rack Reader

PyLabRobot includes a Micronic HTTP backend for the Micronic Code Reader software.

This integration targets the `IO Monitor` HTTP server exposed by the Micronic Windows application.

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
from pylabrobot.rack_reading import RackReader
from pylabrobot.rack_reading.micronic import MicronicHTTPBackend

reader = RackReader(backend=MicronicHTTPBackend(host="127.0.0.1", port=2500))
await reader.setup()

result = await reader.scan_box_and_wait(timeout=60.0, poll_interval=2.0)

print(result.rack_id)
print(result.entries[0].position, result.entries[0].tube_id)
```

## Notes

- The Micronic server is path-based. Use `POST /scanbox`, not `POST /` with raw text.
- The Micronic application must have the HTTP server enabled in `IO Monitor`.
- The backend is intentionally focused on the HTTP rack-reading workflow and does not model CSV export behavior.
