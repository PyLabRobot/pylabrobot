"""Values exchanged with the Opentrons robot-server API."""

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from pylabrobot.opentrons.errors import OpentronsProtocolError

Mount = Literal["left", "right"]


def _object(value: Any) -> Dict[str, Any]:
  if not isinstance(value, dict):
    raise OpentronsProtocolError(f"Expected a JSON object, got {value!r}")
  return value


def _string(data: Dict[str, Any], key: str) -> str:
  value = data.get(key)
  if not isinstance(value, str) or not value:
    raise OpentronsProtocolError(f"Expected a non-empty string for {key!r}, got {value!r}")
  return value


def _optional_string(data: Dict[str, Any], key: str) -> Optional[str]:
  if data.get(key) is None:
    return None
  return _string(data, key)


@dataclass(frozen=True)
class RobotInfo:
  """A health reading. Software version is distinct from the HTTP API version header."""

  name: str
  model: str
  software_version: str
  firmware_version: Optional[str] = None
  serial_number: Optional[str] = None

  @classmethod
  def from_response(cls, data: Dict[str, Any]) -> "RobotInfo":
    return cls(
      name=_string(data, "name"),
      model=_string(data, "robot_model"),
      software_version=_string(data, "api_version"),
      firmware_version=_optional_string(data, "fw_version"),
      serial_number=_optional_string(data, "robot_serial"),
    )


@dataclass(frozen=True)
class MountedPipette:
  """A physical pipette, independent of any run's loaded pipette ID."""

  mount: Mount
  name: str
  model: Optional[str] = None
  serial_number: Optional[str] = None


@dataclass(frozen=True)
class ModuleInfo:
  """The identity of a connected module."""

  id: str
  module_type: Optional[str] = None
  model: Optional[str] = None
  serial_number: Optional[str] = None

  @classmethod
  def from_response(cls, data: Dict[str, Any]) -> "ModuleInfo":
    return cls(
      id=_string(data, "id"),
      module_type=_optional_string(data, "moduleType"),
      model=_optional_string(data, "moduleModel"),
      serial_number=_optional_string(data, "serialNumber"),
    )


@dataclass(frozen=True)
class RunInfo:
  """A run's identity and reported execution status."""

  id: str
  status: Optional[str] = None

  @classmethod
  def from_response(cls, data: Dict[str, Any]) -> "RunInfo":
    return cls(id=_string(data, "id"), status=_optional_string(data, "status"))


@dataclass(frozen=True)
class CommandInfo:
  """One command status reading, with an unpacked result or error."""

  status: str
  result: Dict[str, Any]
  error: Dict[str, Any]

  @classmethod
  def from_response(cls, data: Dict[str, Any]) -> "CommandInfo":
    return cls(
      status=_string(data, "status"),
      result=_object(data["result"]) if data.get("result") is not None else {},
      error=_object(data["error"]) if data.get("error") is not None else {},
    )


@dataclass(frozen=True)
class LabwareIdentity:
  """An Opentrons labware definition's namespace, load name, and revision."""

  namespace: str
  load_name: str
  version: int

  @classmethod
  def from_uri(cls, uri: str) -> "LabwareIdentity":
    try:
      namespace, load_name, version = uri.split("/")
      revision = int(version)
      if not namespace or not load_name or revision < 1:
        raise ValueError
    except ValueError as error:
      raise OpentronsProtocolError(f"Invalid labware definition URI {uri!r}") from error
    return cls(namespace, load_name, revision)
