# SpectraMax Gemini EM

The Molecular Devices SpectraMax Gemini EM is a fluorescence plate reader controlled over a
serial RS-232 interface. PyLabRobot supports this reader with the
{class}`~pylabrobot.plate_reading.MolecularDevicesSpectraMaxGeminiEMBackend`.

## Installation

```bash
pip install pylabrobot[serial]
```

On Linux, the USB-to-RS-232 adapter will typically appear as `/dev/ttyUSB0` or `/dev/ttyUSB1`.
On Windows, it appears as a `COM` port.

```python
from pylabrobot.plate_reading import PlateReader, MolecularDevicesSpectraMaxGeminiEMBackend

backend = MolecularDevicesSpectraMaxGeminiEMBackend(port="/dev/ttyUSB0")
pr = PlateReader(name="gemini", backend=backend, size_x=0, size_y=0, size_z=0)

await pr.setup()
```

## Validated behavior

The Gemini EM backend was validated against a Molecular Devices SpectraMax Gemini EM using
a USB-to-RS-232 adapter. The instrument identified itself as:

```text
GEMINI EM
2.00b78 01Mar04
```

The following operations were validated:

- opening and closing the drawer
- querying reader status
- querying and setting temperature
- endpoint fluorescence reads on a 96-well plate
- endpoint fluorescence reads on a 384-well plate
- endpoint luminescence reads on a 96-well plate
- endpoint time-resolved fluorescence reads on a 96-well plate
- kinetic time-resolved fluorescence reads on a 96-well plate
- emission and excitation fluorescence spectrum reads on a 96-well plate
- fluorescence fill wellscan reads on a 96-well plate
- top and bottom fluorescence read-stage selection
- configurable excitation and emission wavelengths

The Windows/SoftMax Pro setup used during development was for protocol discovery only. The intended
deployment path is direct serial control from Linux using the USB-to-RS-232 adapter, typically as
`/dev/ttyUSB0` or `/dev/ttyUSB1`.

Absorbance and fluorescence polarization are not implemented for this backend.

## Support status

| Mode | Backend status | Hardware status |
| --- | --- | --- |
| Drawer open/close | implemented | validated |
| Status and temperature query | implemented | validated |
| Temperature setpoint | implemented | validated |
| Fluorescence endpoint | implemented | validated, 96-well and 384-well |
| Fluorescence top/bottom read | implemented | validated |
| Luminescence endpoint | implemented | validated, top read |
| Time-resolved fluorescence endpoint | implemented | validated |
| Time-resolved fluorescence kinetic | implemented | validated with a short run |
| Fluorescence emission spectrum | implemented | validated with a short sweep |
| Fluorescence excitation spectrum | implemented | validated with a short sweep |
| Fluorescence wellscan | implemented | validated with fill pattern |
| Rectangular partial-region reads | implemented | unit tested |
| Arbitrary non-rectangular partial reads | not implemented | not validated |
| Absorbance | not supported by Gemini EM | not applicable |
| Fluorescence polarization | not implemented | not validated |

## Fluorescence reads

Assign a plate to the reader before reading. The backend uses the plate resource geometry to
configure the reader's plate position.

```python
from pylabrobot.resources import Cor_96_wellplate_360ul_Fb

plate = Cor_96_wellplate_360ul_Fb(name="plate")
pr.assign_child_resource(plate)

data = await pr.read_fluorescence(
  plate=plate,
  wells=plate.get_all_items(),
  excitation_wavelength=485,
  emission_wavelength=520,
)
```

For a TRITC-like fluorescence read:

```python
data = await pr.read_fluorescence(
  plate=plate,
  wells=plate.get_all_items(),
  excitation_wavelength=557,
  emission_wavelength=576,
)
```

For a bottom read, pass `read_from_bottom=True`:

```python
data = await pr.read_fluorescence(
  plate=plate,
  wells=plate.get_all_items(),
  excitation_wavelength=485,
  emission_wavelength=520,
  read_from_bottom=True,
)
```

Full-plate reads and rectangular contiguous well regions are supported. Arbitrary non-rectangular
well selections raise `NotImplementedError`.

## Luminescence reads

Endpoint luminescence is available through the Gemini EM backend. The implementation follows the
SoftMax Pro command sequence captured during development, including `!READTYPE LUM`,
`!EMWAVELENGTH 0`, `!TOPREADCLEAR OFF`, and `!READSTAGE TOP`.

```python
data = await backend.read_luminescence(
  plate=plate,
  wells=plate.get_all_items(),
)
```

Optional pre-read shaking is supported:

```python
from pylabrobot.plate_reading.molecular_devices import ShakeSettings

data = await backend.read_luminescence(
  plate=plate,
  wells=plate.get_all_items(),
  shake_settings=ShakeSettings(before_read=True, before_read_duration=10),
)
```

Only endpoint, top-read luminescence is currently implemented.

## Time-Resolved Fluorescence

Endpoint time-resolved fluorescence is available through the Gemini EM backend. The implementation
uses the Gemini EM command form observed from SoftMax Pro:

```text
!READTYPE TIME <delay_time> <integration_time>
```

For example:

```python
data = await backend.read_time_resolved_fluorescence(
  plate=plate,
  excitation_wavelengths=[485],
  emission_wavelengths=[525],
  cutoff_filters=[7],
  delay_time=50,
  integration_time=850,
)
```

For a bottom TRF read:

```python
data = await backend.read_time_resolved_fluorescence(
  plate=plate,
  excitation_wavelengths=[485],
  emission_wavelengths=[525],
  cutoff_filters=[7],
  delay_time=50,
  integration_time=850,
  read_from_bottom=True,
)
```

Kinetic TRF is also supported with `ReadType.KINETIC` and `KineticSettings`:

```python
from pylabrobot.plate_reading.molecular_devices import KineticSettings, ReadType

data = await backend.read_time_resolved_fluorescence(
  plate=plate,
  excitation_wavelengths=[485],
  emission_wavelengths=[525],
  cutoff_filters=[7],
  delay_time=50,
  integration_time=850,
  read_type=ReadType.KINETIC,
  kinetic_settings=KineticSettings(interval=30, num_readings=21),
)
```

Endpoint and kinetic TRF are currently implemented for full plates and rectangular contiguous well
regions. Arbitrary non-rectangular well selections are not supported.

## Fluorescence Spectra

Gemini-specific helpers are available for fluorescence spectra. They use `SpectrumSettings`
internally but expose fixed-excitation and fixed-emission sweeps directly.

For an emission spectrum with fixed excitation:

```python
data = await backend.read_fluorescence_emission_spectrum(
  plate=plate,
  wells=plate.get_all_items(),
  excitation_wavelength=350,
  start_emission_wavelength=400,
  step=10,
  num_steps=36,
  read_from_bottom=True,
)
```

This sends the Gemini EM spectrum mode:

```text
!EXWAVELENGTH 350
!MODE EMSPECTRUM 400 10 36
!ORDER WAVELENGTH
```

For an excitation spectrum with fixed emission:

```python
data = await backend.read_fluorescence_excitation_spectrum(
  plate=plate,
  wells=plate.get_all_items(),
  emission_wavelength=600,
  start_excitation_wavelength=350,
  step=20,
  num_steps=4,
  read_from_bottom=True,
)
```

This sends:

```text
!EMWAVELENGTH 600
!AUTOFILTER EX OFF
!MODE EXSPECTRUM 350 20 4
!ORDER WAVELENGTH
```

The spectrum helpers default to cutoff filter `1`, matching the captured SoftMax Pro spectrum
protocols. Pass `cutoff_filters=[...]` to override this.

## Wellscan fluorescence

The Gemini EM backend includes a Gemini-specific `read_fluorescence_wellscan` method. SoftMax
Pro implements wellscan by enabling wellscan mode and performing separate reads at shifted plate
origins. The backend mirrors that behavior instead of averaging scan points silently.

```python
data = await backend.read_fluorescence_wellscan(
  plate=plate,
  wells=plate.get_all_items(),
  excitation_wavelength=485,
  emission_wavelength=525,
  pattern="fill",
)
```

Supported patterns are:

```text
horizontal
vertical
cross
fill
```

Each returned read includes `wellscan_point`, `wellscan_x`, and `wellscan_y` fields.

## 384-well plates

384-well fluorescence reads were validated with `Greiner_384_wellplate_28ul_Fb`:

```python
from pylabrobot.resources import Greiner_384_wellplate_28ul_Fb

plate = Greiner_384_wellplate_28ul_Fb(name="plate")
pr.assign_child_resource(plate)

data = await pr.read_fluorescence(
  plate=plate,
  wells=plate.get_all_items(),
  excitation_wavelength=557,
  emission_wavelength=576,
)
```

## OEM command observations

SoftMax Pro was used during development to observe the OEM serial behavior. The observed
bottom-read setup sequence was:

```text
!OPTION
!TEMP
!CLEAR DATA
!TAG OFF
!WELLSCANMODE
!XPOS 14.380 9 12
!YPOS 11.235 9 8
!SHAKE OFF
!SHAKE 0 0 0 0 0
!STRIP 1 12
!READTYPE FLU
!EMWAVELENGTH 525
!AUTOFILTER OFF
!EMFILTER 7
!EXWAVELENGTH 490
!FPW 6
!TOPREADCLEAR ON
!AUTOPMT ON
!CSPEED 8
!PMTCAL ON
!MODE ENDPOINT
!ORDER COLUMN
!READSTAGE BOT
!READ
```

The Gemini EM responds to `!READSTAGE BOT` and `!READSTAGE TOP` with a single `OK` response
field. This differs from some other Molecular Devices readers and is handled by the Gemini EM
backend.

SoftMax Pro sent `!TOPREADCLEAR ON` before selecting either read stage. The Gemini EM backend
does the same before sending `!READSTAGE TOP` or `!READSTAGE BOT`.

### Luminescence

SoftMax Pro used this sequence for a luminescence endpoint read with a 10 second pre-read shake:

```text
!CLEAR DATA
!TAG OFF
!WELLSCANMODE
!XPOS 14.380 9 12
!YPOS 11.235 9 8
!SHAKE ON
!SHAKE 10 0 0 0 0
!STRIP 1 12
!READTYPE LUM
!EMWAVELENGTH 0
!FPW 6
!TOPREADCLEAR OFF
!AUTOPMT ON
!CSPEED 8
!PMTCAL ON
!MODE ENDPOINT
!ORDER COLUMN
!READSTAGE TOP
!READ
```

### Time-resolved fluorescence

SoftMax Pro used this sequence for a TRF endpoint read:

```text
!TAG OFF
!WELLSCANMODE
!XPOS 14.380 9 12
!YPOS 11.235 9 8
!SHAKE ON
!SHAKE 10 0 0 0 0
!STRIP 1 12
!READTYPE TIME 50 850
!EMWAVELENGTH 525
!AUTOFILTER OFF
!EMFILTER 7
!EXWAVELENGTH 485
!FPW 6
!TOPREADCLEAR ON
!AUTOPMT ON
!CSPEED 8
!PMTCAL ON
!MODE ENDPOINT
!ORDER COLUMN
!READSTAGE BOT
!READ
```

For a TRF kinetic assay, SoftMax Pro used:

```text
!SHAKE ON
!SHAKE 5 30 27 3 0
!READTYPE TIME 50 850
!AUTOPMT OFF
!PMT MED
!MODE KINETIC 30 21
!READSTAGE TOP
!READ
```

During the kinetic run, SoftMax Pro repeatedly polled `!QUEUE` and called `!TRANSFER`.

### Fluorescence spectra

For an emission spectrum with fixed excitation, SoftMax Pro sent:

```text
!EXWAVELENGTH 350
!AUTOFILTER OFF
!EMFILTER 1
!MODE EMSPECTRUM 400 10 36
!ORDER WAVELENGTH
!READSTAGE BOT
!READ
```

For an excitation spectrum with fixed emission, SoftMax Pro sent:

```text
!EMWAVELENGTH 600
!AUTOFILTER OFF
!EMFILTER 1
!AUTOFILTER EX OFF
!MODE EXSPECTRUM 350 20 4
!ORDER WAVELENGTH
!READSTAGE BOT
!READ
```

### Partial-region selection

SoftMax Pro changed the selected wells by changing plate geometry and strip selection. One
captured partial-region read used:

```text
!XPOS 14.380 9 12
!YPOS 20.235 9 6
!STRIP 2 6
```

The Gemini EM backend maps rectangular contiguous well subsets to this command model. The selected
rows change the `!YPOS` origin and row count, while the selected columns change `!STRIP`. For
example, wells `B2:G7` on a 96-well plate map to row offset 1, six rows, strip 2, and six columns.

Arbitrary non-rectangular well selections are not supported. Wellscan uses explicit scan geometry
and does not yet infer scan geometry from `wells`.

### Wellscan patterns

SoftMax Pro enabled wellscan with:

```text
!WELLSCANMODE
!WELLSCANMODE ON
```

It then performed one read per scan point, changing `!XPOS` and `!YPOS` between reads. The
captured 3-point and 9-point scans used this coordinate model:

```text
center X = 14.380
center Y = 20.235
offset   = 1.133 mm

X positions = 13.247, 14.380, 15.513
Y positions = 19.102, 20.235, 21.368
```

The horizontal pattern order was:

```text
13.247, 20.235
14.380, 20.235
15.513, 20.235
```

The vertical pattern order was:

```text
14.380, 19.102
14.380, 20.235
14.380, 21.368
```

The cross pattern order was:

```text
14.380, 19.102
13.247, 20.235
14.380, 20.235
15.513, 20.235
14.380, 21.368
```

The fill pattern was a 3 by 3 row-major raster:

```text
13.247, 19.102
14.380, 19.102
15.513, 19.102
13.247, 20.235
14.380, 20.235
15.513, 20.235
13.247, 21.368
14.380, 21.368
15.513, 21.368
```

After the first scan point, SoftMax Pro did not resend the full optical setup. It resent position,
shake state, `!PMTCAL OFF`, `!STRIP`, and then `!READ`.

## Development notes

The backend intentionally lives in its own module,
`pylabrobot/plate_reading/molecular_devices/spectramax_gemini_em_backend.py`. It uses the shared
Molecular Devices serial protocol implementation where possible, while keeping Gemini-specific
behavior isolated from the SpectraMax M5 and SpectraMax 384 Plus backends.

During protocol discovery, a serial bridge/logger can be placed between SoftMax Pro and the
reader:

```text
SoftMax Pro -> virtual COM port -> Python bridge/logger -> physical serial port -> Gemini EM
```

This allows OEM traffic to be captured without changing the production backend.
