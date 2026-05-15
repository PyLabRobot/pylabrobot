# Rack Reading

The `rack_reading` capability standardizes rack-scale code readers that decode a tube rack and
return structured per-position scan results.

Unlike one-at-a-time code reads, rack reading is job-oriented and returns the full decoded rack map.

## Public API

```python
from pylabrobot.capabilities.rack_reading import RackReader

result = await reader.rack_reading.scan_rack(rack, timeout=60.0, poll_interval=1.0)
```

- `scan_rack(rack, timeout, poll_interval)` — scan a `TubeRack` and return a `RackScanResult`. The
  backend validates that the rack shape matches what the hardware supports.
- `scan_rack_id(timeout, poll_interval)` — read just the rack barcode (no per-position decoding)
  and return the rack identifier.

## Example With Micronic

```python
from pylabrobot.micronic import MicronicCodeReader

reader = MicronicCodeReader(
  scanner_backend="sane",
  sane_device="avision:libusb:001:004",
  serial_port="/dev/ttyUSB0",
)
await reader.setup()

try:
  result = await reader.rack_reading.scan_rack(rack=my_rack, timeout=90.0, poll_interval=1.0)
  print(result.rack_id)
  print(result.entries[0].position, result.entries[0].tube_id)

  rack_id = await reader.rack_reading.scan_rack_id(timeout=60.0, poll_interval=1.0)
  print(rack_id)
finally:
  await reader.stop()
```
