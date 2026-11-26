"""Biomek files built for tests: a variant-tree writer, and an instrument file (`.bif`) and a
Master Controller instrument record written from a declared configuration, so the readers can be
checked against inputs made in the test. Not a test module itself, and not used by the driver."""

import struct
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from pylabrobot.beckman_coulter.biomek_i7.biomek_files.instrument_file import (
  GEOMETRY,
  LIST_SETTINGS,
  NESTED_SETTINGS,
  OPTIONAL_GEOMETRY,
)
from pylabrobot.beckman_coulter.biomek_i7.biomek_files.variant_tree import (
  DICTIONARY,
  HEADER_SIZE,
  LIST,
  Ref,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import DeviceConfiguration


class Array:
  """An array variant: its vartype and its elements, as `variant_tree` reads them."""

  def __init__(self, vartype: int, values: List[Any], fmt: Optional[str] = None):
    self.vartype, self.values, self.fmt = vartype, values, fmt


class Writer:
  """Writes Python values as a variant stream: dicts as dictionaries, lists as lists, `Ref` as a
  reference, `Array` as an array; None, bools, ints (int32), floats (double) and strings."""

  def __init__(self) -> None:
    self.objects = 0

  @staticmethod
  def string(text: str) -> bytes:
    return struct.pack("<i", len(text)) + text.encode("utf-16-le")

  def variant(self, value: Any) -> bytes:
    if value is None:
      return b"\x01"
    if value is True:
      return b"\x02"
    if value is False:
      return b"\x03"
    if isinstance(value, int):
      return b"\x06" + struct.pack("<i", value)
    if isinstance(value, float):
      return b"\x08" + struct.pack("<d", value)
    if isinstance(value, str):
      return b"\x0b" + self.string(value)
    if isinstance(value, Ref):
      return b"\x0d" + struct.pack("<i", value.index)
    if isinstance(value, Array):
      head = b"\x0f" + struct.pack("<BHIi", value.vartype, 1, len(value.values), 0)
      if value.fmt is not None:
        return head + struct.pack(f"<{len(value.values)}{value.fmt}", *value.values)
      if value.vartype == 0x08:
        return head + b"".join(self.string(v) for v in value.values)
      return head + b"".join(self.variant(v) for v in value.values)
    if isinstance(value, dict):
      head = self.define(DICTIONARY) + b"\x00" + struct.pack("<i", len(value))
      return head + b"".join(self.string(k) + self.variant(v) for k, v in value.items())
    if isinstance(value, list):
      head = self.define(LIST) + struct.pack("<i", len(value))
      return head + b"".join(self.variant(v) for v in value)
    raise TypeError(f"no variant for {value!r}")

  def define(self, class_id: bytes) -> bytes:
    """An object definition's start: its index (the objects so far) and class id."""
    head = b"\x0e" + struct.pack("<i", self.objects) + class_id
    self.objects += 1
    return head


def stream(root: Any) -> bytes:
  """A decompressed variant-tree stream: its size, then `root`."""
  body = Writer().variant(root)
  return struct.pack("<i", len(body)) + body


def container(data: bytes) -> bytes:
  """A variant-tree file's bytes: the header, then the stream compressed."""
  return b"\x00" * HEADER_SIZE + zlib.compress(data)


# -- an instrument file ----------------------------------------------------------------------------

PODS = {"left": "Pod1", "right": "Pod2"}
POD_TYPES = {"multichannel": "MC", "span8": "SPAN8"}

# Runtime state an instrument file carries in a pod entry beside its configuration, which the
# reader leaves out.
STATE = {"XPosition": 12.5, "Tip1": "none", "Z3Position": 4.0, "RT_Speed": 3, "LastTips": "T230"}


def _pod_entry(side: str, pod: Dict[str, Any]) -> Dict[str, Any]:
  settings = dict(pod["settings"])
  entry: Dict[str, Any] = {
    "Name": PODS[side],
    "PodName": pod["component"],
    "PodType": POD_TYPES[pod["module_type"]],
    "LeftPod": side == "left",
    "CmdGen": {
      "Conversion": {
        f"{axis}{part}": c[key]
        for axis, c in pod["conversion"].items()
        for part, key in (("Const", "const"), ("Offset", "offset"))
      }
    },
    "Min": {axis: low for axis, (low, _) in pod["limits"].items()},
    "Max": {axis: high for axis, (_, high) in pod["limits"].items()},
    "ParkLocation": pod["park"],
    "GripperModel": pod["gripper_model"],
    "Obstacles": [
      {
        "Name": o["name"],
        "Buffer": o["buffer"],
        **{k.upper(): o[k] for k in ("x1", "x2", "y1", "y2", "z1", "z2") if k in o},
      }
      for o in pod["obstacles"]
    ],
  }
  for name, key in GEOMETRY.items():
    entry[key] = pod["geometry"][name]
  for name, key in OPTIONAL_GEOMETRY.items():
    if name in pod["geometry"]:
      entry[key] = pod["geometry"][name]
  if "GripperSqueezeForce" in settings:
    entry["Gripper"] = {"SqueezeForce": settings.pop("GripperSqueezeForce")}
  for path in NESTED_SETTINGS:
    value = settings.pop("/".join(path), None)
    if value is not None:
      node = entry
      for key in path[:-1]:
        node = node.setdefault(key, {})
      node[path[-1]] = value
  for key, value in settings.items():
    entry[key] = list(value) if key in LIST_SETTINGS else value
  entry.update(STATE)
  return entry


def instrument_file_tree(configuration: DeviceConfiguration, deck: str = "Deck1") -> Dict[str, Any]:
  """What an instrument file holds for `configuration`, as plain values for `Writer`: the two pod
  entries (with runtime state beside them) and the default deck, with another deck that is not the
  default."""
  data = configuration.to_dict()
  pipettor: Dict[str, Any] = {"SerialNumber": data["instrument_serial_number"]}
  for side, pod in data["pods"].items():
    pipettor[PODS[side]] = _pod_entry(side, pod)
  positions = {
    name: {
      **{f"{a}{n}": p["corners"][n][i] for n in p["corners"] for i, a in enumerate("XYZ")},
      "XSpan": p["x_span"],
      "YSpan": p["y_span"],
      **{f"Framed{n}": how for n, how in p["framed"].items()},
    }
    for name, p in data["deck_positions"].items()
  }
  groups = {
    a["name"]: {
      "Type": a["alp_type"],
      "column": a["column"],
      "row": a["row"],
      "Positions": {name: {} for name in a["positions"]},
      **({"X1": a["x1"]} if a.get("x1") is not None else {}),
    }
    for a in data["deck_layout"]
  }
  return {
    "Devices": {"Pipettor1": pipettor},
    "Decks": {
      "Other": {"IsDefault": False, "Positions": {}, "Groups": {}},
      deck: {"IsDefault": True, "Positions": positions, "Groups": groups},
    },
  }


def write_instrument_file(configuration: DeviceConfiguration, path: Union[str, Path]) -> None:
  Path(path).write_bytes(container(stream(instrument_file_tree(configuration))))


# -- a Master Controller instrument record -------------------------------------------------------

PUMP_CONFIGURE = "<8I2iH"  # the pump's Configure layout, which its record packs


def _motor(axis: Dict[str, Any]) -> str:
  limits = axis["limits"]
  configure = bytes.fromhex(axis["configure"])
  vmin = amin = 0
  if axis["device"] == 0x12:
    (min_start, max_start, vmin, _vmax, min_cutoff, max_cutoff, amin, _amax, _pmin, _pmax,
     backlash) = struct.unpack(PUMP_CONFIGURE, configure)  # fmt: skip
    config = f"#(<pump-config> {min_start} {max_start} {min_cutoff} {max_cutoff} {backlash})"
    jmax = "#f"
  else:
    config = "#vu8(" + " ".join(str(b) for b in configure) + ")"
    jmax = str(limits["max_jerk"])
  fields = [
    "<motor>", axis["name"], axis["device"], axis["index"], 0, limits["min_position"],
    limits["max_position"], axis["home_position"], axis["hysteresis"], vmin,
    limits["max_velocity"], amin, limits["max_acceleration"], 0, jmax, "#f", config,
  ]  # fmt: skip
  return "#(" + " ".join(str(f) for f in fields) + ")"


def record_text(configuration: DeviceConfiguration) -> str:
  """The Master Controller's instrument record for `configuration`, as its database holds it."""
  arms: List[str] = []
  for side, pod in configuration.pods.items():
    kind = {"multichannel": "mc", "span8": "span8"}[pod.module_type]
    motors = " ".join(_motor(a) for a in configuration.modules[pod.module_type])
    arms.append(f'#(<arm> "{pod.module_serial_number}" {side} {kind} #({motors}))')
  return f'#(<instrument> "{configuration.chassis_serial_number}" ' + " ".join(arms) + ")"


def write_record(configuration: DeviceConfiguration, path: Union[str, Path]) -> None:
  Path(path).write_text(record_text(configuration) + "\n", encoding="utf-8")


def write_database(texts: List[Tuple[str, str]], path: Union[str, Path]) -> None:
  """A Master Controller database holding only its `instrument` table: (name, record) rows."""
  import sqlite3

  db = sqlite3.connect(str(path))
  try:
    with db:
      db.execute("CREATE TABLE instrument (name TEXT, definition TEXT)")
      db.executemany("INSERT INTO instrument VALUES (?, ?)", texts)
  finally:
    db.close()
