"""What a declared configuration takes from Biomek's Master Controller: its instrument record.

The Master Controller keeps one record per instrument it drives, in the `instrument` table of its
database (`C:\\ProgramData\\Beckman Coulter\\Biomek Master Controller\\Log.db3`; the same file
holds its logs). The record is the instrument's motor configuration, keyed by serial numbers:

  #(<instrument> "<chassis serial>"
    #(<arm> "<module serial>" left|right mc|span8
      #(#(<motor> ...) ...))
    ...)

Each `<motor>` holds, in order: the axis's name, device byte, index and mask; its minimum and
maximum position; its home position (where Home All leaves it); its hysteresis (what the Master
Controller adds to its targets, measured per instrument: non-zero on the Span-8 Z channels); its
minimum and maximum velocity, acceleration and jerk (`#f` for none); a simulated flag; and its
Configure parameters as sent to its board (`#vu8(...)`), or for a syringe pump a `<pump-config>`
of its start and cutoff velocity limits and backlash, from which the pump's Configure parameters
are packed. Every value checks against the Configure bytes the Master Controller sends at a cold
start (the same limits; the same bytes). The record is rewritten only when the instrument's
motors are changed or measured, and carries no runtime state.

The record is read in one of two ways, to the same result:
- from the database, read-only (`master_controller_db`), choosing the instrument by its chassis
  serial number when the database holds more than one;
- from a text file holding the record (`master_controller_record`), for a computer without the
  database, or a Python without `sqlite3`.
"""

import re
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

MODULE_TYPES = {"mc": "multichannel", "span8": "span8"}
PUMP_DEVICE = 0x12

_TOKEN = re.compile(r'\s*(#vu8\(|#\(|\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+)')


class Symbol(str):
  """A bare word of the record (a name, `left`, `#f`), as opposed to a string."""


def parse_record(text: str) -> List[Any]:
  """Every top-level value of a record's text: vectors and lists as Python lists, byte vectors as
  `bytes`, integers as `int`, `#t` / `#f` as `True` / `False`, strings as `str`, other words as
  `Symbol`.

  Raises:
    ValueError: for text that is not a record.
  """
  tokens = []
  at = 0
  while text[at:].strip():
    m = _TOKEN.match(text, at)
    if m is None:
      raise ValueError(f"the Master Controller record holds text this reader does not know at {at}")
    tokens.append(m.group(1))
    at = m.end()
  pos = 0

  def value() -> Any:
    nonlocal pos
    token = tokens[pos]
    pos += 1
    if token in ("#(", "("):
      items = []
      while tokens[pos] != ")":
        items.append(value())
      pos += 1
      return items
    if token == "#vu8(":
      data = []
      while tokens[pos] != ")":
        data.append(int(tokens[pos]))
        pos += 1
      pos += 1
      return bytes(data)
    if token == ")":
      raise ValueError("unbalanced ')' in the Master Controller record")
    if token.startswith('"'):
      return token[1:-1]
    if token in ("#t", "#f"):
      return token == "#t"
    if re.fullmatch(r"-?\d+", token):
      return int(token)
    return Symbol(token)

  out = []
  try:
    while pos < len(tokens):
      out.append(value())
  except IndexError as e:
    raise ValueError("the Master Controller record ends inside a value") from e
  return out


def _axis(motor: List[Any]) -> Dict[str, Any]:
  """One `<motor>` record as a declared configuration's axis."""
  (_, name, device, index, _mask, pmin, pmax, phome, hysteresis, vmin, vmax, amin, amax, _jmin,
   jmax, _simulated, config) = motor  # fmt: skip
  limits: Dict[str, int] = {"max_velocity": vmax, "max_acceleration": amax}
  if device == PUMP_DEVICE:
    _, min_start, max_start, min_cutoff, max_cutoff, backlash = config
    configure = struct.pack(
      "<8I2iH", min_start, max_start, vmin, vmax, min_cutoff, max_cutoff, amin, amax, pmin, pmax,
      backlash,
    )  # fmt: skip
  else:
    configure = config
    limits["max_jerk"] = jmax if jmax is not False else 0
  limits.update({"min_position": pmin, "max_position": pmax})
  return {
    "name": str(name),
    "device": device,
    "index": index,
    "configure": configure.hex(),
    "limits": limits,
    "home_position": phome,
    "hysteresis": hysteresis,
  }


def _instrument(record: List[Any]) -> Dict[str, Any]:
  """One `<instrument>` record as the part of a declared configuration it gives."""
  if not record or record[0] != "<instrument>":
    raise ValueError("not a Master Controller instrument record")
  modules: Dict[str, List[Dict[str, Any]]] = {}
  module_serial_numbers: Dict[str, str] = {}
  for arm in record[2:]:
    if not arm or arm[0] != "<arm>":
      raise ValueError("an instrument record entry that is not an arm")
    _, serial, side, arm_type, motors = arm
    if arm_type not in MODULE_TYPES:
      raise ValueError(f"a {arm_type} arm, which this driver has no tables for")
    module = MODULE_TYPES[arm_type]
    if module in modules:
      raise ValueError(f"two {module} arms in one instrument record")
    modules[module] = [_axis(m) for m in motors]
    module_serial_numbers[str(side)] = serial.upper()
  return {
    "chassis_serial_number": record[1].upper(),
    "module_serial_numbers": module_serial_numbers,
    "modules": modules,
  }


def _records(
  master_controller_db: Optional[Union[str, Path]],
  master_controller_record: Optional[Union[str, Path]],
) -> List[str]:
  if (master_controller_db is None) == (master_controller_record is None):
    raise ValueError("give one of master_controller_db and master_controller_record")
  if master_controller_record is not None:
    return [Path(master_controller_record).read_text(encoding="utf-8")]
  import contextlib
  import sqlite3  # standard library; only needed for this way of reading the record

  uri = Path(master_controller_db).resolve().as_uri() + "?mode=ro"  # type: ignore[arg-type]
  with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:  # closed, not only committed
    return [row[0] for row in db.execute("SELECT definition FROM instrument ORDER BY name")]


def read_master_controller(
  master_controller_db: Optional[Union[str, Path]] = None,
  master_controller_record: Optional[Union[str, Path]] = None,
  chassis_serial_number: Optional[str] = None,
) -> Dict[str, Any]:
  """The part of a declared configuration the Master Controller's instrument record gives:
  `chassis_serial_number`, `module_serial_numbers` (per side) and `modules` (the motor tables).

  Args:
    master_controller_db: its database (`Log.db3`), opened read-only.
    master_controller_record: a text file holding the record, instead of the database.
    chassis_serial_number: which instrument, when more than one record is found.

  Raises:
    ValueError: for neither or both sources, no record of that instrument, more than one record
      and no chassis serial number to choose by, or a record this reader does not know.
  """
  found = [
    _instrument(record)
    for text in _records(master_controller_db, master_controller_record)
    for record in parse_record(text)
  ]
  if chassis_serial_number is not None:
    found = [r for r in found if r["chassis_serial_number"] == chassis_serial_number.upper()]
  if len(found) != 1:
    serials = [r["chassis_serial_number"] for r in found]
    raise ValueError(
      f"{len(found)} Master Controller instrument records"
      + (f" for chassis {chassis_serial_number}" if chassis_serial_number else "")
      + (f" ({', '.join(serials)}); pass chassis_serial_number" if len(found) > 1 else "")
    )
  return found[0]


def write_record(text: str, path: Union[str, Path]) -> None:
  """Write a record's text to a file `master_controller_record` reads, after checking it parses."""
  for record in parse_record(text):
    _instrument(record)
  Path(path).write_text(text + "\n", encoding="utf-8")


def record_text(
  master_controller_db: Union[str, Path], chassis_serial_number: Optional[str] = None
) -> str:
  """The record's text as the database holds it, for `write_record`: the one instrument, or the one
  with that chassis serial number."""
  texts = _records(master_controller_db, None)
  matches: List[Tuple[str, Dict[str, Any]]] = [
    (t, _instrument(r)) for t in texts for r in parse_record(t)
  ]
  if chassis_serial_number is not None:
    matches = [m for m in matches if m[1]["chassis_serial_number"] == chassis_serial_number.upper()]
  if len(matches) != 1:
    raise ValueError(f"{len(matches)} matching Master Controller instrument records")
  return matches[0][0]
