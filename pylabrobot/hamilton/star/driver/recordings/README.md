# Device recordings

What a real STAR reported about itself, kept as data so a simulated one can stand in for it.

**None of the three shipped here says which device it came from.** Only the STAR was read off a
device at all, and that reading predates `C0 RI`, so its `serial_number` is null and the STARlet and
STARplus inherit its firmware strings along with everything else derived from it. The first
recording taken with `save_configuration` on a real device will carry both, and is worth taking for
that reason alone. A device that will not answer `C0 RI` leaves the field null too, with a warning
at setup - so a null serial means either an old recording or a device that would not say, and the
file cannot tell you which.

A recording is written by the driver, not by hand:

```python
star = STAR(driver=STARDriver())
await star.setup()
star.driver.save_configuration("star_legacy_2021_8ch_head96_autoload1D.json")
```

and read back through `declared_configuration_json`, which is the one way a configuration is read
from a file:

```python
star = STAR(simulation=True, declared_configuration_json="my_star.json")
await star.setup()
```

It means two different things depending on what is on the other end. A **simulated** device answers
as the declaration says, standing in for the device it records. A **physical** device answers for
itself, and the declaration is cross-checked against it: setup refuses if the two disagree on which
features are fitted, how many channels, or what each arm carries. Identity and geometry are not
compared, so a declaration taken off one device still describes another of the same build.

Five recordings ship with this package. `STAR`, `STARLet` and `STARPlus` hand the 96-head one for
their frame to a **simulated** device when nothing else is declared; the two 384-head ones are
declared by name. A physical device is never given any of them:

| frame | head | recording |
|---|---|---|
| STAR | 96 | `star_legacy_2021_8ch_head96_autoload1D.json` |
| STARlet | 96 | `starlet_legacy_2021_8ch_head96_autoload1D.json` |
| STARplus | 96 | `starplus_legacy_2021_8ch_head96.json` |
| STAR | 384 | `star_legacy_2021_8ch_head384_autoload1D.json` |
| STARlet | 384 | `starlet_legacy_2021_8ch_head384_autoload1D.json` |

Only the first was read off a device. The others are derived from it. The two other frames move
everything the right-hand end of the deck sets by the difference in deck length, and leave
everything belonging to the arm itself where it is; the STARplus is derived without an autoload,
because the recorded sled does not travel far enough to reach that frame's last track. The two
384-head ones swap the head the arm carries, flipping the bits that say which head is fitted and
putting the 384-head's documented configuration where the 96-head's reading was. Replace any of
them wholesale with a recording when there is a device to take one from.

A 384-head device is what the 384-head's own configuration is declared through, rather than a
fragment file: it is a device that exists, so it is described the way every other device here is.
Field 1 of its name is carried over from the STAR it was derived from, since the convention below
takes that field from a 96-head and one of these has none.

## What is in one

Shaped as the device is, so nothing fixes how many of anything a device may have:

```json
{
  "device":   { ... },
  "arms":     { "left": { "pipettes": {...}, "head96": {...}, "iswap": {...} } },
  "autoload": { ... }
}
```

The device's own configuration is what the master answered. Each feature sits under the arm that
carries it; a feature fitted to the device rather than to an arm sits beside `arms`. A device that
grows a second head is one more entry, with no change to the format.

## What is not

- **Documented defaults.** Values the driver holds because a drive documents them, not because a
  device reported them, stay in the code. The 384-head is the standing example: no 384-head has
  been read off any device, so its offsets and drive defaults live in `simulator.py`.

  Some fields break this rule knowingly: the iSWAP's
  `rotation_drive_predefined_z_positions_increments`, the 96-head's
  `predefined_y_positions_increments` and `predefined_z_positions_increments`, and the whole of
  the 384-head, which no device has been read for at all. Each carries its drive's documented
  defaults rather than a reading, because a simulated device has to answer where it parks and
  nothing else here says. They are in the file rather than the code so that the
  first device read overwrites a value in the same place, instead of leaving a constant to be
  found and deleted. Until then they are documented defaults sitting where readings belong, and
  the device they describe may hold something else.
- **Where the device is.** Rest positions, probed Z heights, which track the autoload sits on. That
  is state, not configuration, and it changes every run.

## Naming

A convention to follow, not something the driver does. `save_configuration` writes exactly the path
it is given and nothing else: it chooses no directory, invents no name, and appends no extension.
Where a recording goes and what it is called are yours, so give it a full path.

Six fields, in the order below, joined by `_`. Every one can be read off the device or the file it
wrote, so a name can always be worked out from what you have. Two recordings of the same class of
device then sort together, and a reader can tell them apart without opening either.

| field | value | read from |
|---|---|---|
| 0 | `star`, `starlet`, `starplus` | `device.instrument_size_slots` (54, 30, 76) |
| 1 | `legacy`, `FM` | `arms.<side>.head96.instrument_type` |
| 2 | build year, `YYYY` | `device.firmware_date` |
| 3 | `8ch`, `12ch`, `16ch` | `device.num_pip_channels` |
| 4 | `head96`, `head384` | `device.head96_installed` / `device.head384_installed` |
| 5 | `autoload1D`, `autoload2D` | `autoload.autoload_type` |

Leave a field out only when the device has none of that thing: a device with no autoload ends at
field 4. Do not reorder, and do not abbreviate a field to make a name shorter, because the position
is what carries the meaning.

The device this package ships a recording of reads as:

```
star_legacy_2021_8ch_head96_autoload1D.json
```

### What a recording cannot yet tell you

**Field 1 is not confirmed.** `instrument_type` is decoded from the third of the 96-head's hardware
tokens, and whether that token is populated on every build is unverified. A device reading `legacy`
may be one that does not report the token rather than one that is legacy. Confirm against the
device before trusting the field on an FM.

**Three stored tables are documented defaults rather than readings.** The iSWAP's
`rotation_drive_predefined_z_positions_increments` and the 96-head's two predefined tables were
never read off a device, for the reason given above. Each has a reader that records what came
back - `rotation_drive_request_predefined_z_positions`, `request_predefined_y_positions` and
`request_predefined_z_positions` - so one call apiece on a device makes them real.

**Two more identity facts are still unread.** A recording says which device answered and what it
was running, through `device.serial_number` and `device.firmware_version`. Two things the older
driver could read are not implemented here:

| fact | how it is read | why it might matter |
|---|---|---|
| download date | `C0 RO` | another candidate for field 2, though whether it dates the build or the last firmware download is unverified |
| electronic board type | `C0 QB` | four board generations are known, and which one a device has may bear on the encodings that apply to it |

`device.serial_number` is what the device answers, not the USB serial a driver may have picked it
off the bus with. It is null in the recording shipped here: that device's serial was never read,
and it is not ours to invent.
