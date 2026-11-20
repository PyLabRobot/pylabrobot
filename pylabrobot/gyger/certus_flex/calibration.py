"""Measured calibration curves, vendor XML import, and portable PLR configuration."""

import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _finite(value: float, name: str) -> float:
  """Require a finite numeric calibration value."""
  if isinstance(value, bool) or not math.isfinite(value):
    raise ValueError(f"{name} must be a finite number")
  return value


def _quantity(value: str, units: dict[str, float]) -> float:
  """Parse explicit vendor quantity units without assuming a unit for bare numbers."""
  match = re.fullmatch(
    r"\s*([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\s*\[?([a-zA-Zµμ]+)\]?\s*",
    value,
  )
  if match is None or match[2].replace("μ", "µ").lower() not in units:
    raise ValueError(f"Unsupported or unitless vendor quantity: {value!r}")
  result = float(match[1]) * units[match[2].replace("μ", "µ").lower()]
  return _finite(result, "vendor quantity")


def _xml(text: str) -> ET.Element:
  """Parse a bounded vendor document without DTDs or entity declarations."""
  if len(text) > 5_000_000 or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
    raise ValueError("Calibration XML is oversized or contains entity declarations")
  return ET.fromstring(text)


@dataclass(frozen=True)
class ValveCalibration:
  """Measured (volume in µL, opening duration in seconds) points at a given pressure.

  Points must increase in both volume and duration. Interpolation is linear, as in
  CERTUS CONTROL. Extrapolation is rejected. Use the calibration for the installed
  valve and fluid, at the stated pressure in bar.
  """

  points: tuple[tuple[float, float], ...]
  pressure: float
  fluid: Optional[str] = None
  valve_serial: Optional[str] = None
  valve_type: Optional[str] = None
  source_id: Optional[str] = None
  vendor_xml: Optional[str] = None

  def __post_init__(self) -> None:
    """Validate the measured range and pressure."""
    if not 0.051 <= _finite(self.pressure, "pressure") <= 1:
      raise ValueError("Calibration pressure must be between 0.051 and 1 bar")
    if len(self.points) < 2:
      raise ValueError("At least two calibration points are required")
    previous = (0.0, 0.0)
    for volume, duration in self.points:
      if _finite(volume, "volume") <= previous[0] or _finite(duration, "duration") <= previous[1]:
        raise ValueError("Calibration volumes and durations must be positive and increasing")
      previous = (volume, duration)

  def duration_for_volume(self, volume: float, *, correction_factor: float = 1.0) -> float:
    """Interpolate seconds for a volume in µL, after applying a volume correction.

    The factor uses CERTUS CONTROL's supported range of 0.5–1.5. It multiplies
    the requested volume before interpolation, not the resulting opening time.
    The corrected volume must remain within the measured calibration range.
    """
    _finite(volume, "volume")
    if not 0.5 <= _finite(correction_factor, "correction_factor") <= 1.5:
      raise ValueError("correction_factor must be between 0.5 and 1.5")
    volume *= correction_factor
    if not self.points[0][0] <= volume <= self.points[-1][0]:
      raise ValueError("Volume is outside the measured calibration range")
    for (v0, t0), (v1, t1) in zip(self.points, self.points[1:]):
      if v0 <= volume <= v1:
        return t0 + (volume - v0) * (t1 - t0) / (v1 - v0)
    raise ValueError("Volume is outside the measured calibration range")

  def validate_for(self, *, fluid: str, valve_serial: str) -> None:
    """Match declared installed fluid and valve identity to calibration metadata."""
    if not self.fluid or not self.valve_serial:
      raise ValueError("Calibration needs fluid and valve serial metadata")
    if self.fluid.casefold() != fluid.strip().casefold():
      raise ValueError("Calibration fluid does not match the declared channel fluid")
    if self.valve_serial.casefold() != valve_serial.strip().casefold():
      raise ValueError("Calibration valve serial does not match the declared installed valve")

  def to_dict(self) -> dict:
    """Serialize a calibration in PLR default units, including its source metadata."""
    return {
      "version": 1,
      "points": [list(p) for p in self.points],
      "pressure": self.pressure,
      "fluid": self.fluid,
      "valve_serial": self.valve_serial,
      "valve_type": self.valve_type,
      "source_id": self.source_id,
      "vendor_xml": self.vendor_xml,
    }

  @classmethod
  def from_dict(cls, data: dict) -> "ValveCalibration":
    """Load the explicitly versioned PLR calibration representation."""
    if data.get("version") != 1:
      raise ValueError("Unsupported calibration format version")
    return cls(
      points=tuple(tuple(p) for p in data["points"]),
      pressure=data["pressure"],
      fluid=data.get("fluid"),
      valve_serial=data.get("valve_serial"),
      valve_type=data.get("valve_type"),
      source_id=data.get("source_id"),
      vendor_xml=data.get("vendor_xml"),
    )

  def save(self, path: str | Path) -> None:
    """Write a reusable PLR calibration file in µL, seconds, and bar."""
    Path(path).write_text(json.dumps(self.to_dict(), indent=2, allow_nan=False) + "\n")

  @classmethod
  def load(cls, path: str | Path) -> "ValveCalibration":
    """Load a PLR calibration file without contacting hardware."""
    return cls.from_dict(json.loads(Path(path).read_text()))

  @classmethod
  def from_vendor_xml(
    cls, text: str, *, calibration_id: Optional[str] = None
  ) -> "ValveCalibration":
    """Import one CERTUS CONTROL calibration-map, rejecting ambiguous selections.

    Disabled points are excluded. XML volume is already per shot: ``used-shots``
    and total weight are metadata and must not divide the volume again.
    Unknown metadata is preserved for lossless vendor re-export.
    """
    root = _xml(text)
    maps = list(root.iter("calibration-map"))
    if calibration_id is not None:
      maps = [m for m in maps if m.get("id") == calibration_id]
    if len(maps) != 1:
      raise ValueError("Select exactly one vendor calibration-map by calibration_id")
    node = maps[0]
    supply, fluid, valve = node.find("fluid-supply"), node.find("fluid"), node.find("valve")
    if supply is None or fluid is None or valve is None:
      raise ValueError("Vendor calibration lacks fluid, valve, or supply configuration")
    pressure = _quantity(
      supply.attrib["dispense-pressure"], {"bar": 1, "mbar": 0.001, "kpa": 0.01, "pa": 0.00001}
    )
    points = []
    for p in node.findall("calibration-points/calibration-point"):
      enabled = p.get("enable", "true").lower()
      if enabled not in ("true", "false", "1", "0"):
        raise ValueError("Invalid calibration point enable flag")
      if enabled in ("false", "0"):
        continue
      volume = _quantity(
        p.attrib["volume"], {"µl": 1, "ul": 1, "nl": 0.001, "ml": 1000, "l": 1e6, "pl": 1e-6}
      )
      duration = _quantity(
        p.attrib["valve-open-duration"], {"s": 1, "ms": 0.001, "us": 1e-6, "µs": 1e-6}
      )
      points.append((volume, duration))
    points.sort(key=lambda p: p[1])
    return cls(
      tuple(points),
      pressure,
      fluid=fluid.get("name"),
      valve_serial=valve.get("valve-serial-number"),
      valve_type=valve.get("valve-type"),
      source_id=node.get("id"),
      vendor_xml=ET.tostring(node, encoding="unicode"),
    )

  def to_vendor_xml(self) -> str:
    """Return the imported vendor map with all metadata and disabled points retained.

    PLR-created or edited curves should be exported as PLR JSON; inventing vendor
    identities, connection metadata, or measurement dates would be misleading.
    """
    if self.vendor_xml is None:
      raise ValueError("Vendor XML export requires an imported vendor calibration")
    original = self.from_vendor_xml(self.vendor_xml)
    if (
      self.points,
      self.pressure,
      self.fluid,
      self.valve_serial,
      self.valve_type,
      self.source_id,
    ) != (
      original.points,
      original.pressure,
      original.fluid,
      original.valve_serial,
      original.valve_type,
      original.source_id,
    ):
      raise ValueError("Calibration changed; export the edited curve as PLR JSON")
    return self.vendor_xml
