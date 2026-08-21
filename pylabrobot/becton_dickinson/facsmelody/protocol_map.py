"""Consumer-side loader for a decoded FACSMelody ``ProtocolMap``.

A ``ProtocolMap`` is the deliverable of the reverse-engineering step described in
``docs/facsmelody-re.md``: a JSON file that maps each logical command
(``start_sort``, ``clean``, ...) to the exact bytes that drive it, plus how each
frame is framed and checksummed. That reverse-engineering toolkit is deliberately
kept out of this package; only the read side lives here, so the backend can load a
trusted map and refuse to run against an incomplete one.

Nothing in this module imports a hardware library, so it is always importable.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Dict, List, Optional

from .constants import REQUIRED_COMMANDS, Transport


@dataclass
class Command:
  """A single, replayable command in the Melody protocol.

  ``frame_template`` is the bytes to send as a hex string, optionally containing
  ``{param}`` tokens filled at send time. Everything stays optional until decoding
  fills it in; an undecoded ``Command`` still documents intent, which keeps the
  backend accurate about what it cannot yet do.
  """

  name: str
  transport: Transport = Transport.UNKNOWN
  frame_template: Optional[str] = None  # hex string, may contain {param} tokens
  response_regex: Optional[str] = None
  params: Dict[str, str] = field(default_factory=dict)  # param -> encoder spec
  terminator: Optional[str] = None  # hex of frame terminator, if any
  checksum: Optional[str] = None  # checksum scheme name, if any
  evidence: List[str] = field(default_factory=list)  # capture-frame refs
  decoded: bool = False
  notes: str = ""


@dataclass
class ProtocolMap:
  """Everything needed to drive the Melody headlessly."""

  device: str = "BD FACSMelody"
  transport: Transport = Transport.UNKNOWN
  endpoint: Optional[str] = None
  commands: Dict[str, Command] = field(default_factory=dict)
  created: float = field(default_factory=time.time)
  notes: str = ""

  def to_json(self, path: str) -> None:
    payload = {
      "device": self.device,
      "transport": self.transport.value,
      "endpoint": self.endpoint,
      "created": self.created,
      "notes": self.notes,
      "commands": {
        name: {**asdict(c), "transport": c.transport.value} for name, c in self.commands.items()
      },
    }
    with open(path, "w") as fh:
      json.dump(payload, fh, indent=2)

  @classmethod
  def from_json(cls, path: str) -> "ProtocolMap":
    with open(path) as fh:
      d = json.load(fh)
    pm = cls(
      device=d.get("device", "BD FACSMelody"),
      transport=Transport(d.get("transport", "unknown")),
      endpoint=d.get("endpoint"),
      created=d.get("created", time.time()),
      notes=d.get("notes", ""),
    )
    # Tolerate unknown keys: this is the consumer side of an external toolkit whose
    # map schema may grow. Drop fields this version does not model rather than
    # crashing the whole load on a single extra key.
    known = {f.name for f in fields(Command)}
    for name, c in d.get("commands", {}).items():
      c = {k: v for k, v in c.items() if k in known}
      c["transport"] = Transport(c.get("transport", "unknown"))
      pm.commands[name] = Command(**c)
    return pm

  def coverage(self) -> dict:
    """Report required-command coverage: how many are decoded and which are missing.

    Checked against ``REQUIRED_COMMANDS``, not merely the commands present in the
    map, so a map that *omits* a required command entirely is still reported as
    missing it. This is the gate a live run must clear (see ``FACSMelodyDriver.setup``),
    so it fails closed: a command that is absent, or present but undecoded, counts as
    missing.
    """
    required = [name for name, _ in REQUIRED_COMMANDS]
    missing = [
      name for name in required if name not in self.commands or not self.commands[name].decoded
    ]
    return {
      "decoded": len(required) - len(missing),
      "total": len(required),
      "missing": missing,
    }


def seed_required(device: str = "BD FACSMelody") -> ProtocolMap:
  """Build a ProtocolMap seeded with the required command set, all undecoded.

  Used as the map for a dry-run instrument and as the explicit target list a real
  reverse-engineering pass fills in.
  """
  pm = ProtocolMap(device=device)
  for name, note in REQUIRED_COMMANDS:
    pm.commands[name] = Command(name=name, notes=note, decoded=False)
  return pm
