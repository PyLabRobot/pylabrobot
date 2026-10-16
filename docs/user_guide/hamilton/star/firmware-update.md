# Updating firmware

## Guide

Use a manufacturer-supplied ACH image intended for the installed controller. Firmware images are
supplied separately from PyLabRobot. Connect to an idle STAR and update one target at a time.

| API | Target | Image family | Supported firmware |
| --- | --- | --- | --- |
| `star.update_firmware(...)` | C0 master | `GRUC0S*.ACH` | 7.x, C167 dual-processor board |
| `star.pipettes.update_firmware(channel=0, ...)` | P1–PG | `GRUPXS*.ACH` | 4.x, standard 1000 uL channels |
| `star.left_x_arm.update_firmware(...)` | X0 | `GRUX0S*.ACH` | 1.x, 5.x |
| `star.iswap.update_firmware(...)` | R0 | `GRUR0S*.ACH` | 3.x, 4.x |
| `star.autoload.update_firmware(...)` | I0 | `GRUALS*.ACH` | 3.x |
| `star.head96.update_firmware(...)` | H0 | `GRUH0S*.ACH` | 2.x, 5.x |
| `star.head384.update_firmware(...)` | D0 | `GRUD0S*.ACH` | 1.x |

Channel indices start at zero: channel `0` addresses P1. Both X arms use the X0 firmware target;
updating through either arm updates that target and refreshes both cached versions. On a two-arm
instrument, address mounted features through their arm, such as `star.left_x_arm.head96`.

```python
result = await star.pipettes.update_firmware(
  channel=0,
  firmware_file="GRUPXS40 j 2022-03-16.ACH",
  expected_version="4.0S j 2022-03-16",
)
print(result.version, result.sha256, result.records_programmed, result.download_date)
print(result.cycle_counts_before, result.cycle_counts_after)
```

The other targets take the same arguments without `channel`. For example, on a compatible C0
controller, retaining the image's original filename:

```python
result = await star.update_firmware(
  firmware_file="GRUC0S75_0_2017_02_02.ACH",
  expected_version="7.5S 00 2017_02_02 (GRU C0 PLE)",
)
```

Supply the complete expected version, including the build date and hardware suffix. Updating to an
older build requires `allow_downgrade=True`.

Keep power and the connection stable during the update. After success, power-cycle the instrument,
reconnect, and initialize it normally before use. A timeout, rejected record, or cancellation stops
the update; an interrupted transfer requires recovery before using that target.

Not supported:

- C0 single-processor and LPC2468/XE167 boards, including C0 8.x images.
- C0 updates over USB backends that cannot report the device's bus and physical port numbers.
- Engineering (`GRUC0E`), installer, and bootloader images.
- PLE heads, XL or robotic channels, and pipetting channels other than the standard 1000 uL type.
- Accessories such as pumps, wash stations, temperature controllers, and Seyonic modules.
- Broadcast updates such as PX, or changes between controller generations.
- Cycle-count reporting on C0 and X0 1.x, which have no cycle-counter query. Their result fields
  `cycle_counts_before` and `cycle_counts_after` are `None`.

## Background

The updater validates every ACH record's length, type, and checksum before changing device state.
It checks the image family, controller generation, version, status, EEPROM, and applicable hardware
variant, then captures stored settings and cycle counts. C0 7.x stores its build date and controller
tag as separate strings; those and the versioned filename are checked before transfer, and the full
reported version is checked after restart.

The shared transfer in `firmware_update.py` follows this sequence:

1. `_switch_firmware_mode()` sends AP without waiting for a reply. After 15 seconds,
   `request_status()` must report download mode with clear error bits.
2. `_erase_firmware()` sends DE and requires a success acknowledgement.
3. `_program_firmware_record()` sends each ACH record with DP and requires a success
   acknowledgement, including for the EOF record.
4. `_switch_firmware_mode()` starts the application. After 18 seconds, the updater checks its
   version, status, EEPROM, and stored settings. C0 updates also compare every installed slave's
   firmware version; pipette updates compare the other pipetting channels' versions.
5. `_record_download_date()` writes today's date with AO and reads it back with RO.

C0 restarts its USB connection when switching modes. The updater reconnects to the same physical
USB port after each restart, even if its device address changes.

The driver holds its motion locks throughout the update and rejects commands from other tasks.
It confirms download mode before sending DE or DP, since these commands have movement meanings in
some applications. It does not retry uncertain writes or restart partially programmed firmware.
Cached versions are cleared before the mode switch and restored after successful verification.

Channel type is recorded as `PipetteConfiguration.pip_type_1000ul`. Discovery checks it against the
master's configuration, and firmware updates repeat the channel query and comparison before flashing.

Cycle counts are compared, logged, and returned without rewriting them. Pipette and head results
contain `PipetteCycleCounts`, also returned by their `request_cycle_counts()` methods.
`star.pipettes.request_all_cycle_counts()` returns these values in channel-index order.
Other targets return dictionaries when counters are available: X0 5.x uses the documented
`na`, `nb`, `nc`, and `nf` fields; iSWAP returns gripping cycles and autoload returns barcode scans.
