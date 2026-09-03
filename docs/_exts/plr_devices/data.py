"""Loading and validation of the device registry (``devices.json``)."""

import json
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


class DeviceRegistryError(ValueError):
  """A registry entry is malformed."""


# Controlled vocabularies. They keep near-synonyms from drifting apart ("sealer" vs "heat
# sealer"), which would split the filter chips in two. Adding a genuinely new kind or capability
# means adding it here as well, deliberately.

KINDS = (
  "arm",
  "barcode scanner",
  "bulk dispenser",
  "centrifuge",
  "centrifuge loader",
  "decapper",
  "delidder",
  "electroporator",
  "fan",
  "flow cytometer",
  "heater shaker",
  "liquid handler",
  "microscope",
  "peeler",
  "plate reader",
  "plate washer",
  "pump",
  "qPCR machine",
  "scale",
  "sealer",
  "shaker",
  "storage",
  "temperature controller",
  "thermocycler",
  "tilter",
)

CAPABILITIES = (
  "absorbance",
  "active cooling",
  "air filtration",
  "arm",
  "barcode reading",
  "centrifuging",
  "decapping",
  "delidding",
  "dispensing",
  "electroporation",
  "flow cytometry",
  "fluorescence",
  "fluorescence polarization",
  "grinding",
  "heating",
  "liquid handling",
  "luminescence",
  "microscopy",
  "peeling",
  "plate washing",
  "pumping",
  "qPCR",
  "sealing",
  "shaking",
  "storage",
  "thermocycling",
  "tilting",
  "time-resolved fluorescence",
  "weighing",
)

# Ordered from least to most complete; the table renders statuses in this order.
STATUSES = ("wip", "basic", "mostly", "full")

STATUS_LABELS = {
  "wip": "WIP",
  "basic": "Basic",
  "mostly": "Mostly",
  "full": "Full",
}

STATUS_DESCRIPTIONS = {
  "wip": "Work in progress.",
  "basic": "Core functionality is available.",
  "mostly": "Most capabilities are available, but some known commands are still missing.",
  "full": "Comprehensive support (at least 90% of capabilities), with documentation.",
}

API_VERSIONS = ("v0", "v1")

API_VERSION_DESCRIPTIONS = {
  "v0": "Driver still lives under pylabrobot.legacy and is being migrated.",
  "v1": "Driver uses the current API.",
}

REQUIRED_FIELDS = ("id", "vendor", "name", "kind", "status")

OPTIONAL_FIELDS = (
  "capabilities",
  "models",
  "api",
  "api_version",
  "code_slug",
  "doc_slug",
  "manager",
  "oem",
  "notes",
)

_ALL_FIELDS = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)


class Device(Dict[str, Any]):
  """A single registry entry: a plain dict with a few derived conveniences."""

  @property
  def id(self) -> str:
    return str(self["id"])

  @property
  def title(self) -> str:
    return f"{self['vendor']} {self['name']}"

  @property
  def status_label(self) -> str:
    return STATUS_LABELS.get(str(self["status"]), str(self["status"]))


def capability_hue(capability: str) -> int:
  """A stable hue (0-359) for a capability, so badge colors survive new capabilities."""
  return zlib.crc32(capability.encode("utf-8")) % 360


def _validate(device: Any, index: int, seen_ids: Dict[str, int], path: Path) -> Device:
  where = f"{path}[{index}]"

  if not isinstance(device, dict):
    raise DeviceRegistryError(
      f"{where}: device entries must be JSON objects, got {type(device).__name__}"
    )

  missing = [f for f in REQUIRED_FIELDS if not device.get(f)]
  if missing:
    raise DeviceRegistryError(f"{where}: missing required field(s): {', '.join(missing)}")

  unknown = sorted(set(device) - _ALL_FIELDS)
  if unknown:
    raise DeviceRegistryError(f"{where} ({device['id']}): unknown field(s): {', '.join(unknown)}")

  device_id = device["id"]
  if device_id in seen_ids:
    raise DeviceRegistryError(
      f"{where}: duplicate device id {device_id!r} (first seen at index {seen_ids[device_id]})"
    )
  seen_ids[device_id] = index

  if device["kind"] not in KINDS:
    raise DeviceRegistryError(
      f"{where} ({device_id}): kind {device['kind']!r} is not a known kind. Add it to KINDS in "
      f"{Path(__file__).name} if it is genuinely new."
    )

  if device["status"] not in STATUSES:
    raise DeviceRegistryError(
      f"{where} ({device_id}): status {device['status']!r} is not one of {', '.join(STATUSES)}"
    )

  api_version = device.get("api_version")
  if api_version is not None and api_version not in API_VERSIONS:
    raise DeviceRegistryError(
      f"{where} ({device_id}): api_version {api_version!r} is not one of {', '.join(API_VERSIONS)}"
    )

  for field in ("manager", "oem"):
    value = device.get(field)
    if value is not None and not str(value).startswith(("http://", "https://")):
      raise DeviceRegistryError(f"{where} ({device_id}): {field} must be a URL, got {value!r}")

  capabilities = device.get("capabilities", [])
  if not isinstance(capabilities, list) or not all(isinstance(c, str) for c in capabilities):
    raise DeviceRegistryError(f"{where} ({device_id}): capabilities must be a list of strings")

  unknown_capabilities = [c for c in capabilities if c not in CAPABILITIES]
  if unknown_capabilities:
    raise DeviceRegistryError(
      f"{where} ({device_id}): unknown capability/capabilities: {', '.join(unknown_capabilities)}. "
      f"Add to CAPABILITIES in {Path(__file__).name} if genuinely new."
    )

  models = device.get("models", [])
  if not isinstance(models, list):
    raise DeviceRegistryError(f"{where} ({device_id}): models must be a list of objects")
  for model_index, model in enumerate(models):
    model_where = f"{where} ({device_id}) models[{model_index}]"
    if not isinstance(model, dict):
      raise DeviceRegistryError(f"{model_where}: model must be an object")
    unknown_model_fields = sorted(set(model) - {"name", "status"})
    if unknown_model_fields:
      raise DeviceRegistryError(
        f"{model_where}: unknown field(s): {', '.join(unknown_model_fields)}"
      )
    if not isinstance(model.get("name"), str) or not model["name"]:
      raise DeviceRegistryError(f"{model_where}: name must be a non-empty string")
    model_status = model.get("status")
    if model_status is not None and model_status not in STATUSES:
      raise DeviceRegistryError(
        f"{model_where}: status {model_status!r} is not one of {', '.join(STATUSES)}"
      )

  return Device(device)


def load_devices(path: Path) -> List[Device]:
  """Read and validate the registry at ``path``, sorted by vendor and then name."""

  if not path.is_file():
    raise DeviceRegistryError(f"device registry not found: {path}")

  with open(path, encoding="utf-8") as f:
    try:
      raw = json.load(f)
    except json.JSONDecodeError as e:
      raise DeviceRegistryError(f"{path}: invalid JSON: {e}") from e

  if not isinstance(raw, list):
    raise DeviceRegistryError(f"{path}: expected a JSON array of devices, got {type(raw).__name__}")

  seen_ids: Dict[str, int] = {}
  devices = [_validate(d, i, seen_ids, path) for i, d in enumerate(raw)]
  devices.sort(key=lambda d: (str(d["vendor"]).lower(), str(d["name"]).lower()))
  return devices


def registry_path(app_or_env) -> Path:
  """Absolute path of the registry. Accepts either the app or the build environment."""
  base = getattr(app_or_env, "confdir", None) or app_or_env.srcdir
  return Path(base) / app_or_env.config.plr_devices_json


def get_devices(app) -> List[Device]:
  """Return the registry for this build, loading it on first use."""

  devices = getattr(app, "plr_devices", None)
  if devices is None:
    devices = load_devices(registry_path(app))
    app.plr_devices = devices
  return devices


def get_device(app, device_id: str) -> Optional[Device]:
  for device in get_devices(app):
    if device["id"] == device_id:
      return device
  return None


def filter_devices(devices: Sequence[Device], filters: Dict[str, str]) -> List[Device]:
  """Keep devices matching every filter."""

  def matches(device: Device, field: str, wanted: str) -> bool:
    if field == "capabilities":
      return wanted.lower() in {c.lower() for c in device.get("capabilities", [])}
    return str(device.get(field, "")).lower() == wanted.lower()

  return [d for d in devices if all(matches(d, f, w) for f, w in filters.items() if w)]
