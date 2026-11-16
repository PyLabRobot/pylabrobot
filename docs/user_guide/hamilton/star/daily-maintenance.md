# Daily maintenance of independent channels

`DailyMaintenance` runs the positive-pressure, negative-pressure, and capacitive liquid-level
detection (cLLD) checks for standard 1000 µL channels on single-arm STAR and STARlet instruments.
It supports a selected subset of channels and reuses one to eight closed teaching needles in
batches. One needle is sufficient to check all channels.

A passing report covers the selected pipetting channels. The instrument's overall maintenance
record is not updated.

## Physical setup

Inspect and clean the deck, empty waste, seat the closed 300 µL teaching needles in their rack,
and close the cover. The script initializes the STAR before running the checks. Initialization
can eject mounted tools, so put reusable tools back in their racks before starting.

Use the conducting rear verification pad on the waste block for the Hamilton daily check.
The verification position is the pad's surface in deck coordinates.
Check that the travel path is clear below the selected needle-tip traverse height, which must
be at least 245 mm. A taller obstacle requires a higher reachable traverse height.

The default pad positions are:

| Model | X (mm) | Y (mm) | Z (mm) |
| --- | ---: | ---: | ---: |
| STAR | 1338.9 | 429.6 | 220 |
| STARlet | 798.9 | 429.6 | 220 |

Check that the pad matches these coordinates; the surface search spans only ±1 mm around Z.
For a different pad location, supply `verification_position=Coordinate(x, y, z)` in Python or
`--verification-position X Y Z` on the CLI. The resolved position is shown in the CLI plan and
saved with each run.

## Run from a script

From a PLR source checkout, initialize the STAR and check channels 3–5 using one needle:

```bash
python tools/qc/hamilton/star_daily_maintenance.py \
  --model STAR \
  --needle-position 1326.25 529 175 \
  --channels 3 4 5 --needle-count 1 --traverse-height 245 --execute
```

Use your instrument's needle position. `--needle-position` is the A1
needle center X/Y and collar support Z; the remaining rack positions retain their 9 mm pitch.
With one STAR connected, no serial number is needed. Use `--serial-number` to select a specific
instrument by its USB serial number, which can differ from its instrument serial number.

Omit `--execute` to preview the positions without connecting. Omit `--channels` to check every
installed standard channel; CLI channel numbers start at **1**.

Each connected run creates a timestamped JSON report and firmware log under `star-service-runs`
(change this with `--output-dir`). Parameter snapshots and partial measurements are saved as
the procedure progresses. A failed measurement returns a nonzero exit status; communication,
motion, and restoration errors raise and retain the partial report. On exit, the script stops
the driver, which retracts axes and parks accessories where possible.

## Call from Python

Given a calibrated `deck` and its `needle_rack`:

```python
from pylabrobot.hamilton.star.driver.master import STARDriver
from pylabrobot.hamilton.star.maintenance import DailyMaintenance
from pylabrobot.resources.tip_tracking import set_tip_tracking

set_tip_tracking(True)
driver = STARDriver(deck=deck)
try:
  await driver.setup()
  maintenance = DailyMaintenance(
    driver=driver,
    needles=[needle_rack.get_item("A1")],
    channels=[2, 3, 4],  # Python channel indices start at zero.
    traverse_height=245,
  )
  report = await maintenance.run()
  print(report.state)
finally:
  await driver.stop()
```

If `run()` raises, partial results remain available as `maintenance.report`. Create a new
`DailyMaintenance` instance for each run.

`on_update` accepts a synchronous callback receiving the report. Use it to persist results or
display progress; it must not issue instrument commands.

## Results

| Check | Pass condition |
| --- | --- |
| Positive pressure | Initial magnitude ≥3500 Pa; loss ≤400 Pa over 20 seconds |
| Negative pressure | Initial magnitude ≥3500 Pa; loss ≤400 Pa over 20 seconds |
| cLLD | No detection in air; detection on the verification pad |

Pressure checks build toward 4000 Pa and settle for 2 seconds before the first measurement.
Needles are returned between pressure checks and after cLLD. Pressure values in reports are in Pa.

The routine checks mounted tools and traverse clearance before lateral travel. It may raise
selected channels and move other channels in Y to maintain spacing. An unexpectedly low
unselected channel aborts the checks.

On an error or cancellation, the routine attempts to restore channel settings. Needles may remain
mounted: inspect the report and resolve the fault before returning them. A lost connection or
terminated process can prevent restoration; saved settings are included in the report.
