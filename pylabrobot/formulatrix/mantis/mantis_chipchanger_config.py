"""Per-machine chip-changer dock paths, loaded from the vendor install.

Chip geometry is machine-specific and lives in the vendor install, NOT in code:

* ``Data/Device/Configs/ChipChanger.config`` maps each chip to its sequence
  files (``ChipChanger.N.SafeStartSequence = SafeStart3.seq.txt`` ...).
* ``Data/System/Sequences/ChipChanger/*.seq.txt`` hold the actual move waypoints.

This composes a chip's ATTACH (pickup) path in the same order the vendor
``MantisInputManager`` uses — SafeStart → MoveAttach → Origin → Shift → SafeStop —
resolving each ``MoveSequenceItem`` (absolute ``X/Y/Z`` or relative ``dX/dY/dZ``,
unspecified axes hold their running value). The head is pressed to the Origin
plunge depth and HELD ``ATTACH_DWELL_S`` (a passive magnet captures the chip — no
valve/PPI fires; verified against the vendor prime sequence). Composition verified
byte-exact against that sequence.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Axis token in a MoveSequenceItem line, e.g. "X -55.254" or "dX 12". The
# negative-lookbehind keeps the X in "dX" from also matching as a bare "X".
_AXIS = re.compile(r"(?<![A-Za-z])(dX|dY|dZ|X|Y|Z)\s+(-?\d+(?:\.\d+)?)")

# Vendor MantisInputManager._sequenceNameList (attach order).
_ATTACH_ORDER = (
  "SafeStartSequence",
  "MoveAttachSequence",
  "OriginSequence",
  "ShiftSequence",
  "SafeStopSequence",
)

# Hold at the Origin plunge for the passive magnet to capture the chip. Observed
# ~1.43 s in the vendor prime sequence; rounded slightly up for margin.
ATTACH_DWELL_S = 1.45

_CONFIG_REL = os.path.join("Data", "Device", "Configs", "ChipChanger.config")
_SEQ_REL = os.path.join("Data", "System", "Sequences", "ChipChanger")

Waypoint = Tuple[float, float, float]


@dataclass
class ChipAttachPath:
  """An absolute machine-frame dock path for picking up one chip."""

  chip_number: int
  waypoints: List[Waypoint]
  dwell_index: int  # hold ATTACH_DWELL_S after reaching this waypoint (the plunge)
  dwell_s: float = ATTACH_DWELL_S


def _read_config(path: str) -> Dict[str, str]:
  cfg: Dict[str, str] = {}
  with open(path, encoding="utf-8-sig") as fh:
    for line in fh:
      m = re.search(r'<add key="([^"]+)" value="([^"]+)"', line)
      if m:
        cfg[m.group(1)] = m.group(2)
  return cfg


def _parse_seq(path: str, start: Waypoint) -> Tuple[List[Waypoint], Waypoint]:
  """Parse a .seq.txt into absolute waypoints, threading the running position."""
  x, y, z = start
  wps: List[Waypoint] = []
  with open(path, encoding="utf-8-sig") as fh:
    for line in fh:
      line = line.strip()
      if not line.startswith("MoveSequenceItem"):
        continue
      pairs = _AXIS.findall(line)
      if not pairs:
        continue
      for axis, val in pairs:
        v = float(val)
        if axis == "X":
          x = v
        elif axis == "Y":
          y = v
        elif axis == "Z":
          z = v
        elif axis == "dX":
          x += v
        elif axis == "dY":
          y += v
        elif axis == "dZ":
          z += v
      wps.append((x, y, z))
  return _dedup(wps), (x, y, z)


def load_attach_path(
  install_root: str, chip_number: int, start: Waypoint = (0.0, 0.0, 0.0)
) -> ChipAttachPath:
  """Build the pickup path for ``chip_number`` from the vendor install at
  ``install_root`` (the folder containing ``Data/``)."""
  cfg = _read_config(os.path.join(install_root, _CONFIG_REL))
  seq_dir = os.path.join(install_root, _SEQ_REL)

  pos = start
  waypoints: List[Waypoint] = []
  dwell_index: Optional[int] = None
  for seq_key in _ATTACH_ORDER:
    fname = cfg.get(f"ChipChanger.{chip_number}.{seq_key}")
    if not fname:
      continue
    seq_wps, pos = _parse_seq(os.path.join(seq_dir, fname), pos)
    waypoints.extend(seq_wps)
    if seq_key == "OriginSequence":
      dwell_index = len(waypoints) - 1

  if not waypoints:
    raise ValueError(f"No chip-changer sequences found for chip {chip_number} in {install_root}")
  # Drop consecutive-identical waypoints (no-op moves to where the head already
  # is — the vendor files list the plunge twice, etc.). The dwell goes at the
  # plunge, the deepest-Z waypoint.
  waypoints = _dedup(waypoints)
  max_z = max(z for _, _, z in waypoints)
  dwell_index = max(i for i, (_, _, z) in enumerate(waypoints) if abs(z - max_z) < 1e-6)
  return ChipAttachPath(chip_number=chip_number, waypoints=waypoints, dwell_index=dwell_index)


def _dedup(wps: List[Waypoint]) -> List[Waypoint]:
  """Remove consecutive-identical waypoints (harmless no-op moves)."""
  out: List[Waypoint] = []
  for w in wps:
    if not out or any(abs(a - b) > 1e-6 for a, b in zip(w, out[-1])):
      out.append(w)
  return out


# The Mantis idle / ready XY. A waste-station MoveSequenceItem that specifies only
# Z (no X/Y) returns the arm to idle XY (verified against the vendor prime sequence: this
# is the center via-point that keeps the long waste swing clear of the tubing).
_IDLE_XY: Tuple[float, float] = (15.0, 31.17)
_WASTE_REL = os.path.join("Data", "System", "Sequences", "Common", "MoveToWasteStation.seq.txt")


def load_waste_path(install_root: str) -> List[Waypoint]:
  """Load the move-to-waste-station path (the prime travel route) from the vendor
  ``MoveToWasteStation.seq.txt``. Z-only lines return to idle XY (a safe center
  via-point). Verified byte-exact against the vendor prime sequence."""
  path = os.path.join(install_root, _WASTE_REL)
  x, y, z = _IDLE_XY[0], _IDLE_XY[1], 0.0
  wps: List[Waypoint] = []
  with open(path, encoding="utf-8-sig") as fh:
    for line in fh:
      line = line.strip()
      if not line.startswith("MoveSequenceItem"):
        continue
      pairs = _AXIS.findall(line)
      if not pairs:
        continue
      if not any(a in ("X", "Y", "dX", "dY") for a, _ in pairs):
        x, y = _IDLE_XY  # Z-only -> return to idle XY (center via-point)
      for axis, val in pairs:
        v = float(val)
        if axis == "X":
          x = v
        elif axis == "Y":
          y = v
        elif axis == "Z":
          z = v
        elif axis == "dX":
          x += v
        elif axis == "dY":
          y += v
        elif axis == "dZ":
          z += v
      wps.append((x, y, z))
  return _dedup(wps)
