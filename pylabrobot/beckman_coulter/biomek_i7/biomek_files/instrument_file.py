"""What a declared configuration takes from Biomek's instrument file (`.bif`).

From the live pod entries `Devices/Pipettor1/Pod1` and `Pod2`, per pod:
- the counts conversions, travel limits and park location;
- the geometry (the collision model's dimensions);
- every other scalar setting, and the few kept as a dictionary (`NESTED_SETTINGS`);
- the gripper model;
- the deck obstacles Biomek keeps per pod (boxes, an absent bound open);

and from the default deck, its positions with a corner per pod, and its ALPs.

The per-device entries are used, not the pod-type defaults under `HardwareSetupInfo/PodTypes`:
they are what reproduces Biomek's logged travel (see `PodGeometry`). Runtime state the file also
carries (positions, tips, gripped labware, `RT_*`) is left out: Biomek rewrites it during ordinary
runs, and it is not configuration.
"""

from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

from pylabrobot.beckman_coulter.biomek_i7.biomek_files.variant_tree import Tree, parse_file, plain

MODULE_TYPES = {"MC": "multichannel", "SPAN8": "span8"}

# Geometry fields (driver `PodGeometry`) and the pod entry's keys they come from.
GEOMETRY = {
  "bridge_x_span": "BridgeXSpan",
  "bridge_to_head_x_offset": "BridgeToHeadXOffset",
  "mandrel1_x_offset": "Mandrel1XOffset",
  "mandrel1_y_offset": "Mandrel1YOffset",
  "mandrel_x_spacing": "MandrelXSpacing",
  "mandrel_y_spacing": "MandrelYSpacing",
  "avoidance_margin": "AvoidanceMargin",
  "pod_x_span": "PodXSpan",
  "pod_y_span": "PodYSpan",
  "gripper_x_offset": "GripperXOffset",
  "gripper_y_offset": "GripperYOffset",
  "gripper_z_offset": "GripperZOffset",
  "frame_safety_left": "Frame_Safety_L",
  "frame_safety_right": "Frame_Safety_R",
  "additional_safe_height": "AdditionalSafeHeight",
  "additional_safe_height_gripper": "AdditionalSafeHeightGripper",
}
OPTIONAL_GEOMETRY = {
  "mandrels_x": "MandrelsX",
  "mandrels_y": "MandrelsY",
  "sub_mandrel_safe_height": "SubMandrelSafeHeight",
}

# Runtime state Biomek writes back into the file: where things are and what is held.
STATE_KEYS = {
  "CurrentSpeed", "DPosition", "GGPosition", "GRPosition", "GYPosition", "GZPosition",
  "GripSide", "GrippedLabware", "GrippedLabwareOffsets", "HasGripperProbes", "LastOperation",
  "LastTips", "LastValidation", "PositionSaved", "SPosition", "Squeeze", "SqueezeForce", "Tips",
  "TipsFrom", "TipsFromLW", "TipsSelected", "XPosition", "YPosition", "ZPosition",
}  # fmt: skip

# Per-probe lists of numbers, kept as settings like the scalars (the seat per probe).
LIST_SETTINGS = {"LoadingOffsets", "BrokenTips"}

# Settings Biomek keeps as a dictionary in the pod entry, taken as that dictionary under the key's
# path: a Multichannel's mandrel A1 from the pod (`Head/PodToMandrel1`: X, Y, Z in cm, Z the bare
# mandrels' end), a Span-8 probe's tip hub (`DefaultTipHubInfo`: Length, MinRadius, MaxRadius), a
# Span-8's tubing bundle (`LLSWireModel`: where it leaves the probes, its anchor, its lowest point).
NESTED_SETTINGS = (("Head", "PodToMandrel1"), ("DefaultTipHubInfo",), ("LLSWireModel",))


def is_state(key: str) -> bool:
  """Whether a pod entry's key is runtime state rather than configuration."""
  if key.startswith("RT_") or key in STATE_KEYS:
    return True
  # Per channel or pump: Tip1..Tip8, Z1Position.., D1Position..
  return (key.startswith("Tip") and key[3:].isdigit()) or (
    key.endswith("Position") and key[0] in "ZD" and key[1:-8].isdigit()
  )


def _plain_dict(tree: Tree, obj: Any) -> Dict[str, Any]:
  """A dictionary as plain JSON values, without the reader's bookkeeping keys."""

  def clean(v: Any) -> Any:
    if isinstance(v, dict):
      return {k: clean(x) for k, x in v.items() if not k.startswith("$")}
    if isinstance(v, list):
      return [clean(x) for x in v]
    return v

  return clean(plain(obj, tree.objects, inline=False))  # type: ignore[no-any-return]


def _unit_of(axis: str) -> str:
  if axis == "GR":
    return "deg"
  if axis[0] == "D":
    return "uL"
  return "cm"


def _pod(tree: Tree, pod: Any) -> Tuple[str, Dict[str, Any]]:
  get = tree.deref
  e = {k: get(v) for k, v in pod.entries.items()}
  conversion_entries = get(get(e["CmdGen"]).entries["Conversion"]).entries
  conversion = {}
  for key, value in conversion_entries.items():
    if key.endswith("Const"):
      axis = key[: -len("Const")]
      conversion[axis] = {
        "const": value,
        "offset": conversion_entries[f"{axis}Offset"],
        "unit": _unit_of(axis),
      }
  low, high = get(e["Min"]).entries, get(e["Max"]).entries
  limits = {k: [low[k], high[k]] for k in high if k in low}
  geometry: Dict[str, Any] = {name: e[key] for name, key in GEOMETRY.items()}
  geometry.update({name: e[key] for name, key in OPTIONAL_GEOMETRY.items() if key in e})
  used = set(GEOMETRY.values()) | set(OPTIONAL_GEOMETRY.values())
  settings: Dict[str, Any] = {
    k: v
    for k, v in e.items()
    if k not in used and not is_state(k) and isinstance(v, (bool, int, float, str))
  }
  for k, v in e.items():
    items = getattr(v, "items", None)
    if k not in used and not is_state(k) and k in LIST_SETTINGS and isinstance(items, list):
      settings[k] = [get(x) for x in items]
  for path in NESTED_SETTINGS:
    node: Any = pod
    for key in path:
      entries = getattr(get(node), "entries", None)
      node = entries.get(key) if entries is not None else None
      if node is None:
        break
    if node is not None:
      settings["/".join(path)] = _plain_dict(tree, get(node))
  # The force a grip closes with (`SF` on GG; Biomek sends it as is). The pod's own
  # `SqueezeForce` is runtime state; the gripper's is the setting.
  gripper = get(e["Gripper"]).entries if "Gripper" in e else {}
  if "SqueezeForce" in gripper:
    settings["GripperSqueezeForce"] = gripper["SqueezeForce"]
  side = "left" if e["LeftPod"] else "right"
  return side, {
    "component": e["PodName"],
    "module_type": MODULE_TYPES[e["PodType"]],
    "source": f"bif:Devices/Pipettor1/{e['Name']}",
    "conversion": conversion,
    "limits": limits,
    "geometry": geometry,
    "park": _plain_dict(tree, e["ParkLocation"]),
    "settings": settings,
    "gripper_model": _plain_dict(tree, e["GripperModel"]),
    "obstacles": [
      {
        "name": o["Name"],
        "buffer": o.get("Buffer", 0.0),
        **{k.lower(): o[k] for k in ("X1", "X2", "Y1", "Y2", "Z1", "Z2") if k in o},
      }
      for o in (_plain_dict(tree, get(item)) for item in get(e["Obstacles"]).items)
    ],
  }


def _default_deck(tree: Tree) -> Tuple[str, Any]:
  get = tree.deref
  decks = get(tree.root.entries["Decks"])
  name = next(k for k in decks.entries if get(decks.entries[k]).entries.get("IsDefault"))
  return name, get(decks.entries[name])


def _positions(tree: Tree) -> Dict[str, Dict[str, Any]]:
  """The default deck's positions: each one's corner per pod (`X<n>`/`Y<n>`/`Z<n>`), its spans and
  how each corner was framed."""
  get = tree.deref
  positions = {}
  for name, ref in get(_default_deck(tree)[1].entries["Positions"]).entries.items():
    e = get(ref).entries
    if not all(f"{a}{n}" in e for a in "XYZ" for n in (1, 2)):
      continue
    positions[name] = {
      "corners": {str(n): [e[f"X{n}"], e[f"Y{n}"], e[f"Z{n}"]] for n in (1, 2)},
      "x_span": e["XSpan"],
      "y_span": e["YSpan"],
      "framed": {str(n): e[f"Framed{n}"] for n in (1, 2) if f"Framed{n}" in e},
    }
  return dict(sorted(positions.items()))


def _layout(tree: Tree) -> List[Dict[str, Any]]:
  """The default deck's ALPs (`Decks/<deck>/Groups`): each one's name, `Type`, pin `column` and
  `row`, its positions' names and its left edge `X1` (cm), left to right."""
  get = tree.deref
  layout = []
  for name, ref in get(_default_deck(tree)[1].entries["Groups"]).entries.items():
    e = get(ref).entries
    positions = get(e.get("Positions"))
    layout.append({
      "name": name,
      "alp_type": e["Type"],
      "column": e["column"],
      "row": e["row"],
      "positions": list(positions.entries) if positions is not None and positions.entries else [],
      "x1": e.get("X1"),
    })  # fmt: skip
  return sorted(layout, key=lambda a: (a["column"], a["row"]))


def read_instrument_file(path: Union[str, Path]) -> Dict[str, Any]:
  """The part of a declared configuration an instrument file gives, as `DeviceConfiguration`
  serializes it: `instrument_serial_number`, `pods` (without their module serial numbers, which
  the Master Controller's record gives), `deck_positions` and `deck_layout`.

  Raises:
    ParseError: for a file this reader cannot account for completely.
  """
  tree = parse_file(str(path))
  pipettor = tree.deref(tree.deref(tree.root.entries["Devices"]).entries["Pipettor1"])
  pods = dict(_pod(tree, tree.deref(pipettor.entries[name])) for name in ("Pod1", "Pod2"))
  return {
    "instrument_serial_number": pipettor.entries.get("SerialNumber"),
    "pods": pods,
    "deck_positions": _positions(tree),
    "deck_layout": _layout(tree),
  }
