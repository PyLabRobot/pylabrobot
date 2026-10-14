# Updating component firmware

The STAR driver can download manufacturer-supplied ACH images to individual standard component
boards. Firmware images are supplied separately from PyLabRobot. Use an image intended for the
installed hardware; matching a filename and version alone does not establish compatibility.

| Component API | Board | Image family | Supported firmware major versions |
| --- | --- | --- | --- |
| `star.pipettes.update_firmware(channel=0, ...)` | P1–PG | `GRUPXS*.ACH` | 4, standard 1000 uL channels |
| `star.left_x_arm.update_firmware(...)` | X0 | `GRUX0S*.ACH` | 1, 5 |
| `star.iswap.update_firmware(...)` | R0 | `GRUR0S*.ACH` | 3, 4 |
| `star.autoload.update_firmware(...)` | I0 | `GRUALS*.ACH` | 3 |
| `star.head96.update_firmware(...)` | H0 | `GRUH0S*.ACH` | 2, 5 |
| `star.head384.update_firmware(...)` | D0 | `GRUD0S*.ACH` | 1 |

Channel indices start at zero: channel `0` addresses P1. Both X arms share X0: updating through
either arm updates the same board and refreshes both cached versions. On a two-arm instrument,
address mounted features through their arm, such as `star.left_x_arm.head96`.

With an already connected, idle STAR, update one component at a time:

```python
result = await star.pipettes.update_firmware(
  channel=0,
  firmware_file="GRUPXS40 j 2022-03-16.ACH",
  expected_version="4.0S j 2022-03-16",
)
print(result.version, result.sha256, result.records_programmed)
print(result.cycle_counts_before, result.cycle_counts_after)
```

The other components take the same arguments without `channel`. For example, with a compatible
96-head image:

```python
result = await star.head96.update_firmware(
  firmware_file="GRUH0S50e.ACH",
  expected_version="5.0S e 2017-03-16 (H0 XE167)",
)
```

Supply the complete embedded version, including any hardware suffix. An older build date requires
`allow_downgrade=True`. Changing controller generations is rejected even with that option: for
example, X0 1.x and 5.x images target different controllers, as do H0 2.x and 5.x images.

Before changing device state, the updater reads the entire file and checks its family, record types,
lengths, checksums, final EOF, and embedded version. It checks the target's version, status, EEPROM,
and applicable hardware variant, then captures calibration, identity, configuration, and supported
cycle counters. Malformed version replies stop the update before entering download mode.

The boards share this transfer sequence in `firmware_update.py`:

1. `_switch_firmware_mode()` sends AP without waiting for a reply. After 15 seconds,
   `request_status()` must report download mode with clear error bits.
2. `_erase_firmware()` sends DE and requires an explicit success reply.
3. `_program_firmware_record()` sends each ACH record with DP and waits for its explicit
   success reply, including the EOF record.
4. `_switch_firmware_mode()` starts the application. After 18 seconds, the updater checks
   its version, status, EEPROM, saved settings, and the master firmware version. Pipette updates
   also verify the other pipetting channels' versions.

Download-mode verification matters especially for heads: DE/DP can mean movement commands in
the application. Every command has a named Python method; erase, record programming, and mode
switching remain private steps of the complete update. The driver holds its motion locks throughout
and rejects commands from other tasks. Cached versions are cleared before switching modes and
restored only after successful verification.

Keep power and the connection stable. A timeout, rejected record, or cancellation stops the update
without retrying the write or restarting partially programmed firmware. Such a failure requires
recovery before using the component.

After success, power-cycle the instrument, reconnect, and initialize it normally before use. The
update commands do not initialize or move the instrument. Pipette results contain
`PipetteCycleCounts`; other components return counter dictionaries (gripping cycles, barcode scans,
or head tip and liquid cycles). X-drive counters are not read and return empty dictionaries.
Counter differences are logged and returned; neither counters nor download dates are rewritten.

The master controller (C0), PLE pipetting head, and accessories such as pumps and temperature
controllers are not supported by these update APIs. Their formats or download protocols require
separate handling. The X-drive, iSWAP, autoload, and 96/384-head paths have offline protocol coverage;
their complete transfers have not yet been verified on hardware.
