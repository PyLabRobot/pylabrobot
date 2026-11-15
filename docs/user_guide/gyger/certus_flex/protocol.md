# CERTUS FLEX protocol and verification

The driver provides direct Ethernet and INCO server connections, motion, pressure
control, timed or calibrated dispensing, and valve maintenance. The Python API
uses millimetres, seconds, and microlitres; pressure is explicitly specified in bar.

## Verification scope

Hardware evidence uses dispenser **C196069**, firmware **1.0.7.378-trunk**, with an
angled FLEX8 head. The fixtures retain controller replies for regression testing.

| Operation | Verification |
| --- | --- |
| Direct UDP | Identity, status, head, signed position/pressure reads; reconnect and sequence rollover |
| Motion | A1–A3 dry traversal at park height; B2/C3 positioning at 35.4 mm Z; return to park |
| Pressure | Regulation to 0.2 bar and discharge to approximately zero |
| Timed valves | Water flow through channels 2/3; visible water in B2/C3 after 1 ms pulses |
| INCO server TCP | Native request fixtures and simulated responses; no hardware verification |
| Prime, purge, clean cycle | Command sequencing and failure tests; native maintenance commands unverified on hardware |
| Volume-to-time conversion | Vendor-source reference cases cover interpolation, volume correction, units, and the valve command; physical volume accuracy depends on the supplied calibration |

Reference-axis motion, the full travel envelope, other head types, and cleaning
effectiveness remain unverified. Instrument calibration and cleaning effectiveness
are separate from driver command verification and depend on the installed valves and fluid.

## Connection

With `target` omitted, the Python client connects directly to the **controller**
through `pylabrobot.io.UDP`, normally on UDP port **1964**. Put the computer's wired
interface on the controller's subnet; `source_ip` can select the local interface
when necessary. The tested controller was at `192.168.1.3`, with the computer at
`192.168.1.2/24`; these are observed addresses, not assumed factory defaults.

```python
from pylabrobot.gyger import CertusFlex

certus = CertusFlex("192.168.1.3", head="flex8_angled")
```

With `target` set, the client uses `pylabrobot.io.Socket` to reach the **INCO server
on the control PC**, normally on TCP port **7979**. `target` is the local target
name configured in that server. It is separate from `host`, the server PC's
address. The server must have incoming TCP access enabled and be able to reach
the controller. Neither transport loads a Windows DLL or requires SiLA.

Remote target chains, INCO discovery, fragmented transfers,
asynchronous procedure tickets, and protocol-file upload are outside this
implementation. Only the synchronous get/put/procedure operations used below are
implemented. Unsupported replies cause an error rather than an automatic retry.

## Evidence

The device paths, commands, state transitions, and channel mappings were recovered
from CERTUS CONTROL **2.1.17.2517**. Transport framing was recovered from
`inco_32.dll` and its matching PDB in `IndelToolsSetup-tools_19.2.0.exe`, distributed
inside the manufacturer's
[matching installer](https://downloads.fgyger.ch/certus-flex/GUI/Certus%20CONTROL%202.1.17.2517/Certus%20CONTROL%20Setup%202.1.17.2517.zip).
The supplied **User Manual CERTUS FLEX EN** gives the 0.051–1.0 bar dispensing
pressure range; the machine's valve and fluid requirements still apply.

| Reference file | SHA-256 |
| --- | --- |
| `Gyger.IndelInterface.dll` | `a73215827ff28b9b05bd8c533a84bae8c392fc5cfed38fa1422d70c40a031594` |
| `Gyger.Certus.DispenserCore.dll` | `8d853fd26b6f1a8c4cd55209646018953cb2d7d5e164954417b186b89777e9c4` |
| `inco_32.dll` | `8e42a7ab422c8e6845d38738d9af09c23852229f76e1406a6627b2294980e6d4` |
| `inco_32.pdb` | `60cd7c1351d1935cffbad5030c03e982f78b6ccfd207e8347da15b0565e57d0d` |

`pylabrobot/gyger/certus_flex/fixtures/native_requests.json` contains eight complete
requests produced by offline x86 emulation of that DLL. They cover numeric and
string reads, a numeric write, positive and negative motion coordinates, fractional
values, string and empty-string arguments, a timed pulse, and a zero-argument
completion barrier. The fixtures are not produced by the Python encoder.

The `hardware_udp_*.json` fixtures in that directory contain real controller
exchanges for reads, start/park procedures, numeric plate moves, pressure writes,
timed valve commands, completion barriers, and discharge. Timeouts, cancellation,
malformed packets, and lost replies use a simulated transport; UDP datagram
handling is tested on loopback sockets.

The native call sequence is `CMcINCOFrameWithBuffer` construction, address
generation with sender `.` and server insertion, frame number 1, operation
generation, then `AdjustToNextTarget` as performed by `TransmitINCOFrame`.
Useful virtual addresses in this specific x86 binary are:

| Native function | Address |
| --- | --- |
| `GenerateFrameAddress` | `0x1001c070` |
| `CMcINCOFrameWithBuffer` constructor | `0x1001c7d0` |
| `CMcINCOFrame::SetFrameNumber` | `0x1001df90` |
| `CMcINCOFrameGenerator::GetVariable` | `0x10022de0` |
| `CMcINCOFrameGenerator::PutVariable` | `0x10023810` |
| `CMcINCOFrameGenerator::CallProcedure` | `0x100210f0` |
| `CMcINCOFrameParser::GetVariable` | `0x100242f0` |
| `CServerComm::TransmitINCOFrame` | `0x100175a0` |
| `CThreadCommDataTcp::WriteFrame` | `0x10018790` |

## Direct UDP wire format

The tested controller uses classic INCO framing, not the extended TCP frame below.
Each request is one datagram: `DLE SOH`, an eight-byte header (sequence, operation,
subtype, destination count 1, destination `FF`, source count 1, source `00`, checksum),
payload, `DLE ETX`, and a data checksum. Literal `DLE` bytes are doubled. The header
sum, including `SOH` and its checksum, is `FF` modulo 256. The payload sum, including
`ETX` and its checksum, is also `FF` modulo 256; escape bytes are excluded.

An ACK begins `DLE ACK`, then sequence, a two-byte error, destination count 1,
destination `00`, and header checksum. Its payload and trailer use the same
escaping and checksum rule. A nonzero error is exposed as `0x50000 + error`.
The source stays `00`. Sequence numbers run from 1 to 255; the client reopens its
socket before reusing a sequence. Valid replies with a different sequence are
ignored under the original request deadline. Requests are never retransmitted.

Numeric and string reads are `2/1` and `2/5`, with a NUL-terminated path and no
padding. Strings return NUL-terminated Latin-1 bytes. Numeric responses use
legacy 32.32 fixed point, optionally followed by type `0D`, or an eight-byte
double followed by type `09`. The low fraction bit is a legacy sign flag and is
removed from the numeric value. Short procedure results may use 16.16 fixed point.

Numeric put (`2/0`) contains an eight-byte signed 32.32 value, the terminated path,
and type `0D`. A zero-argument procedure (`4/0`) contains a four-byte zero count
and a path padded to four bytes. Procedures with arguments (`4/1`) contain a
four-byte count, four-byte type bitmap, padded path, then arguments. Numeric
arguments set bitmap bits `i` and `16+i` and contain signed 32.32 values; string
arguments set bit `16+i` and contain terminated strings padded to four bytes.
At most 16 arguments and 494 payload bytes are supported.

This format was recovered by static inspection of `ConvertVariableToClassicFrame`
(`0x1001d460`), `ConvertCallProcedureToClassicFrame` (`0x1001cbe0`),
`ConvertFromClasicFrame` (`0x1001d070`), and the classic frame generator/parser
(`0x100280d0` / `0x100283f0`) in the referenced binary. Numeric writes and numeric
procedure arguments also have hardware-capture coverage for the operations in
the verification table.

## INCO server TCP wire format

Each TCP message consists of a 52-byte header, an optional extended header, an
address block, and data. TCP may split any of these across reads. There is no
additional TCP length prefix. Multi-byte header fields follow the endianness bit
at byte 32; requests use big endian.

| Offset | Size | Meaning |
| --- | --- | --- |
| 0 | 13 | Compatibility bytes `100100820101ff01007a1003fc` |
| 13 | 2 | Content interface/feature versions: 2, 3 |
| 15 | 2 | Receiver interface/feature versions |
| 17 | 1 | Transaction: request 1, reply 6 |
| 18 | 2 | Operation type and subtype |
| 20 | 4 | Frame number |
| 24 | 4 | Fragment count and fragment index, two 16-bit values |
| 28 | 4 | Unsigned INCO error code |
| 32 | 4 | Flags; bit 0 of byte 32 selects big endian |
| 36 | 4 | Spare |
| 40 | 4 | Target startup timestamp |
| 44 | 2 | INCO path offset |
| 46 | 6 | Extended-header, address, and data lengths, three 16-bit values |

The outgoing address block begins `01 20 03 01 00 00 00 00`, followed by three
32-byte, NUL-padded slots: `.`, `...`, and the target name. These are direction,
slot width, slot count, current index, reserved bytes, and the route respectively.

Numeric get is type/subtype `2/1`; string get is `2/5`. Both contain four zero
bytes followed by a NUL-terminated path, padded to a four-byte boundary. Numeric
put is `2/0`, containing a typed number followed by the NUL-terminated path.
Procedure call is `4/1`: one argument-count byte, three zero bytes, padded path,
then typed arguments. Numeric arguments contain type `0x010f`, two padding bytes,
an IEEE double and a signed 32.32 fallback. String arguments contain type
`0x0200`, two padding bytes, and a padded NUL-terminated string.

Numeric responses contain type `0x0109` (double) or `0x010d` (fixed point), two
padding bytes, and eight value bytes. Fixed-point words are integer then fraction,
even in little endian replies. String responses use type `0x0202`, two padding
bytes, a length split into low and high 16-bit words, and the terminated string.
The INCO error is checked before decoding a value and exposed as `IncoError.code`.

## Device operations

The root is `Machine.Entity.Dispenser`. Module paths begin
`Machine.Entity.Dispenser.Module.Entity.`. `.Prop.State` values are startup=0,
off=1, wait=2, on=3, ready=4, busy=5, error=6. The coordinator state is
`Machine.Entity.Dispenser.Coord.Prop.State`.

| Operation | Controller path or command |
| --- | --- |
| Serial number | `Machine.Entity.Dispenser.Act.Serialnumber` |
| Firmware | `Software.Revision.Dispenser` |
| Start | `Machine.Entity.Dispenser.Cmd.Start()` |
| Reference axes | `PosSystem.Cmd.Init("")` |
| Position | `PosSystem.Act.Position.0`, `.1`, `.2` in mm |
| Move | `PosSystem.Cmd.MoveXYZ(x:d,y:d,z:d,"Slow")` |
| Park | `PosSystem.Cmd.MoveZToPark("Slow")`, then `MoveXYToPark("Slow")` |
| Prime/purge position | Park Z, `MoveXYToPrime`/`MoveXYToPurge`, then matching Z command |
| Pressure | `PressureRegulator.Prop.SetPressure`, `.Act.Pressure`, in bar |
| Pressurize/discharge | `PressureRegulator.Cmd.Init()`, `.Cmd.Discharge()` |
| Pulse | `ValveN.Cmd.Dispens(microseconds:d)` |
| Prime | `ValveN.Cmd.Prime(microseconds:d)`; bounded by `ValveN.Prop.PrimeTimeout` |
| Purge | `ValveN.Cmd.Purge(total_open_microseconds:d,pulse_microseconds:d[,wait_microseconds:d])` |
| Close valve | `ValveN.Cmd.CloseValve()` |
| Await pulses | Set `Head.Prop.SyncValves` to 0, call `Head.Cmd.SyncValves()`, poll flag > 0 |

The first head channel is `valves[0]`. FLEX5 maps to outputs 1–5; FLEX8 to 0–7;
angled FLEX4 to 0–3; angled FLEX8 to 0–7. The installed head is an explicit
constructor argument; the driver does not infer it from a serial number.

Motion waits follow the vendor's five-second acceptance observation: wait to see
Ready depart, then wait for Ready to return. A sufficiently short move can finish
between reads, so Ready after the observation period is also accepted. The
timeout must allow that grace period. Fault states raise immediately. Pressure
completion also requires a charged/idle charge state and actual pressure within
0.01 bar of the requested value.

Speed names select firmware profiles, such as `"slow"` mapping to `"Slow"`.
A numeric mm/s command interface has not been verified.

Durations use seconds at the Python API and microseconds on the controller. The
protocol validator's minimum is 50 µs; individual valves may require longer.
Volume dispensing requires a measured per-valve/per-fluid `ValveCalibration` in
µL and seconds. Setpoint and actual pressure must match that calibration.

`dispense(volume, correction_factor=1.0)` follows CERTUS CONTROL's volume-to-time
calculation: multiply the requested volume by the channel's correction factor,
then linearly interpolate between the surrounding calibration points. The factor
range is 0.5–1.5. The factor applies to volume, not opening duration. PLR converts
the resulting seconds to microseconds for the same native `ValveN.Cmd.Dispens`
command used by CERTUS CONTROL. It does not read a control PC's calibration files
or correction factors automatically.

Reference tests cover both interpolation segments, exact endpoints, factor limits,
and the resulting microsecond command. Their synthetic data checks the calculations;
it is not calibration data for an installed valve. For example, points of 0.5 µL
at 500 µs and 1.5 µL at 1250 µs map a 1 µL request with factor 1.25 to 1062.5 µs.
The reference methods are `DispenseCalibration.CalculateValveOpenTime` and
`CalibrationUtils.InterpolateValveOpenTime` in CERTUS CONTROL 2.1.17.2517.
Unlike the vendor's extrapolation, PLR rejects corrected volumes outside the
measured range. This verifies the command calculation, not the actual dispensed
mass or volume; those require measurements for the installed fluid and valve.

Motion and valve operations share a lock. The calibration pressure check is held
under that same lock. This does not prevent another application from controlling
the machine; use one controlling application at a time. A transport timeout,
cancellation during an exchange, or malformed reply closes the socket and requires
explicit reconnection. None of those outcomes proves that a physical operation
stopped. A failed dose must be investigated before another dispense is requested.
`stop()` disconnects only; use `set_pressure(0)` explicitly to discharge.

## Valve maintenance

`valves[i].prime(duration)` opens continuously at the current position.
`valves[i].purge(duration, pulse_duration=..., interval=...)` requests a pulse
train timed by the controller. All times are in seconds. Purge `duration` is
cumulative valve-open time; it excludes gaps between pulses. Omit `interval` to
use the firmware's timing. Explicit intervals need the three-argument command;
unsupported requests raise without retrying another command form.

`clean_cycle()` moves to the configured purge position and repeats purge, a
250 ms pause, and prime. It processes selected channels sequentially, waits for
each operation, and parks on success. `channels` uses zero-based valve indexes.
Pressure is controlled separately and remains unchanged by cleaning:

```python
await certus.initialize()
await certus.set_pressure(0.2)
try:
  await certus.clean_cycle(
    channels=[1, 2],
    prime_duration=0.1,
    purge_duration=0.02,
    purge_pulse_duration=0.001,
    cycles=1,
  )
finally:
  await certus.set_pressure(0)
```

The default cycle count is five. These durations are a functional-test example,
not a validated cleaning recipe. Use fluid-compatible cleaning conditions and
waste collection. Calibrated volumes require measured valve/fluid calibration.
On failure or cancellation, cleaning attempts to stop the selected valves and
raises the original error without repeating doses or making a recovery move.

## Continuing hardware verification

Use the [hello-world notebook](hello-world.ipynb) one cell at a time. Confirm the
controller address (or server/target) and installed head, capture identity and status reads, and compare
positions and pressure with CERTUS CONTROL. After those match, verify reference
and park motions with a clear workspace, then a short water pulse into waste.
Measure delivered volume separately before supplying a calibration. Preserve
the PLR capture and record firmware, head, valve, pressure, pulse duration, and
observations. Automatic plate-to-well coordinate conversion is not implemented;
motion methods currently take controller coordinates.
