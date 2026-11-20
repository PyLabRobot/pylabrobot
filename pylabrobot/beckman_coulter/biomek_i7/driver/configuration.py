"""What the driver knows about an instrument: facts only (STAR design.md P3).

Two kinds:

- **What the boards answer** about themselves at setup (`ModuleConfiguration`, `BoardInfo`).
- **What they cannot answer**, declared per instrument (`DeviceConfiguration`):
  - the motor tables loaded into the boards after every power-up;
  - per pod, the counts conversions, travel limits, geometry and settings that Biomek keeps on
    the host side.

  One JSON file per instrument, made only by `DeviceConfiguration.from_biomek` from the
  instrument's two Biomek sources (`biomek_files/`), and given to the driver by path; no
  instrument's own ships with the driver (the generic one in `defaults/`, anonymized, is for
  simulation):
  - Biomek's instrument file (`.bif`): the pods, the deck's calibrated positions and its layout;
  - the Master Controller's instrument record: the motor tables (with each instrument's measured
    hysteresis) and the modules' serial numbers.

  `sources` records, per source, where it was read, when, and a fingerprint of exactly the values
  taken from it. Loading refuses a file whose values no longer match its fingerprints (changed
  after it was imported), and a live session refuses one whose sources have changed since
  (`check_sources`).
"""

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pylabrobot.beckman_coulter.biomek_i7.driver.motors import MotorTables
from pylabrobot.beckman_coulter.biomek_i7.driver.protocol import HardwareVersion, ModuleId

# The firmware this driver has been run against (one instrument, 2026-10-24 through 2026-11-10):
# every motion board and module the same image, and the other boards' application versions
# (chassis 0.2.4, SAGIAN Motion 0.3.14, LLS 0.7.8, syringe pump 5.0.9). The Master Controller
# flashes the boards when it brings other firmware, which would change what the boards answer.
TESTED_FIRMWARE_IMAGES = {(0x6D2DDB78, 266436)}
TESTED_APPLICATION_VERSIONS = {"0.2.4", "0.3.14", "0.7.8", "5.0.9"}


@dataclass
class BoardInfo:
  """One board inside a module, as the module reports it."""

  index: int
  hardware: HardwareVersion
  serial_number: Optional[str] = None
  application_version: Optional[str] = None
  application_crc: Optional[int] = None
  application_length: Optional[int] = None


@dataclass
class ModuleConfiguration:
  """One ACE module (the chassis, or an arm): its identity, its boards, and its inputs."""

  module_id: ModuleId
  usb_serial_number: Optional[str]
  serial_number: str
  hardware: HardwareVersion
  application_crc: int
  application_length: int
  device_counts: Dict[int, int]
  boards: List[BoardInfo] = field(default_factory=list)
  analog_inputs: Dict[int, str] = field(default_factory=dict)
  analog_units: Dict[int, str] = field(default_factory=dict)
  digital_inputs: Dict[int, str] = field(default_factory=dict)
  digital_outputs: Dict[int, str] = field(default_factory=dict)

  def untested_firmware(self) -> List[str]:
    """What this module runs that the driver has not been run against: one line per image or
    version, empty when everything is tested."""
    found = []
    if (self.application_crc, self.application_length) not in TESTED_FIRMWARE_IMAGES:
      found.append(
        f"module image CRC {self.application_crc:#010x}, {self.application_length} bytes"
      )
    for b in self.boards:
      image = (b.application_crc, b.application_length)
      if b.application_crc is not None and image not in TESTED_FIRMWARE_IMAGES:
        found.append(
          f"board {b.index} image CRC {b.application_crc:#010x}, {b.application_length} bytes"
        )
      if (
        b.application_version is not None
        and b.application_version not in TESTED_APPLICATION_VERSIONS
      ):
        found.append(f"board {b.index} application {b.application_version}")
    return found


# -- declared per instrument -------------------------------------------------------------------------

Side = Literal["left", "right"]


@dataclass(frozen=True)
class AxisConversion:
  """How Biomek turns a value into counts for one axis or quantity: `counts = (value + offset) x
  const`, rounded. `unit` is what `value` is in: Biomek's own units, `cm`, `deg` or `uL`.

  Read from the instrument file's `CmdGen/Conversion` (`<axis>Const`, `<axis>Offset`). Checked on
  this instrument against positions it logged:
  - the Multichannel's park GR, 90 deg, is -175,443 counts;
  - its rest Z, 39.763 cm, is -256 counts;
  - the Span-8 Z park, 39.1079 cm on Z1, is 9,029 counts.
  """

  const: float
  offset: float
  unit: str

  def to_counts(self, value: float) -> int:
    return round((value + self.offset) * self.const)

  def to_value(self, counts: int) -> float:
    return counts / self.const - self.offset


@dataclass(frozen=True)
class PodGeometry:
  """A pod's dimensions as Biomek's collision model uses them, in cm.

  From the instrument file's per-device pod entry. That entry, not the pod type's defaults under
  `HardwareSetupInfo/PodTypes`, is what reproduces the travel Biomek logged:
  - BridgeXSpan is 39.0154 here, 12.7 in the type defaults;
  - the bridges touch at left X + |right X| = 250,807 counts, and the most Biomek ever commanded
    was 250,678.

  The optional fields are absent on a pod type that has no such part.
  """

  bridge_x_span: float
  bridge_to_head_x_offset: float
  mandrel1_x_offset: float
  mandrel1_y_offset: float
  mandrel_x_spacing: float
  mandrel_y_spacing: float
  avoidance_margin: float
  pod_x_span: float
  pod_y_span: float
  gripper_x_offset: float
  gripper_y_offset: float
  gripper_z_offset: float
  frame_safety_left: float
  frame_safety_right: float
  additional_safe_height: float
  additional_safe_height_gripper: float
  mandrels_x: Optional[int] = None
  mandrels_y: Optional[int] = None
  sub_mandrel_safe_height: Optional[float] = None


@dataclass(frozen=True)
class Obstacle:
  """A fixed obstacle on the deck, as Biomek keeps it per pod (`Obstacles` in the instrument
  file): a box in Biomek's frame (cm; Y towards the front, Z up from the deck surface),
  a bound that is None open on that side, widened by `buffer`. Walls (the shields, the towers, the
  light curtain) have no Z bounds; the pump bank is a floor up to `z2`; the I-beam a ceiling from
  `z1`."""

  name: str
  buffer: float = 0.0
  x1: Optional[float] = None
  x2: Optional[float] = None
  y1: Optional[float] = None
  y2: Optional[float] = None
  z1: Optional[float] = None
  z2: Optional[float] = None


# The settings the driver takes from a pod's entry in the instrument file, never assumed: a pod
# whose entry lacks one is refused (`PodConfiguration`). Every pod's; a gripper's; each pod type's.
POD_SETTINGS: Tuple[str, ...] = ()
GRIPPER_SETTINGS: Tuple[str, ...] = ("GripperSqueezeForce",)
TYPE_SETTINGS: Dict[str, Tuple[str, ...]] = {
  "span8": (
    "ProbeSize",
    "DefaultTipHubInfo",
  ),
  "multichannel": (
    "Head/PodToMandrel1",
    "HeadBodyXSpan",
    "HeadBodyYSpan",
  ),
}


def required_settings(module_type: str, gripper: bool) -> Tuple[str, ...]:
  """The settings a pod of `module_type` (with a gripper or not) must have in its entry."""
  return (
    *POD_SETTINGS,
    *(GRIPPER_SETTINGS if gripper else ()),
    *TYPE_SETTINGS.get(module_type, ()),
  )


@dataclass
class PodConfiguration:
  """One pod's facts the boards do not hold.

  - `conversion`: per axis or quantity, keyed by Biomek's own names (`X`, `Y`, `Z`, `D`, `GY`,
    `GG`, `GZ`, `GR`; on a Span-8 also `S` for the span, `Z1`..`Z8`, `D1`..`D8`).
  - `limits`: each axis's travel in those units, `(lowest, highest)`.
  - `park`: Biomek's park location.
  - `settings`: every other scalar Biomek keeps for the pod, and the Span-8's per-probe lists
    (`LoadingOffsets`, `BrokenTips`) (tip loading, safe heights, speeds,
    gripper squeeze) under its own name, for the features that will use them.
  - `gripper_model`: the gripper's dimensions, as Biomek nests them; empty for a pod with no
    gripper.
  - `module_serial_number`: the serial number the pod's module reports, which ties these facts to
    one module (`BiomekI7Driver.discover` checks it), from the Master Controller's record; None in
    the generic configuration.
  """

  component: str
  module_type: str
  side: Side
  conversion: Dict[str, AxisConversion]
  limits: Dict[str, Tuple[float, float]]
  geometry: PodGeometry
  park: Dict[str, float] = field(default_factory=dict)
  settings: Dict[str, Any] = field(default_factory=dict)
  gripper_model: Dict[str, Any] = field(default_factory=dict)
  obstacles: List[Obstacle] = field(default_factory=list)
  source: str = ""
  module_serial_number: Optional[str] = None

  def __post_init__(self) -> None:
    missing = [
      k
      for k in required_settings(self.module_type, bool(self.gripper_model))
      if k not in self.settings
    ]
    if missing:
      raise ValueError(
        f"{self.component}: the instrument file's entry for this pod has no {', '.join(missing)}; "
        "the driver takes these from it and never assumes them"
      )

  def to_counts(self, axis: str, value: float) -> int:
    """A value in Biomek's units for `axis` (see `conversion`) as counts.

    Raises:
      ValueError: for an axis with no conversion.
    """
    return self._conversion(axis).to_counts(value)

  def to_value(self, axis: str, counts: int) -> float:
    """Counts on `axis` as a value in Biomek's units for it."""
    return self._conversion(axis).to_value(counts)

  def probe_spacing(self) -> float:
    """A Span-8's probes' spacing at their narrowest (cm): the span axis's zero shared out between
    the eight (`S` counts from that span, and each probe's Y counts from its narrowest place), or
    `ProbeSize` without a span axis."""
    if "S" in self.conversion:
      return -self.conversion["S"].offset / 7
    return float(self.settings["ProbeSize"])

  def probe_y(self, probe: int, counts: int) -> float:
    """Span-8 probe `probe`'s (1-8) Y in cm from its `Y<probe>` counts: the `Y` conversion is
    probe 1's, and each next probe's counts start one narrowest spacing further on."""
    return self.to_value("Y", counts) + (probe - 1) * self.probe_spacing()

  def _conversion(self, axis: str) -> AxisConversion:
    if axis not in self.conversion:
      raise ValueError(
        f"{self.component} has no conversion for {axis!r}; has {sorted(self.conversion)}"
      )
    return self.conversion[axis]


@dataclass(frozen=True)
class DeckPosition:
  """A deck position as Biomek calibrated it, in Biomek's frame (cm; Y towards the front, Z up
  from the deck surface): a corner per pod, by Biomek's pod number, and the position's spans.

  Each pod moves to a position relative to its own corner: corner n is the one that shifts when
  pod n's deck framing changes (`ShiftDeck<n>` applied to `X<n>`/`Y<n>`/`Z<n>`). `framed[n]` says
  how corner n was obtained: `Position` (framed at this position) or `Deck` (from the deck's framing).
  """

  corners: Dict[int, Tuple[float, float, float]]
  x_span: float
  y_span: float
  framed: Dict[int, str] = field(default_factory=dict)

  def corner(self, pod_number: int) -> Tuple[float, float, float]:
    """Raises:
    ValueError: for a pod with no corner here.
    """
    if pod_number not in self.corners:
      raise ValueError(f"no corner for pod {pod_number}; have {sorted(self.corners)}")
    return self.corners[pod_number]


@dataclass(frozen=True)
class AlpPlacement:
  """One ALP of a deck layout, as an instrument file places it (`Decks/<deck>/Groups/<ALP>`): its
  name and Biomek type, the pin column of its left pins and the pin row of its front pins, the
  names of its positions, and the X Biomek has its left edge at (`X1`, cm), which checks the
  column."""

  name: str
  alp_type: str
  column: int
  row: int
  positions: Tuple[str, ...]
  x1: Optional[float] = None


SOURCES = ("instrument_file", "master_controller")


def source_parts(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
  """A serialized configuration's values split by the source each comes from (`SOURCES`)."""
  pods = data.get("pods", {})
  return {
    "instrument_file": {
      "instrument_serial_number": data.get("instrument_serial_number"),
      "pods": {
        side: {k: v for k, v in pod.items() if k != "module_serial_number"}
        for side, pod in pods.items()
      },
      "deck_positions": data.get("deck_positions", {}),
      "deck_layout": data.get("deck_layout", []),
    },
    "master_controller": {
      "chassis_serial_number": data.get("chassis_serial_number"),
      "module_serial_numbers": {
        side: pod.get("module_serial_number") for side, pod in pods.items()
      },
      "modules": data.get("modules", {}),
    },
  }


def fingerprint(value: Any) -> str:
  """A digest of `value`'s canonical JSON: equal values, equal fingerprints."""
  text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
  return "sha256:" + hashlib.sha256(text.encode("ascii")).hexdigest()


def differences(old: Any, new: Any, path: str = "") -> List[str]:
  """Where two JSON values differ, one line per leaf (`pods/left/limits/X: [..] -> [..]`)."""
  if isinstance(old, dict) and isinstance(new, dict):
    lines = []
    for key in sorted(set(old) | set(new), key=str):
      sub = f"{path}/{key}" if path else str(key)
      if key not in old:
        lines.append(f"{sub}: added")
      elif key not in new:
        lines.append(f"{sub}: removed")
      else:
        lines += differences(old[key], new[key], sub)
    return lines
  if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
    return [
      line for i, (a, b) in enumerate(zip(old, new)) for line in differences(a, b, f"{path}[{i}]")
    ]
  return [] if old == new else [f"{path}: {old!r} -> {new!r}"]


@dataclass(frozen=True)
class SourcesCheck:
  """How a configuration's sources compare with it now (`DeviceConfiguration.check_sources`):
  `changed` lists each value that differs, `unavailable` each source that could not be found."""

  changed: List[str]
  unavailable: List[str]


@dataclass
class DeviceConfiguration:
  """Everything declared about one instrument: its motor tables and its pods.

  `chassis_serial_number` and each pod's `module_serial_number` tie the file to the instrument it
  describes: a live session refuses a file whose serial numbers are not the instrument's
  (`BiomekI7Driver.discover`). `generic` marks a file that describes no instrument in particular
  (the package's default, for simulation; `anonymized`): it carries no serial numbers and no
  measured hysteresis, and a live session refuses it. `instrument_serial_number` is the instrument
  file's own, for information only. `sources`: per source (`SOURCES`), its `fingerprint`, and for
  an instrument's own file where (`path`, `kind`) and when (`read_at`) it was read."""

  chassis_serial_number: Optional[str]
  instrument_serial_number: Optional[str]
  modules: Dict[str, Any]
  pods: Dict[Side, PodConfiguration]
  sources: Dict[str, Dict[str, str]] = field(default_factory=dict)
  # The default deck's positions by Biomek's name (`P1`, `TL1`), as calibrated; empty if not
  # imported.
  deck_positions: Dict[str, DeckPosition] = field(default_factory=dict)
  generic: bool = False
  # The default deck's ALPs, as the instrument file places them; empty if not imported.
  deck_layout: List[AlpPlacement] = field(default_factory=list)

  @property
  def motor_tables(self) -> MotorTables:
    return MotorTables.from_dict(self.modules)

  def pod(self, side: Side) -> PodConfiguration:
    """Raises:
    ValueError: if nothing is declared for that side.
    """
    if side not in self.pods:
      raise ValueError(f"no pod declared on the {side}; declared: {sorted(self.pods)}")
    return self.pods[side]

  @classmethod
  def from_biomek(
    cls,
    bif: Union[str, Path],
    master_controller_db: Optional[Union[str, Path]] = None,
    master_controller_record: Optional[Union[str, Path]] = None,
    chassis_serial_number: Optional[str] = None,
  ) -> "DeviceConfiguration":
    """An instrument's declared configuration, from its Biomek files: the only way one is made.

    Args:
      bif: Biomek's instrument file (on the instrument's computer,
        `C:\\Users\\Public\\Documents\\Biomek5\\Biomek_i7.bif`).
      master_controller_db: the Master Controller's database (`C:\\ProgramData\\Beckman
        Coulter\\Biomek Master Controller\\Log.db3`), read-only.
      master_controller_record: instead of the database, a text file holding its instrument
        record (`biomek_files.master_controller.write_record`).
      chassis_serial_number: which instrument's record, when the database holds more than one.

    Raises:
      ValueError: for files that do not describe one consistent instrument (see
        `biomek_files.importer`).
    """
    from pylabrobot.beckman_coulter.biomek_i7.biomek_files.importer import import_configuration

    return import_configuration(
      bif,
      master_controller_db=master_controller_db,
      master_controller_record=master_controller_record,
      chassis_serial_number=chassis_serial_number,
    )

  def check_sources(self) -> SourcesCheck:
    """Read this configuration's sources again (read-only) and say what has changed since it was
    imported, and which sources cannot be found here."""
    from pylabrobot.beckman_coulter.biomek_i7.biomek_files.importer import check_sources

    return check_sources(self)

  def fingerprints(self) -> Dict[str, str]:
    """The fingerprint of the values taken from each source, as they are now."""
    parts = source_parts(self.to_dict())
    return {name: fingerprint(parts[name]) for name in SOURCES}

  def unrecorded_changes(self) -> List[str]:
    """The sources whose values no longer match the fingerprints recorded at import: changed after
    the file was made, by hand or by anything but `from_biomek`."""
    now = self.fingerprints()
    return [name for name in SOURCES if self.sources.get(name, {}).get("fingerprint") != now[name]]

  def anonymized(self) -> "DeviceConfiguration":
    """This configuration as a generic Biomek i7: every serial number removed (any key naming one,
    at any depth), the measured hysteresis zeroed, `generic` set, and the sources reduced to their
    fingerprints (no path or date)."""
    data = self.to_dict()

    def scrub(value: Any) -> Any:
      if isinstance(value, dict):
        return {k: None if "serial" in k.lower() else scrub(v) for k, v in value.items()}
      if isinstance(value, list):
        return [scrub(v) for v in value]
      return value

    data = scrub(data)
    for axes in data["modules"].values():
      for axis in axes:
        axis["hysteresis"] = 0
    data["generic"] = True
    data["sources"] = {}
    generic = DeviceConfiguration.from_dict(data)
    generic.sources = {name: {"fingerprint": f} for name, f in generic.fingerprints().items()}
    return generic

  @classmethod
  def load(cls, path: Union[str, Path]) -> "DeviceConfiguration":
    """An instrument's declared configuration, from the file `from_biomek` made and `save` wrote.

    Raises:
      ValueError: if its values no longer match the fingerprints recorded at import: it was
        changed afterwards. Make it again with `from_biomek`.
    """
    with open(path, encoding="utf-8") as fh:
      config = cls.from_dict(json.load(fh))
    changed = config.unrecorded_changes()
    if changed:
      raise ValueError(
        f"{path}: the values from {' and '.join(changed)} are not the ones imported (changed "
        "after import, or not made by `DeviceConfiguration.from_biomek`); import it again from "
        "the instrument's Biomek files"
      )
    return config

  def save(self, path: Union[str, Path], indent: Optional[int] = 2) -> None:
    """Write it as `load` reads it; `indent` as `json.dump`'s (None: one line)."""
    Path(path).write_text(json.dumps(self.to_dict(), indent=indent) + "\n", encoding="utf-8")

  @classmethod
  def from_dict(cls, data: Dict[str, Any]) -> "DeviceConfiguration":
    pods: Dict[Side, PodConfiguration] = {}
    for side, p in data.get("pods", {}).items():
      pods[side] = PodConfiguration(
        component=p["component"],
        module_type=p["module_type"],
        side=side,
        conversion={k: AxisConversion(**v) for k, v in p["conversion"].items()},
        limits={k: (v[0], v[1]) for k, v in p["limits"].items()},
        geometry=PodGeometry(**p["geometry"]),
        park=dict(p.get("park", {})),
        settings=dict(p.get("settings", {})),
        gripper_model=dict(p.get("gripper_model", {})),
        obstacles=[Obstacle(**o) for o in p.get("obstacles", [])],
        source=p.get("source", ""),
        module_serial_number=p.get("module_serial_number"),
      )
    positions = {
      name: DeckPosition(
        corners={int(n): (c[0], c[1], c[2]) for n, c in p["corners"].items()},
        x_span=p["x_span"],
        y_span=p["y_span"],
        framed={int(n): v for n, v in p.get("framed", {}).items()},
      )
      for name, p in data.get("deck_positions", {}).items()
    }
    return cls(
      chassis_serial_number=data.get("chassis_serial_number"),
      instrument_serial_number=data.get("instrument_serial_number"),
      modules=data["modules"],
      pods=pods,
      sources=copy.deepcopy(data.get("sources", {})),
      deck_positions=positions,
      generic=bool(data.get("generic", False)),
      deck_layout=[
        AlpPlacement(
          name=a["name"],
          alp_type=a["alp_type"],
          column=a["column"],
          row=a["row"],
          positions=tuple(a["positions"]),
          x1=a.get("x1"),
        )
        for a in data.get("deck_layout", [])
      ],
    )

  def to_dict(self) -> Dict[str, Any]:
    return {
      "generic": self.generic,
      "chassis_serial_number": self.chassis_serial_number,
      "instrument_serial_number": self.instrument_serial_number,
      "sources": self.sources,
      "pods": {
        side: {
          "component": p.component,
          "module_type": p.module_type,
          "module_serial_number": p.module_serial_number,
          "source": p.source,
          "conversion": {k: asdict(c) for k, c in p.conversion.items()},
          "limits": {k: list(v) for k, v in p.limits.items()},
          "geometry": {k: v for k, v in asdict(p.geometry).items() if v is not None},
          "park": p.park,
          "settings": p.settings,
          "gripper_model": p.gripper_model,
          "obstacles": [{k: v for k, v in asdict(o).items() if v is not None} for o in p.obstacles],
        }
        for side, p in self.pods.items()
      },
      "deck_positions": {
        name: {
          "corners": {str(n): list(c) for n, c in p.corners.items()},
          "x_span": p.x_span,
          "y_span": p.y_span,
          "framed": {str(n): v for n, v in p.framed.items()},
        }
        for name, p in self.deck_positions.items()
      },
      "deck_layout": [{**asdict(a), "positions": list(a.positions)} for a in self.deck_layout],
      "modules": self.modules,
    }
