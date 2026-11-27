"""The exact bytes each step type sends.

Every payload is frozen: the table in ``test_data/payloads.tsv`` holds a definition and the payload
the step it reads must send, and each row was checked against an independent implementation of the
same wire format before it was written down. A change to these bytes is a change to what reaches an
instrument, not a change to a test fixture.

Most rows are definitions out of the protocol files in ``test_data`` -- real parameter combinations,
which is what makes the table worth having. The rest are constructed to reach what those files do
not: every member of each vocabulary, signed offsets at their limits, volume boundaries, and the two
step types no protocol in the tree happens to use.

Structural properties of the payloads -- their lengths, what the fitted options move, what a step a
wash owns leaves out -- are in ``step_payload_tests.py``. This file is only about exact bytes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.secondary_aspirate_pattern import (
  SECONDARY_ASPIRATE_PATTERN_TO_BYTE,
)
from pylabrobot.agilent.biotek.lhc.enums.steps.travel_rate import TravelRate
from pylabrobot.agilent.biotek.lhc.protocols.steps import step_from_definition
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import (
  PreDispense,
  SecondaryAspirate,
  Sectors,
  Shake,
  Soak,
  VacuumDelay,
  WashStages,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps import STEP_CLASSES
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_aspirate import ManifoldAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_dispense import ManifoldDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_wash import ManifoldWash
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.shake_soak import ShakeSoak

SETTINGS = InstrumentSettings()
"""What these payloads are encoded against: a plainly equipped instrument of the first model."""

TABLE = Path(__file__).parent / "test_data" / "payloads.tsv"
"""Where the frozen payloads live: one step class, definition and payload per line."""


def frozen() -> list[tuple[str, str, str]]:
  """Every frozen payload.

  Returns:
    The step class, the definition, and the payload it must send, one per row.
  """
  rows = []
  for line in TABLE.read_text().splitlines():
    if not line.strip() or line.startswith("#"):
      continue
    kind, definition, payload = line.split("\t")
    rows.append((kind, definition, payload))
  return rows


ROWS = frozen()
"""The table, read once."""


def test_the_table_covers_every_step_type():
  """A step type absent from the table is one whose bytes nothing here would notice changing."""
  covered = {kind for kind, _, _ in ROWS}
  assert {cls.__name__ for cls in STEP_CLASSES.values()} <= covered


def test_the_table_has_no_repeated_definitions():
  """The table has no repeated definitions."""
  definitions = [definition for _, definition, _ in ROWS]
  assert len(set(definitions)) == len(definitions)


@pytest.mark.parametrize(
  "definition, expected",
  [(definition, payload) for _, definition, payload in ROWS],
  ids=[f"{kind}-{index}" for index, (kind, _, _) in enumerate(ROWS)],
)
def test_a_step_sends_the_bytes_it_should(definition: str, expected: str):
  """A step sends the bytes it should."""
  assert step_from_definition(definition).to_bytes(SETTINGS).hex() == expected


def test_every_secondary_aspirate_pattern_has_a_wire_value():
  """Two of them are values the instrument's own editor cannot produce, but the wire value for all
  four is known, so a caller building a step outright can reach them."""
  assert SECONDARY_ASPIRATE_PATTERN_TO_BYTE == {"None": 0, "Point": 1, "Circle": 2, "Square": 3}


# --- a wash, whose payload is five steps of its own and where every offset has somewhere to go.
#
# The four arguments below are the ones worth being explicit about: where in the well each of the
# two aspirates works, and where a second aspirate would go. They belong to different sections of
# the payload, and getting them the wrong way round puts a height into the field of a different
# step -- which is the sort of mistake that only shows up as a manifold in the wrong place.


def wash(
  asp_x: int = 0,
  asp_y: int = 0,
  asp_z: int = 29,
  sec_z: int = 29,
  final_sec_z: int = 29,
  cycles: int = 3,
  volume: int = 300,
  flow: int = 7,
  disp_z: int = 121,
  travel: str = "3",
) -> ManifoldWash:
  """Build a wash from the arguments the cases below vary.

  Args:
    asp_x: Offset across the well for both aspirates.
    asp_y: Offset along the well for both aspirates.
    asp_z: Aspirating height for both aspirates.
    sec_z: Height a second aspirate would work at, on the aspirate that starts each cycle.
    final_sec_z: The same, on the aspirate after the last cycle.
    cycles: How many wash cycles to run.
    volume: Volume per well in µL.
    flow: How fast to dispense.
    disp_z: Dispensing height.
    travel: How fast the tips descend.

  Returns:
    The wash.
  """

  def aspirate(secondary_z: int) -> ManifoldAspirate:
    """One of the wash's two aspirates.

    Args:
      secondary_z: Where a second aspirate would go.

    Returns:
      The aspirate, marked as one a wash owns.
    """
    return ManifoldAspirate(
      in_wash=True,
      travel_rate=cast(TravelRate, travel),
      delay=0,
      positioning=Positioning(z_steps=asp_z, x_steps=asp_x, y_steps=asp_y),
      secondary=SecondaryAspirate(pattern="None", positioning=Positioning(z_steps=secondary_z)),
    )

  def dispense() -> ManifoldDispense:
    """One of the wash's two dispenses.

    Returns:
      The dispense.
    """
    return ManifoldDispense(
      buffer="A",
      volume=volume,
      flow_rate=flow,
      positioning=Positioning(z_steps=disp_z),
      pre_dispense=PreDispense(enabled=False, volume=0, flow_rate=9),
      vacuum=VacuumDelay(enabled=False, volume=0),
    )

  return ManifoldWash(
    wash_format="Plate",
    sectors=Sectors(),
    cycles=cycles,
    stages=WashStages(final_aspirate=True),
    bottom_wash=dispense(),
    aspirate=aspirate(sec_z),
    dispense=dispense(),
    shake_soak=ShakeSoak(
      shake=Shake(enabled=False, duration=0),
      soak=Soak(enabled=False, duration=0),
      move_carrier_home=False,
    ),
    final_aspirate=aspirate(final_sec_z),
  )


WASHES: list[tuple[str, dict[str, Any], str]] = [
  (
    "every argument at its default",
    {},
    "0001000f0003412c01070000790000000900000000000000000000000300001d000000001d0000000000000000000000000300001d000000001d000000000000000000412c0107000079000000090000000000000000000000030000000000000000000000",
  ),
  (
    "asp_x=8",
    {"asp_x": 8},
    "0001000f0003412c01070000790000000900000000000000000000000308001d000000001d0000000000000000000000000308001d000000001d000000000000000000412c0107000079000000090000000000000000000000030000000000000000000000",
  ),
  (
    "asp_y=-4",
    {"asp_y": -4},
    "0001000f0003412c01070000790000000900000000000000000000000300fc1d000000001d0000000000000000000000000300fc1d000000001d000000000000000000412c0107000079000000090000000000000000000000030000000000000000000000",
  ),
  (
    "asp_z=25",
    {"asp_z": 25},
    "0001000f0003412c010700007900000009000000000000000000000003000019000000001d00000000000000000000000003000019000000001d000000000000000000412c0107000079000000090000000000000000000000030000000000000000000000",
  ),
  (
    "sec_z=12",
    {"sec_z": 12},
    "0001000f0003412c01070000790000000900000000000000000000000300001d000000001d0000000000000000000000000300001d000000000c000000000000000000412c0107000079000000090000000000000000000000030000000000000000000000",
  ),
  (
    "final_sec_z=14",
    {"final_sec_z": 14},
    "0001000f0003412c01070000790000000900000000000000000000000300001d000000000e0000000000000000000000000300001d000000001d000000000000000000412c0107000079000000090000000000000000000000030000000000000000000000",
  ),
  (
    "cycles=5",
    {"cycles": 5},
    "0001000f0005412c01070000790000000900000000000000000000000300001d000000001d0000000000000000000000000300001d000000001d000000000000000000412c0107000079000000090000000000000000000000030000000000000000000000",
  ),
  (
    "volume=250",
    {"volume": 250},
    "0001000f000341fa00070000790000000900000000000000000000000300001d000000001d0000000000000000000000000300001d000000001d00000000000000000041fa0007000079000000090000000000000000000000030000000000000000000000",
  ),
  (
    "flow=3",
    {"flow": 3},
    "0001000f0003412c01030000790000000900000000000000000000000300001d000000001d0000000000000000000000000300001d000000001d000000000000000000412c0103000079000000090000000000000000000000030000000000000000000000",
  ),
  (
    "travel=5",
    {"travel": "5"},
    "0001000f0003412c01070000790000000900000000000000000000000500001d000000001d0000000000000000000000000500001d000000001d000000000000000000412c0107000079000000090000000000000000000000030000000000000000000000",
  ),
  (
    "disp_z=118",
    {"disp_z": 118},
    "0001000f0003412c01070000760000000900000000000000000000000300001d000000001d0000000000000000000000000300001d000000001d000000000000000000412c0107000076000000090000000000000000000000030000000000000000000000",
  ),
  (
    "asp_x=8, asp_y=-4, asp_z=25, sec_z=12, final_sec_z=14",
    {"asp_x": 8, "asp_y": -4, "asp_z": 25, "sec_z": 12, "final_sec_z": 14},
    "0001000f0003412c01070000790000000900000000000000000000000308fc19000000000e0000000000000000000000000308fc19000000000c000000000000000000412c0107000079000000090000000000000000000000030000000000000000000000",
  ),
]
"""Each wash, and the payload it sends."""


@pytest.mark.parametrize(
  "arguments, expected",
  [(arguments, expected) for _, arguments, expected in WASHES],
  ids=[label for label, _, _ in WASHES],
)
def test_a_wash_sends_the_bytes_it_should(arguments: dict[str, Any], expected: str):
  """A wash sends the bytes it should."""
  assert wash(**arguments).to_bytes(SETTINGS).hex() == expected


def test_each_aspirate_carries_its_own_secondary_height():
  """The two heights land in different sections, so setting one must not move the other."""
  first = wash(sec_z=12).to_bytes(SETTINGS)
  last = wash(final_sec_z=12).to_bytes(SETTINGS)
  assert first != last
  assert first != wash().to_bytes(SETTINGS)
  assert last != wash().to_bytes(SETTINGS)


def test_the_aspirate_offsets_reach_both_aspirates():
  """Both aspirates work the same place in the well, so one offset changes two sections."""
  plain = wash().to_bytes(SETTINGS)
  offset = wash(asp_x=8).to_bytes(SETTINGS)
  differing = [index for index, (a, b) in enumerate(zip(plain, offset)) if a != b]
  assert len(differing) == 2, differing
