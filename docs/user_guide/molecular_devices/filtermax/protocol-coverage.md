# FilterMax F5 protocol coverage

This table records the compact, sanitized protocol fixtures recovered from SoftMax Pro 6.3.1 and
a FilterMax F5 with firmware `V1.2 b13 11.12.2014`. Raw timestamped capture files are intentionally
kept outside version control.

## Transport

| Property | Captured value |
|---|---|
| Serial | Automatic startup detection: 38,400 then 9,600 baud; 7 data bits, even parity, 1 stop bit, no flow control |
| Host transaction | `ENQ`; device `ACK`; host frame; device `ACK`; host `EOT` |
| Device response | Device `ENQ`; host `ACK`; one or more device frames, each host-acknowledged; device `EOT` |
| Frame | `STX`, ASCII frame number, ASCII payload, `ETB` or `ETX`, two uppercase ASCII checksum digits, `CR LF` |
| Frame numbers | `1` through `7`, then `0`, repeated for additional continuation frames |
| Checksum | Modulo-256 sum from the ASCII frame number through `ETB`/`ETX`, inclusive |
| Example | `TG` is `02 31 54 47 03 43 46 0D 0A` |
| Success | Payload beginning with `+` |
| Device error | `- E<code>: <details>` |

During setup, PLR first requests the instrument identity at 38,400 baud. If the initial ENQ
receives no byte before its deadline, PLR closes the port and tries the identity request at
9,600 baud. The working rate is retained for the session and recorded in the connection log.
An unexpected byte, invalid identity, checksum error, device error, or timeout after the initial
ACK ends setup without another attempt. Failed setup releases the port; errors after connection
do not trigger baud detection or command retries.

The SoftMax startup capture includes a silent probe at 38,400 baud, a successful identity exchange
at 9,600 baud, and an acknowledged `BAUD 2` command before later communication at 38,400 baud.
PLR supports both observed rates and does not send `BAUD` or change the reader's baud setting.

Only one application may use the serial port. When SoftMax runs in a VirtualBox VM whose COM port
maps to the host serial device, save the results and shut the guest down normally before connecting
PLR. Confirm the VM is powered off: closing SoftMax or an empty unprivileged `fuser` result does not
establish that VirtualBox released the host device.

The instrument does not emit a separate busy token in the captured operations. A command owns the
half-duplex transaction until its response arrives. Long reads return one or more framed result
messages. Kinetic timing is scheduled client-side, matching SoftMax's observed behavior. Result
timepoints use the requested kinetic schedule (for example, 0, 18, and 36 seconds); if a read
overruns an interval, the next read starts immediately without changing its nominal timepoint.

## Controls and state

| Operation | Exact request payload | Captured response form | Final state |
|---|---|---|---|
| Identity | `?` | Multiframe model, firmware, serial, slide IDs, device code, and options | Unchanged |
| Ready status | `STAT` | `+ READY` | Unchanged |
| State bits | `SGS` | `+` followed by 11 integers | Unchanged |
| Installed slides | `CS` | `+ 2 1` for excitation ID 2 and emission ID 1 | Unchanged |
| Tray in | `L P` | `+` and captured position | Tray in |
| Tray out | `E P` | `+` and captured position | Tray out |
| Excitation slide out/in | `E A` / `L A` | `+` | Requested slide state |
| Emission slide out/in | `E B` / `L B` | `+` | Requested slide state |
| Read temperature | `TG` | `+ 24.6`, for example | Unchanged |
| Set 25 °C | `TS 25` | `+` | Temperature control active |
| Deactivate temperature | `TS 0` | `+` | Temperature control inactive |
| Linear, low, 5 s | `SHAKE X 0 3 50 5` | `+ 0 0 // 0 0 //` | Shaking complete |
| Linear, medium, 5 s | `SHAKE X 0 3 40 5` | `+ 0 0 // 0 0 //` | Shaking complete |
| Orbital, high, 5 s | `SHAKE O 2 3 25 5` | `+ 0 0 // 0 0 //` | Shaking complete |
| Cancel active read | `STOP` | `- E140:` | Idle; SoftMax then ejects tray |

## Measurement setup

The {download}`example slide catalog <filtermax_example_slides.xml>` is a SoftMax XML export
containing definitions for excitation slides 1 and 2 and emission slide 1. The
[hello-world guide](hello-world.ipynb) shows how to load it with
`FilterSlideCatalog.from_softmax_xml()`, resolve a wavelength to a slide and position, and pass
the catalog to `FilterMaxF5(filter_slide_catalog=...)`. For absorbance, excitation slide 2 has
260 nm at position 1 and 450 nm at position 4. Use a catalog that matches the physical filters
installed in the reader; the example is not a universal FilterMax configuration.

The live-optimized Costar 96-well clear landscape geometry was sent as:

```text
PLATE Temp 12770 8570 1427 1069 1405 1118 12 8 902 900 640 640 0 300 0 1027 1 1
```

For luminescence at a 1 mm read height, the read-height field is `100`. The installed filter
definitions were sent as:

```text
SF A 2 260 12 1 340 10 1 405 10 1 450 8 1 595 8 1 620 8 1
SF B 1 595 35 2 535 25 2 535 25 4 535 25 4 625 35 10 0 0 16
```

## Reads

| Captured operation | Exact read payload | Result shape |
|---|---|---|
| Absorbance 450, full plate | `ABS 0 1 450 1 8 1 1 0 0 0 0 0 3 1 1 0 O e INFO` | One 12-value message per row, then `INFO` |
| Absorbance 450, portrait full plate | `SHIFT`, then `ABS 0 1 450 1 8 1 1 0 0 0 0 0 4 1 1 0 O e INFO` | Same `PLATE` geometry and canonical 8x12 result as landscape |
| Absorbance 450, A1:B3 | `ABS 0 1 450 1 2 1 1 0 0 0 0 0 3 1 1 0 O e INFO` | Two complete hardware rows; PLR masks columns 4-12 |
| Absorbance 450, C4:D6 | `ABS 0 1 450 3 4 1 1 0 0 0 0 0 3 1 1 0 O e INFO` | Two complete hardware rows; PLR masks unselected columns |
| Absorbance 450 minus 620 | `ABS 1 2 450 620 3 4 1 1 0 0 0 0 0 3 1 1 0 O e INFO` | Firmware-subtracted one-channel values |
| Absorbance 450, three-read kinetic | `ABS 3 1 450 3 4 1 1 0 0 0 0 0 3 1 3 2 O e INFO` | Repeated row messages; countdown field is 3, 2, 1 |
| Absorbance C4 horizontal, 28 points | `ABS 0 1 450 3 3 28 1 4 0 0 0 0 3 1 1 0 O e INFO` | 28 points for each of 12 hardware wells |
| Absorbance C4 fill density 5 | `ABS 0 1 450 3 3 5 5 4 0 0 0 0 3 1 1 0 O e INFO` | 21 circular-grid points for each of 12 hardware wells |
| Luminescence C4, 400 ms | `LUM 0 1 0 3 3 1 1 0 0 0 400000 0 3 1 1 0 O INFO` | One 12-value row message, then `INFO` |
| Dual luminescence C4, 400 ms | `LUM 1 2 0 0 3 3 1 1 0 0 0 400000 0 3 1 1 0 O INFO` | Two raw channel messages, then `INFO` |
| Luminescence, three-read kinetic | `LUM 3 1 0 3 3 1 1 0 0 0 400000 0 3 1 3 0 O INFO` | Repeated row messages; countdown field is 3, 2, 1 |

Absorbance values are returned in milli-OD and converted to OD. Luminescence values remain raw RLU.
SoftMax exposed only row-order reads, only horizontal/fill well scans, and one absorbance wavelength
optionally paired with one reference wavelength.

Portrait orientation has been captured for absorbance only. Other portrait measurement modes are
rejected before transmission rather than inferring their orientation fields.

## Direct PLR hardware validation

The driver was exercised directly on FilterMax F5 serial 1191 after releasing the Windows VM's
serial port. Identity, status, slide state, temperature, tray movement, excitation/emission slide
movement, linear/orbital shaking, and cancellation recovery all returned the expected final state
with an empty session error log.

The Orange G plate used for the live matrix produced the following representative PLR results:

| Operation | Direct result |
|---|---|
| Absorbance 450, full 96-well plate | 96 values; range 0.11214-0.16882 OD; mean 0.157792 OD |
| Absorbance 450, portrait full 96-well plate | 96 canonical values; range 0.03145-0.11605 OD; mean 0.089182 OD; Pearson correlation 0.99565 with the SoftMax portrait control |
| Absorbance 450, C4:D6 | 6 values; range 0.15885-0.16210 OD |
| Absorbance 450 minus 620, C4:D6 | 6 values; range 0.12460-0.12856 OD |
| Absorbance 450, three-read kinetic | 3 parsed timepoints and READY recovery |
| Horizontal scan, C4 | 28 ordered points |
| Fill density 5, C4 | 21 unique circular-grid points |
| Luminescence endpoint, C4 | One raw RLU value |
| Dual luminescence, C4 | Two preserved raw channels |
| Luminescence, three-read kinetic | 3 parsed timepoints |
| Luminescence with linear-medium pre-shaking | Completed and returned to READY |

The full-plate and partial absorbance values agreed with the corresponding SoftMax runs to the
precision expected for independent reads. This was a protocol and interaction validation, not an
OEM performance qualification: the OEM SpectraTest material was not available.

## Captured faults and remaining gaps

| Item | Status |
|---|---|
| LED energy failure | Captured as `E62`, including LED, ADC, gain, and potentiometer details |
| Cancellation | Captured as `E140`; reader remained connected and recoverable |
| Fluorescence, FRET, FP, TRF | Not mapped: the available excitation slide ID 2 contains absorbance filters only; the required excitation slide ID 1 was not physically available |
| OEM SpectraTest validation | Not performed: a clear 96-well Orange G plate was available instead |
| Microplate optimization | Interactive four-corner image scan was captured, but the image-analysis wizard is SoftMax UI behavior; PLR accepts the resulting physical plate geometry directly |
| Persistent firmware error log | No standalone query was observed; PLR reports exact errors received during its own session |

Uncaptured measurement commands are rejected before transmission. No SpectraMax `!COMMAND` payload
or inferred FilterMax command is used.
