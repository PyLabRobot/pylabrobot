"""Read Biomek's variant-tree files: the instrument file (`.bif`), and labware / tip class data.

Container: 5 header bytes, then zlib. Inside is one int32 size and one variant. A variant starts
with one code byte; its low nibble is the type and bit 0x10 marks an interface reference:

  0x00 empty   0x01 null    0x02 true    0x03 false   0x04 byte     0x05 int16   0x06 int32
  0x07 single  0x08 double  0x09 currency (int64 / 10000)           0x0A date (double, days)
  0x0B string (int32 length, UTF-16LE)  0x0C error (int32)
  0x0D reference to an object defined earlier (int32 index)
  0x0E object definition: int32 index (= objects so far), 16-byte class id, then the object's body
  0x0F array: vartype byte, uint16 dims, dims x (uint32 count, int32 lbound), then the elements

Two classes carry most of a file:

- a dictionary: a flag byte; if non-zero a class id follows, and with flag 1 an extension body of
  that class comes first; then an int32 count of (string key, variant value) entries;
- a list: an int32 count of variants.

The other classes' bodies (`EXTENSIONS`, `OBJECTS`) were measured on the files in hand. `parse`
refuses to finish with any byte unaccounted for, so a file in a format not seen here is refused,
not misread.
"""

import struct
import uuid
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

HEADER_SIZE = 5


def _class_id(text: str) -> bytes:
  return uuid.UUID(text).bytes_le


DICTIONARY = _class_id("E4C29FFF-BCE3-44DB-BEB6-5B5B6EBC0DF1")
LIST = _class_id("62DBE1C1-B344-4AB4-804F-73722341CBE5")
DATA_SET_MANAGER = _class_id("B6D3BEC0-0085-4F00-A09A-C310B1DAFB72")
DATA_SET = _class_id("C025C3B2-323C-4AC8-B61D-1F13EFFFE37F")
LABWARE = _class_id("739C5987-FD91-4D70-82CB-2A5B768E3CCB")
WORKSPACE = _class_id("23008335-C70E-46F0-87A8-4948F25298E8")
DECK_POSITION = _class_id("0E7CDB5A-E774-4F1A-A72E-A013F06ED62A")
PROMISE = _class_id("FD7B3B46-B8DA-4E47-B7FF-51B975DE468D")
WELL_VALUES = _class_id("51297284-DD58-4725-A9B9-429BD5610D69")

CLASS_NAMES = {
  DICTIONARY: "dictionary",
  LIST: "list",
  DATA_SET_MANAGER: "data set manager",
  DATA_SET: "data set",
  LABWARE: "labware",
  WORKSPACE: "workspace",
  DECK_POSITION: "deck position",
  PROMISE: "promise",
  WELL_VALUES: "well values",
}

# Array vartypes whose elements are fixed-size values, read as raw bytes.
_ARRAY_RAW = {
  0x02: "h",
  0x03: "i",
  0x04: "f",
  0x05: "d",
  0x06: "q",
  0x07: "d",
  0x0A: "i",
  0x0B: "h",
  0x11: "B",
}


class ParseError(Exception):
  """The file is not a variant tree as this reader knows it."""


@dataclass
class Obj:
  """An object definition: its class, what its extension or own body read, and its entries."""

  index: int
  class_id: bytes
  flag: Optional[int] = None
  extension_class_id: Optional[bytes] = None
  extension: Any = None
  entries: Optional[Dict[str, Any]] = None
  items: Optional[List[Any]] = None

  @property
  def class_name(self) -> str:
    return CLASS_NAMES.get(self.class_id, str(uuid.UUID(bytes_le=self.class_id)))

  @property
  def extension_name(self) -> Optional[str]:
    if self.extension_class_id is None:
      return None
    return CLASS_NAMES.get(
      self.extension_class_id, str(uuid.UUID(bytes_le=self.extension_class_id))
    )


@dataclass
class Ref:
  """A reference to an object defined earlier in the stream."""

  index: int


@dataclass
class Currency:
  value: int  # x 10000


@dataclass
class Date:
  days: float  # since 1899-12-30


@dataclass
class Tree:
  root: Any
  objects: List[Obj] = field(default_factory=list)

  def deref(self, value: Any) -> Any:
    """`value`, or the object it refers to."""
    return self.objects[value.index] if isinstance(value, Ref) else value


class Reader:
  def __init__(self, data: bytes):
    self.data = data
    self.pos = 0
    self.objects: List[Obj] = []

  # -- primitives --------------------------------------------------------------------------------

  def take(self, n: int) -> bytes:
    if n < 0 or self.pos + n > len(self.data):
      raise ParseError(f"read of {n} bytes past the end at {self.pos}")
    chunk = self.data[self.pos : self.pos + n]
    self.pos += n
    return chunk

  def unpack(self, fmt: str) -> Any:
    return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))[0]

  def string(self) -> str:
    n = self.unpack("i")
    if n < 0:
      raise ParseError(f"negative string length at {self.pos - 4}")
    return self.take(2 * n).decode("utf-16-le")

  # -- variants ----------------------------------------------------------------------------------

  def variant(self) -> Any:
    at = self.pos
    code = self.unpack("B")
    kind = code & 0x0F
    if kind == 0x00:
      if code not in (0x00, 0x10, 0x20):
        raise ParseError(f"unknown stream code {code:#x} at {at}")
      return None
    if kind == 0x01:
      return None
    if kind == 0x02:
      return True
    if kind == 0x03:
      return False
    if kind == 0x04:
      return self.unpack("B")
    if kind == 0x05:
      return self.unpack("h")
    if kind == 0x06:
      return self.unpack("i")
    if kind == 0x07:
      return self.unpack("f")
    if kind == 0x08:
      return self.unpack("d")
    if kind == 0x09:
      return Currency(self.unpack("q"))
    if kind == 0x0A:
      return Date(self.unpack("d"))
    if kind == 0x0B:
      return self.string()
    if kind == 0x0C:
      return ("error", self.unpack("i"))
    if kind == 0x0D:
      index = self.unpack("i")
      if not 0 <= index < len(self.objects):
        raise ParseError(f"undefined object reference {index} at {at}")
      return Ref(index)
    if kind == 0x0E:
      return self.object(at)
    if kind == 0x0F:
      return self.array()
    raise ParseError(f"unknown stream code {code:#x} at {at}")  # pragma: no cover

  def array(self) -> List[Any]:
    vartype = self.unpack("B")
    dims = self.unpack("H")
    counts = []
    for _ in range(dims):
      counts.append(self.unpack("I"))
      self.unpack("i")  # lower bound
    total = 1
    for c in counts:
      total *= c
    if vartype in _ARRAY_RAW:
      fmt = _ARRAY_RAW[vartype]
      return list(struct.unpack(f"<{total}{fmt}", self.take(total * struct.calcsize(fmt))))
    if vartype == 0x08:
      return [self.string() for _ in range(total)]
    if vartype in (0x09, 0x0C, 0x0D):
      return [self.variant() for _ in range(total)]
    raise ParseError(f"invalid array vartype {vartype:#x} at {self.pos}")

  def object(self, at: int) -> Obj:
    index = self.unpack("i")
    if index != len(self.objects):
      raise ParseError(f"invalid object definition {index} (expected {len(self.objects)}) at {at}")
    obj = Obj(index=index, class_id=self.take(16))
    self.objects.append(obj)
    if obj.class_id == DICTIONARY:
      self.dictionary(obj)
    elif obj.class_id == LIST:
      obj.items = [self.variant() for _ in range(self.unpack("i"))]
    elif obj.class_id in OBJECTS:
      obj.extension = OBJECTS[obj.class_id](self)
    else:
      raise ParseError(f"no reader for class {obj.class_name} at {at}")
    return obj

  def dictionary(self, obj: Obj) -> None:
    obj.flag = self.unpack("B")
    if obj.flag:
      obj.extension_class_id = self.take(16)
      if obj.flag == 1:
        loader = EXTENSIONS.get(obj.extension_class_id)
        if loader is None:
          raise ParseError(f"no reader for extension {obj.extension_name} at {self.pos}")
        obj.extension = loader(self)
      elif obj.flag != 4:
        raise ParseError(f"unknown dictionary flag {obj.flag} at {self.pos}")
    count = self.unpack("i")
    if count < 0:
      raise ParseError(f"negative dictionary count at {self.pos - 4}")
    obj.entries = {}
    for _ in range(count):
      key = self.string()
      obj.entries[key] = self.variant()


# -- class-specific bodies ---------------------------------------------------------------------


def _string_list(r: Reader) -> List[str]:
  """Data set manager: the data set names in order, as a counted list of strings."""
  return [r.string() for _ in range(r.unpack("i"))]


def _nothing(r: Reader) -> None:
  """Data set: nothing of its own before the dictionary's entries."""
  return None


def _labware(r: Reader) -> Dict[str, Any]:
  """Labware: per-well state, as (array variant, int32 well count) pairs.

  Measured on 8-, 96- and 384-well labware: four pairs, a flag byte, six more pairs when it is
  set, and a closing flag byte that has only been seen clear.
  """

  def pairs(n: int) -> List[Any]:
    out = []
    for _ in range(n):
      values = r.variant()
      wells = r.unpack("i")
      if not isinstance(values, list) or len(values) != wells:
        raise ParseError(f"labware array of {wells} wells does not hold {wells} at {r.pos}")
      out.append(values)
    return out

  arrays = pairs(4)
  more = r.unpack("B")
  if more:
    arrays += pairs(6)
  last = r.unpack("B")
  if last:
    raise ParseError(f"labware closing flag {last} not seen before, at {r.pos - 1}")
  return {"arrays": arrays}


def _well_values(r: Reader) -> Dict[str, Any]:
  """Well values: one value per well of a labware, named (a string), as int32 columns and int32
  rows, then columns x rows variants. Measured on the per-well liquid names (`DataSets` /
  `RuntimeDataSets` "Liquid") Biomek writes into the instrument file with the deck's labware after
  a run: 24-, 96- and 8-well labware."""
  name = r.string()
  columns, rows = r.unpack("i"), r.unpack("i")
  if columns < 0 or rows < 0:
    raise ParseError(f"well values of {columns} x {rows} wells at {r.pos - 8}")
  return {
    "name": name,
    "columns": columns,
    "rows": rows,
    "values": [r.variant() for _ in range(columns * rows)],
  }


def _workspace(r: Reader) -> Dict[str, Any]:
  """Workspace root: an int32 version, then two (int32, int32, int32, int32) records. Measured."""
  version = r.unpack("i")
  records = [tuple(r.unpack("i") for _ in range(4)) for _ in range(2)]
  return {"version": version, "records": records}


def _promise(r: Reader) -> Dict[str, Any]:
  """Promise: three doubles, then a variant, then int32 and a byte. Measured."""
  values = [r.unpack("d") for _ in range(3)]
  status = r.variant()
  tail = (r.unpack("i"), r.unpack("B"))
  return {"values": values, "status": status, "tail": tail}


EXTENSIONS: Dict[bytes, Callable[[Reader], Any]] = {
  DATA_SET_MANAGER: _string_list,
  DATA_SET: _nothing,
  LABWARE: _labware,
  WORKSPACE: _workspace,
  WELL_VALUES: _well_values,
}

OBJECTS: Dict[bytes, Callable[[Reader], Any]] = {
  PROMISE: _promise,
}


# -- entry points ------------------------------------------------------------------------------


def decompress(blob: bytes) -> bytes:
  return zlib.decompress(blob[HEADER_SIZE:])


def parse(data: bytes) -> Tree:
  """Parse a decompressed stream completely: one int32 size, then one variant, nothing after.

  Raises:
    ParseError: for anything else.
  """
  r = Reader(data)
  size = r.unpack("i")
  if size != len(data) - 4:
    raise ParseError(f"stream says {size} bytes, holds {len(data) - 4}")
  root = r.variant()
  if r.pos != len(data):
    raise ParseError(f"{len(data) - r.pos} bytes left unparsed at {r.pos}")
  return Tree(root=root, objects=r.objects)


def parse_file(path: str) -> Tree:
  with open(path, "rb") as f:
    return parse(decompress(f.read()))


def plain(value: Any, objects: List[Obj], inline: bool = False, _open: Optional[set] = None) -> Any:
  """The tree as JSON-ready values: dictionaries as dicts, lists as lists, refs as {"$ref": n}.

  With `inline`, a reference is replaced by the object it names, except inside that same object
  (a cycle), where it stays {"$ref": n}.
  """
  _open = set() if _open is None else _open
  if isinstance(value, Ref) and inline and value.index not in _open:
    return plain(objects[value.index], objects, inline, _open)
  if isinstance(value, Obj):
    _open = _open | {value.index}

    def sub(v: Any) -> Any:
      return plain(v, objects, inline, _open)

    if value.entries is not None:
      out: Dict[str, Any] = {}
      if value.extension_class_id is not None:
        out["$class"] = value.extension_name
      if value.extension is not None:
        out["$extension"] = sub(value.extension)
      out["$id"] = value.index
      out.update({k: sub(v) for k, v in value.entries.items()})
      return out
    if value.items is not None:
      return [sub(v) for v in value.items]
    return {"$class": value.class_name, "$id": value.index, "$body": sub(value.extension)}
  if isinstance(value, Ref):
    return {"$ref": value.index}
  if isinstance(value, Currency):
    return value.value / 10000
  if isinstance(value, Date):
    return {"$date": value.days}
  if isinstance(value, dict):
    return {k: plain(v, objects, inline, _open) for k, v in value.items()}
  if isinstance(value, (list, tuple)):
    return [plain(v, objects, inline, _open) for v in value]
  return value


def walk(value: Any, path: str = "") -> Iterator[Tuple[str, Any]]:
  """Every leaf value with its path (`/Decks/Standard/Groups/...`), over a `plain` tree."""
  if isinstance(value, dict):
    for k, v in value.items():
      yield from walk(v, f"{path}/{k}")
  elif isinstance(value, list):
    for i, v in enumerate(value):
      yield from walk(v, f"{path}[{i}]")
  else:
    yield path, value
