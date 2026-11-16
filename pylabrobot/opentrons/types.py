"""Values exchanged with the Opentrons robot-server API."""

import math
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
class DoorStatus:
  """The front door as the OT-2's door switch reports it."""

  closed: bool
  required_closed_for_protocol: bool

  @classmethod
  def from_response(cls, data: Dict[str, Any]) -> "DoorStatus":
    """Parse the ``data`` object of ``GET /robot/door/status``.

    Args:
      data: the response's ``data`` object.

    Raises:
      OpentronsProtocolError: If the status is neither "open" nor "closed", or the requirement is
        not a boolean.
    """
    status = _string(data, "status")
    if status not in {"open", "closed"}:
      raise OpentronsProtocolError(f"Unknown door status {status!r}")
    required = data.get("doorRequiredClosedForProtocol")
    if not isinstance(required, bool):
      raise OpentronsProtocolError(
        f"Expected a boolean for 'doorRequiredClosedForProtocol', got {required!r}"
      )
    return cls(closed=status == "closed", required_closed_for_protocol=required)


@dataclass(frozen=True)
class DeckFixture:
  """A fixture installed in a Flex deck cutout."""

  cutout_id: str
  fixture_id: Optional[str]

  @classmethod
  def from_response(cls, data: Dict[str, Any]) -> "DeckFixture":
    """Parse one fixture from the robot's deck configuration."""
    return cls(_string(data, "cutoutId"), _optional_string(data, "cutoutFixtureId"))


FINISHED_RUN_STATUSES = frozenset({"stopped", "failed", "succeeded"})
"""Run statuses that no longer hold the robot: another run can be created beside them."""


@dataclass(frozen=True)
class RunInfo:
  """A run's identity and reported execution status.

  ``current`` says whether the robot holds this run as its current one, and ``created_at`` when it
  was created (ISO 8601, as the server reports it). Both are None where the response leaves them
  out: the robot reports them in a listing of its runs.
  """

  id: str
  status: Optional[str] = None
  current: Optional[bool] = None
  created_at: Optional[str] = None

  @property
  def finished(self) -> bool:
    """Whether the run no longer holds the robot."""
    return self.status in FINISHED_RUN_STATUSES

  @classmethod
  def from_response(cls, data: Dict[str, Any]) -> "RunInfo":
    """Parse a run from a run listing or a run reading.

    Raises:
      OpentronsProtocolError: If ``current`` is present but not a boolean.
    """
    current = data.get("current")
    if current is not None and not isinstance(current, bool):
      raise OpentronsProtocolError(f"Expected a boolean for 'current', got {current!r}")
    return cls(
      id=_string(data, "id"),
      status=_optional_string(data, "status"),
      current=current,
      created_at=_optional_string(data, "createdAt"),
    )


@dataclass(frozen=True)
class CommandInfo:
  """One command status reading, with an unpacked result or error."""

  status: str
  result: Dict[str, Any]
  error: Dict[str, Any]
  id: str = ""

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


@dataclass(frozen=True)
class InstrumentInfo:
  """A physical Flex instrument, independent of a run's loaded IDs."""

  mount: str
  instrument_type: str
  name: str
  model: str
  channels: Optional[int] = None
  minimum_volume: Optional[float] = None
  maximum_volume: Optional[float] = None

  @classmethod
  def from_response(cls, data: Dict[str, Any]) -> "InstrumentInfo":
    """Validate the robot's instrument identity and pipette specifications."""
    instrument_type = _string(data, "instrumentType")
    channels: Optional[int] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    if instrument_type == "pipette":
      details = _object(data.get("data"))
      channels = details.get("channels")
      minimum, maximum = details.get("min_volume"), details.get("max_volume")
      if not isinstance(channels, int) or isinstance(channels, bool) or channels <= 0:
        raise OpentronsProtocolError("Invalid pipette channel count")
      for volume in (minimum, maximum):
        if (
          not isinstance(volume, (float, int))
          or isinstance(volume, bool)
          or not math.isfinite(volume)
        ):
          raise OpentronsProtocolError("Invalid pipette volume range")
      assert minimum is not None and maximum is not None
      if minimum < 0 or maximum <= 0 or minimum > maximum:
        raise OpentronsProtocolError("Invalid pipette volume range")
    return cls(
      mount=_string(data, "mount"),
      instrument_type=instrument_type,
      name=_optional_string(data, "instrumentName") or "",
      model=_string(data, "instrumentModel"),
      channels=channels,
      minimum_volume=minimum,
      maximum_volume=maximum,
    )


@dataclass(frozen=True)
class PipetteInfo:
  """Pipette geometry used for channel-specific reach checks."""

  mount: str
  pipette_name: str
  pipette_model: str
  pipette_id: str
  channels: int
  min_volume: float
  max_volume: float
