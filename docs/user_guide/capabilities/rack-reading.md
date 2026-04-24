# Rack Reading

The `rack_reading` capability standardizes rack-scale code readers that trigger a rack scan,
report normalized state while scanning, and return structured per-position scan results.

Unlike the single-barcode `barcode_scanning` capability, rack reading is job-oriented and returns
the full decoded rack map.

## Public API

```python
from pylabrobot.capabilities.rack_reading import RackReader

result = await reader.scan_rack(timeout=60.0, poll_interval=1.0)
```

`scan_rack()` is the main public operation. It triggers the scan, waits internally until the
reader reaches `dataready`, and then returns a `RackScanResult`.

If the hardware supports reading just the rack barcode without decoding all tube positions,
`scan_rack_id()` exposes that as a rack-reading operation and returns the rack identifier only.

Lower-level methods are also available:

- `get_state()`
- `wait_for_data_ready()`
- `trigger_rack_scan()`
- `scan_rack_id()`
- `get_scan_result()`
- `get_rack_id()`
- `get_layouts()`
- `get_current_layout()`
- `set_current_layout(layout)`

## Example With Micronic

```python
from pylabrobot.micronic import MicronicCodeReader

reader = MicronicCodeReader(host="localhost", port=2500)
await reader.setup()

try:
  result = await reader.rack_reading.scan_rack(timeout=90.0, poll_interval=1.0)
  print(result.rack_id)
  print(result.entries[0].position, result.entries[0].tube_id)

  rack_id = await reader.rack_reading.scan_rack_id(timeout=60.0, poll_interval=1.0)
  print(rack_id)
finally:
  await reader.stop()
```
