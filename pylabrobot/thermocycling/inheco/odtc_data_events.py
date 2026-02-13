"""Parse ODTC SiLA DataEvent payloads into structured snapshots."""

from __future__ import annotations

import html
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ODTCDataEventSnapshot:
  """Parsed snapshot from one DataEvent (elapsed time and temperatures)."""

  elapsed_s: float
  target_temp_c: Optional[float] = None
  current_temp_c: Optional[float] = None
  lid_temp_c: Optional[float] = None


def _parse_series_value(series_elem: Any) -> Optional[float]:
  """Extract last integerValue from a dataSeries element as float."""
  values = series_elem.findall(".//integerValue")
  if not values:
    return None
  text = values[-1].text
  if text is None:
    return None
  try:
    return float(text)
  except ValueError:
    return None


def parse_data_event_payload(payload: Dict[str, Any]) -> Optional[ODTCDataEventSnapshot]:
  """Parse a single DataEvent payload into an ODTCDataEventSnapshot.

  Input: dict with 'requestId' and 'dataValue' (string of XML, possibly
  double-escaped). Extracts Elapsed time (ms), Target temperature, Current
  temperature, LID temperature (1/100°C -> °C). Returns None on parse error.
  """
  if not isinstance(payload, dict):
    return None
  data_value = payload.get("dataValue")
  if not data_value or not isinstance(data_value, str):
    return None
  try:
    outer = ET.fromstring(data_value)
  except ET.ParseError:
    return None
  any_data = outer.find(".//{*}AnyData") or outer.find(".//AnyData")
  if any_data is None or any_data.text is None:
    return None
  inner_xml = any_data.text.strip()
  if not inner_xml:
    return None
  if "&lt;" in inner_xml or "&gt;" in inner_xml:
    inner_xml = html.unescape(inner_xml)
  try:
    inner = ET.fromstring(inner_xml)
  except ET.ParseError:
    return None
  elapsed_s = 0.0
  target_temp_c: Optional[float] = None
  current_temp_c: Optional[float] = None
  lid_temp_c: Optional[float] = None
  for elem in inner.iter():
    if not elem.tag.endswith("dataSeries"):
      continue
    name_id = elem.get("nameId")
    unit = elem.get("unit") or ""
    raw = _parse_series_value(elem)
    if raw is None:
      continue
    if name_id == "Elapsed time" and unit == "ms":
      elapsed_s = raw / 1000.0
    elif name_id == "Target temperature" and unit == "1/100°C":
      target_temp_c = raw / 100.0
    elif name_id == "Current temperature" and unit == "1/100°C":
      current_temp_c = raw / 100.0
    elif name_id == "LID temperature" and unit == "1/100°C":
      lid_temp_c = raw / 100.0
  return ODTCDataEventSnapshot(
    elapsed_s=elapsed_s,
    target_temp_c=target_temp_c,
    current_temp_c=current_temp_c,
    lid_temp_c=lid_temp_c,
  )
