"""Make an instrument's declared configuration from its Biomek files, and check it against them.

`import_configuration` is the one way a declared configuration is made
(`DeviceConfiguration.from_biomek`):
1. the instrument file gives the pods, the deck's positions and its layout
   (`instrument_file.read_instrument_file`);
2. the Master Controller's instrument record gives the motor tables and the modules' serial
   numbers (`master_controller.read_master_controller`);
3. the two must describe one instrument: an arm on the same sides in both, each pod's arm type in
   the record, and every travel window the instrument file gives inside the motor's own limits;
4. `sources` records each source's path, kind, read time and the fingerprint of the values taken
   from it.

`check_sources` reads the recorded sources again and lists what changed since.
"""

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pylabrobot.beckman_coulter.biomek_i7.biomek_files.instrument_file import read_instrument_file
from pylabrobot.beckman_coulter.biomek_i7.biomek_files.master_controller import (
  read_master_controller,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import (
  SOURCES,
  DeviceConfiguration,
  SourcesCheck,
  differences,
  fingerprint,
  source_parts,
)


def _inconsistencies(
  instrument_file: Dict[str, Any], master_controller: Dict[str, Any]
) -> List[str]:
  """How the two sources disagree about the instrument, one sentence each."""
  problems = []
  serials = master_controller["module_serial_numbers"]
  for side in sorted(set(instrument_file["pods"]) | set(serials)):
    pod = instrument_file["pods"].get(side)
    if pod is None or side not in serials:
      problems.append(
        f"the {side} arm is in the {'Master Controller record' if pod is None else 'instrument file'}"
        " only"
      )
      continue
    if pod["module_type"] not in master_controller["modules"]:
      problems.append(f"the {side} pod is a {pod['module_type']}; the record has no such arm")
      continue
    axes = {a["name"]: a for a in master_controller["modules"][pod["module_type"]]}
    for axis, (low, high) in pod["limits"].items():
      conversion = pod["conversion"].get(axis)
      if conversion is None or axis not in axes:
        continue
      ends = sorted(round((v + conversion["offset"]) * conversion["const"]) for v in (low, high))
      limits = axes[axis]["limits"]
      if ends[0] < limits["min_position"] or ends[1] > limits["max_position"]:
        problems.append(
          f"the {side} pod's {axis} travel, counts {ends}, is outside the motor's "
          f"[{limits['min_position']}, {limits['max_position']}]"
        )
  return problems


def _now() -> str:
  return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def import_configuration(
  bif: Union[str, Path],
  master_controller_db: Optional[Union[str, Path]] = None,
  master_controller_record: Optional[Union[str, Path]] = None,
  chassis_serial_number: Optional[str] = None,
) -> DeviceConfiguration:
  """`DeviceConfiguration.from_biomek`.

  Raises:
    ValueError: for files that do not describe one consistent instrument.
  """
  instrument_file = read_instrument_file(bif)
  master_controller = read_master_controller(
    master_controller_db=master_controller_db,
    master_controller_record=master_controller_record,
    chassis_serial_number=chassis_serial_number,
  )
  problems = _inconsistencies(instrument_file, master_controller)
  if problems:
    raise ValueError(
      "the instrument file and the Master Controller's record do not describe one instrument: "
      + "; ".join(problems)
    )
  pods = {
    side: {**pod, "module_serial_number": master_controller["module_serial_numbers"][side]}
    for side, pod in instrument_file["pods"].items()
  }
  config = DeviceConfiguration.from_dict({
    "generic": False,
    "chassis_serial_number": master_controller["chassis_serial_number"],
    "instrument_serial_number": instrument_file["instrument_serial_number"],
    "pods": pods,
    "deck_positions": instrument_file["deck_positions"],
    "deck_layout": instrument_file["deck_layout"],
    "modules": master_controller["modules"],
  })  # fmt: skip
  fingerprints = config.fingerprints()
  read_at = _now()
  config.sources = {
    "instrument_file": {
      "kind": "bif",
      "path": str(Path(bif).resolve()),
      "read_at": read_at,
      "fingerprint": fingerprints["instrument_file"],
    },
    "master_controller": {
      "kind": "database" if master_controller_db is not None else "record",
      "path": str(Path(master_controller_db or master_controller_record).resolve()),  # type: ignore[arg-type]
      "read_at": read_at,
      "fingerprint": fingerprints["master_controller"],
    },
  }
  return config


def _read_again(config: DeviceConfiguration, name: str, path: Path) -> Dict[str, Any]:
  """The serialized configuration with the values from source `name` read again from `path`."""
  data = config.to_dict()
  if name == "instrument_file":
    fresh = read_instrument_file(path)
    data["instrument_serial_number"] = fresh["instrument_serial_number"]
    data["deck_positions"] = fresh["deck_positions"]
    data["deck_layout"] = fresh["deck_layout"]
    data["pods"] = {
      side: {**pod, "module_serial_number": data["pods"].get(side, {}).get("module_serial_number")}
      for side, pod in fresh["pods"].items()
    }
  else:
    kind = config.sources[name].get("kind")
    fresh = read_master_controller(
      master_controller_db=path if kind == "database" else None,
      master_controller_record=path if kind == "record" else None,
      chassis_serial_number=config.chassis_serial_number,
    )
    data["chassis_serial_number"] = fresh["chassis_serial_number"]
    data["modules"] = fresh["modules"]
    for side, pod in data["pods"].items():
      pod["module_serial_number"] = fresh["module_serial_numbers"].get(side)
  # Through the dataclasses, so both sides are serialized the same way.
  return DeviceConfiguration.from_dict(data).to_dict()


def check_sources(config: DeviceConfiguration) -> SourcesCheck:
  """`DeviceConfiguration.check_sources`.

  Raises:
    ValueError: for a source that is there but no longer reads as one (the reader's own error).
  """
  changed: List[str] = []
  unavailable: List[str] = []
  for name in SOURCES:
    source = config.sources.get(name, {})
    if "path" not in source:
      unavailable.append(f"{name}: no path recorded")
      continue
    path = Path(source["path"])
    if not path.exists():
      unavailable.append(f"{name}: {path} not found")
      continue
    fresh = source_parts(_read_again(config, name, path))[name]
    if fingerprint(fresh) != source["fingerprint"]:
      declared = source_parts(config.to_dict())[name]
      changed += [f"{name}: {line}" for line in differences(declared, fresh)]
  return SourcesCheck(changed=changed, unavailable=unavailable)
