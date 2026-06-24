"""Open-source picklist handling and Echo protocol generation (no vendor SDK required).

This module is intentionally free of any Labcyte/Beckman SDK code. It contains only:

* a parser for the **Echo cherry-pick picklist CSV** — a documented, public data format; and
* a *pluggable* protocol generator that turns transfers into the ``DoWellTransfer`` ``<wp>``
  layout of the (reverse-engineered, interoperability) Medman wire protocol.

The built-in :class:`NaiveEchoProtocolGenerator` emits transfers **in picklist order** and only
groups them by source plate type (a protocol requirement — one ``<SourcePlateName>`` per
``DoWellTransfer``). It deliberately does **not** reimplement the Echo Cherry Pick software's
proprietary transfer-order / head-travel optimisation or survey path-finding. Ordering does not
affect correctness — the instrument dispenses the same droplets regardless — only head-travel
efficiency. Users who own the vendor SDK can supply their own :class:`EchoProtocolGenerator`
(e.g. one that calls the SDK's ``PreProcessProtocol``) via the same interface.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

# Canonical picklist column names -> Transfer fields. Accepts the common header variants the
# Echo applications export (full names and the barcode-style headers).
_COLUMN_ALIASES: Dict[str, str] = {
  "source plate name": "source_plate_name",
  "source plate barcode": "source_plate_name",
  "source plate type": "source_plate_type",
  "source well": "source_well",
  "destination plate name": "dest_plate_name",
  "destination plate barcode": "dest_plate_name",
  "destination plate type": "dest_plate_type",
  "destination well": "dest_well",
  "volume": "volume_nl",
  "transfer volume": "volume_nl",
  "destination well x offset": "dest_x_offset_um",
  "destination well y offset": "dest_y_offset_um",
  "destxoffset": "dest_x_offset_um",
  "destyoffset": "dest_y_offset_um",
  "sample id": "sample_id",
  "sample name": "sample_name",
}


@dataclass
class Transfer:
  """One source-well -> destination-well transfer from a picklist."""

  source_well: str
  dest_well: str
  volume_nl: float
  source_plate_type: str = ""
  source_plate_name: str = ""
  dest_plate_name: str = ""
  dest_plate_type: str = ""
  dest_x_offset_um: int = 0
  dest_y_offset_um: int = 0
  sample_id: str = ""
  sample_name: str = ""


def _rc(well: str) -> Tuple[int, int]:
  """'A2' -> (row=0, col=1), 0-based. Standard plate well addressing."""
  letters = "".join(c for c in well if c.isalpha()).upper()
  digits = "".join(c for c in well if c.isdigit())
  row = 0
  for c in letters:  # supports AA.. for >26 rows
    row = row * 26 + (ord(c) - 64)
  return row - 1, int(digits) - 1


def read_picklist(path: str) -> List[Transfer]:
  """Parse an Echo cherry-pick picklist CSV into :class:`Transfer` objects (in file order)."""
  with open(path, newline="", encoding="utf-8-sig") as handle:
    rows = list(csv.DictReader(handle))
  return picklist_from_rows(rows)


def picklist_from_rows(rows: Iterable[Dict[str, str]]) -> List[Transfer]:
  transfers: List[Transfer] = []
  for raw in rows:
    mapped: Dict[str, str] = {}
    for key, value in raw.items():
      if key is None:
        continue
      field_name = _COLUMN_ALIASES.get(key.strip().lower())
      if field_name is not None and value is not None:
        mapped[field_name] = value.strip()
    if "source_well" not in mapped or "dest_well" not in mapped:
      continue
    transfers.append(
      Transfer(
        source_well=mapped["source_well"],
        dest_well=mapped["dest_well"],
        volume_nl=float(mapped.get("volume_nl", "0") or 0),
        source_plate_type=mapped.get("source_plate_type", ""),
        source_plate_name=mapped.get("source_plate_name", ""),
        dest_plate_name=mapped.get("dest_plate_name", ""),
        dest_plate_type=mapped.get("dest_plate_type", ""),
        dest_x_offset_um=int(float(mapped.get("dest_x_offset_um", "0") or 0)),
        dest_y_offset_um=int(float(mapped.get("dest_y_offset_um", "0") or 0)),
        sample_id=mapped.get("sample_id", ""),
        sample_name=mapped.get("sample_name", ""),
      )
    )
  return transfers


@dataclass
class GeneratedTransfer:
  """One ``DoWellTransfer`` worth of work: a source plate type and its protocol XML."""

  source_plate_type: str
  protocol_xml: str
  transfers: List[Transfer] = field(default_factory=list)


class EchoProtocolGenerator(ABC):
  """Turns a flat list of transfers into one or more ``DoWellTransfer`` protocol payloads.

  Implement this to plug in a different generation strategy (e.g. a vendor-SDK-backed generator
  that reproduces the Echo Cherry Pick optimisation exactly). The default
  :class:`NaiveEchoProtocolGenerator` is SDK-free and unoptimised.
  """

  @abstractmethod
  def generate(self, transfers: List[Transfer]) -> List[GeneratedTransfer]:
    ...


def _wp(oid: int, t: Transfer) -> str:
  sr, sc = _rc(t.source_well)
  dr, dc = _rc(t.dest_well)
  vol = int(t.volume_nl) if float(t.volume_nl).is_integer() else f"{t.volume_nl:g}"
  return (
    f'<wp oid="{oid}" v="{vol}" n="{t.source_well}" r="{sr}" c="{sc}" '
    f'dn="{t.dest_well}" dr="{dr}" dc="{dc}" '
    f'dx="{t.dest_x_offset_um}" dy="{t.dest_y_offset_um}" tag="" />'
  )


class NaiveEchoProtocolGenerator(EchoProtocolGenerator):
  """SDK-free generator: group by source plate type, emit ``<wp>`` in **picklist order**.

  Correct but not head-travel optimised — the vendor's transfer-ordering / survey path-finding is
  intentionally not reproduced here. Per-transfer XY offsets from the picklist are passed through.
  """

  def __init__(self, protocol_name: str = "pylabrobot"):
    self.protocol_name = protocol_name

  def generate(self, transfers: List[Transfer]) -> List[GeneratedTransfer]:
    groups: Dict[str, List[Transfer]] = {}
    for t in transfers:
      groups.setdefault(t.source_plate_type, []).append(t)  # first-appearance order preserved
    out: List[GeneratedTransfer] = []
    for source_type, group in groups.items():
      wps = "".join(_wp(i, t) for i, t in enumerate(group, 1))
      xml = (
        f'<Protocol Name="{self.protocol_name}"><Name></Name>'
        f"<SourcePlateName>{source_type}</SourcePlateName><Layout>{wps}</Layout></Protocol>"
      )
      out.append(GeneratedTransfer(source_plate_type=source_type, protocol_xml=xml, transfers=group))
    return out
