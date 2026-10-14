# Updating a pipetting channel's firmware

`Pipettes.update_firmware()` downloads a manufacturer-supplied `GRUPXS*.ACH` image to one
standard 1000 uL STAR channel. Channel indices start at zero: channel `0` addresses P1.
Use an image compatible with the installed channel hardware. Firmware images are supplied
separately from PyLabRobot.

With an already connected, idle STAR:

```python
result = await star.pipettes.update_firmware(
  channel=0,
  firmware_file="GRUPXS40 j 2022-03-16.ACH",
  expected_version="4.0S j 2022-03-16",
)
print(result.version, result.sha256, result.records_programmed)
print(result.cycle_counts_before, result.cycle_counts_after)
```

An image with an older build date requires `allow_downgrade=True`. Before erasing flash, the
updater reads the entire file and checks its record types, lengths, checksums, final EOF,
and embedded version. It checks the channel's status, EEPROM, and hardware, then captures
its calibration, configuration, identity, and cycle counts.

The transfer follows this sequence:

1. `_switch_firmware_mode()` sends AP without waiting for a reply. After 15 seconds,
   `request_status()` must report download mode with clear error bits.
2. `_erase_firmware()` sends DE and requires an explicit success reply.
3. `_program_firmware_record()` sends each ACH record with DP and waits for its explicit
   success reply, including the EOF record.
4. `_switch_firmware_mode()` starts the application. After 18 seconds, the updater checks
   its version, status, EEPROM, and saved settings, and checks that other channel and master
   firmware versions are unchanged.

Every firmware command is wrapped in a named Python method. The erase, record-programming,
and mode-switch methods are private because they are steps of the complete update procedure.
The driver holds its motion locks throughout the update and rejects commands from other tasks.

Keep power and the connection stable during the transfer. A timeout, rejected record, or
cancellation stops the update without retrying the write or restarting partially programmed
firmware. Such a failure requires recovery before using the channel.

After a successful transfer, power-cycle the instrument, reconnect, and initialize it normally
before pipetting. The update itself does not initialize or move the instrument. Cycle-counter
differences are logged and returned; neither counters nor the stored download date are rewritten.
