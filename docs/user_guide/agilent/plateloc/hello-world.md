# Agilent PlateLoc

The Agilent PlateLoc is controlled through PLR's `Sealer` capability with a direct RS-232 serial
driver. It does not require Agilent ActiveX, VWorks, or vendor server software. Install the
optional serial dependency before connecting:

```bash
pip install "pylabrobot[serial]"
```

```python
from pylabrobot.agilent import PlateLoc

plateloc = PlateLoc(name="plateloc", port="COM6")

await plateloc.setup()
await plateloc.set_sealing_temperature(175)
await plateloc.set_sealing_time(0.5)
status = await plateloc.request_status()
await plateloc.stop()
```

The device also exposes the standard sealer capability:

```python
await plateloc.sealer.seal(temperature=175, duration=1.5)
await plateloc.sealer.open()
await plateloc.sealer.close()
```

`sealer.open()` and `sealer.close()` move the stage and wait for the default stage-settle delay.
`sealer.seal()` starts a sealing cycle after writing the requested temperature and time.

The PlateLoc device class also exposes independent setpoint and status helpers:

```python
await plateloc.set_sealing_temperature(160)
await plateloc.set_sealing_time(1.0)

status = await plateloc.request_status()
print(status.target_temperature, status.sealing_time, status.cycle_complete)
```

`request_status()` returns the best-known PLR state plus a live cycle-complete query. The direct
serial protocol decoded here does not expose actual block temperature or actual stored time reads,
so PLR reports the last successfully written target temperature and sealing time.

## Serial command profile

The decoded direct protocol uses `19200 8N1` and carriage-return-terminated ASCII frames with
two-letter command codes plus payloads. Temperature and time payloads use the firmware's fractional
setpoint convention: the digits after the decimal point are the integer controller value.

| Operation | Frame |
|---|---|
| Set sealing temperature | `ST 0.{temperature_celsius:03d}\r` |
| Set sealing time | `SS 0.{seconds_x10:02d}\r` |
| Start cycle | `GO 00\r` |
| Stop cycle | `AC 00\r` |
| Move stage out | `SO 00\r` |
| Move stage in | `SI 00\r` |
| Apply seal | `AS 00\r` |
| Clear error | `CL 00\r` |
| Check cycle complete | `CC 00\r` |

For example, `set_sealing_temperature(175)` writes `ST 0.175\r`, `set_sealing_temperature(30)`
writes `ST 0.030\r`, `set_sealing_time(0.5)` writes `SS 0.05\r`, and `set_sealing_time(1.2)`
writes `SS 0.12\r`.

Negative acknowledgements are parsed as `<code>NK(message)` and raised as `PlateLocError`. Some
valid firmware commands reply with single-carriage-return acknowledgements such as `SOAK\r`. The
cycle-complete command returns `True` for `CCAK\r` and `False` for `CCNK\r`.

You can still override command codes or serial settings with `PlateLocSerialProfile` while keeping
the same PLR frontend:

```python
from pylabrobot.agilent import PlateLoc, PlateLocSerialProfile

profile = PlateLocSerialProfile(
  baudrate=19200,
  stage_move_delay=6,
  commands={
    "set_sealing_temperature": "ST",
    "set_sealing_time": "SS",
    "start_cycle": "GO",
    "move_stage_out": "SO",
    "move_stage_in": "SI",
  },
)

plateloc = PlateLoc(name="plateloc", port="COM6", profile=profile)
```

## Troubleshooting

The PlateLoc RS-232 connector is not VGA and is not USB TTL. Use a USB-to-RS-232 adapter plus the
correct DB9 cable for the instrument. If the port opens but every command times out, verify the
PlateLoc is powered, the rear serial cable is seated, and the cable wiring matches the instrument
requirement. Some setups require a null-modem DB9 adapter rather than a straight-through cable.
