from __future__ import annotations

import datetime as _dt
import re as _re
import xml.etree.ElementTree as ET
from typing import Any, Mapping, Optional

SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

ET.register_namespace("s", SOAP_ENV)
ET.register_namespace("xsi", XSI)


# --------- scalar parsing/formatting ---------

_INT_RE = _re.compile(r"^-?\d+$")
_FLOAT_RE = _re.compile(r"^-?\d+\.\d+$")
# ISO-8601 duration subset: PnDTnHnMnS (supports fractional seconds)
_DUR_RE = _re.compile(
  r"^P"
  r"(?:(?P<days>\d+)D)?"
  r"(?:T"
  r"(?:(?P<hours>\d+)H)?"
  r"(?:(?P<minutes>\d+)M)?"
  r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
  r")?$"
)

# Basic ISO datetime with optional fractional seconds and optional Z
_DT_RE = _re.compile(
  r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"
  r"T(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"
  r"(?:\.(?P<frac>\d+))?"
  r"(?P<z>Z)?$"
)


def _parse_duration_iso8601(s: str) -> Optional[_dt.timedelta]:
  m = _DUR_RE.match(s)
  if not m:
    return None
  days = int(m.group("days") or 0)
  hours = int(m.group("hours") or 0)
  minutes = int(m.group("minutes") or 0)
  sec_str = m.group("seconds") or "0"
  seconds = float(sec_str) if sec_str else 0.0
  return _dt.timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def _format_duration_iso8601(td: _dt.timedelta) -> str:
  total_seconds = td.total_seconds()
  if total_seconds < 0:
    raise ValueError("negative timedeltas not supported for ISO-8601 formatting here")
  days = int(total_seconds // 86400)
  rem = total_seconds - days * 86400
  hours = int(rem // 3600)
  rem -= hours * 3600
  minutes = int(rem // 60)
  seconds = rem - minutes * 60

  out = "P"
  if days:
    out += f"{days}D"
  if hours or minutes or seconds or not days:
    out += "T"
    if hours:
      out += f"{hours}H"
    if minutes:
      out += f"{minutes}M"
    # keep seconds even if 0 when nothing else was emitted in T
    if seconds or (not hours and not minutes):
      # avoid trailing .0 when it’s integral
      if abs(seconds - round(seconds)) < 1e-12:
        out += f"{int(round(seconds))}S"
      else:
        out += f"{seconds}S"
  return out


def _parse_scalar(text: Optional[str]) -> Any:
  if text is None:
    return ""
  s = text.strip()
  if s == "":
    return ""

  if s in ("true", "false"):
    return s == "true"

  dur = _parse_duration_iso8601(s)
  if dur is not None and s.startswith("P"):
    return dur

  dm = _DT_RE.match(s)
  if dm:
    y, mo, d = int(dm["y"]), int(dm["m"]), int(dm["d"])
    h, mi, se = int(dm["h"]), int(dm["mi"]), int(dm["s"])
    frac = dm["frac"]
    micro = int((frac or "0").ljust(6, "0")[:6])
    dt = _dt.datetime(y, mo, d, h, mi, se, microsecond=micro)
    if dm["z"] == "Z":
      dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt

  if _INT_RE.match(s):
    try:
      return int(s)
    except ValueError:
      pass

  if _FLOAT_RE.match(s):
    try:
      return float(s)
    except ValueError:
      pass

  return s


def _format_scalar(value: Any) -> str:
  if isinstance(value, bool):
    return "true" if value else "false"
  if isinstance(value, int):
    return str(value)
  if isinstance(value, float):
    return repr(value)
  if isinstance(value, _dt.timedelta):
    return _format_duration_iso8601(value)
  if isinstance(value, _dt.datetime):
    # if timezone-aware UTC, emit Z
    if value.tzinfo is not None and value.utcoffset() == _dt.timedelta(0):
      v = value.astimezone(_dt.timezone.utc).replace(tzinfo=None)
      return v.isoformat(timespec="seconds") + "Z"
    return value.isoformat()
  return str(value)


def _localname(tag: str) -> str:
  # "{uri}Name" -> "Name"
  return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _is_xsi_nil(el: ET.Element) -> bool:
  nil_attr = el.attrib.get(f"{{{XSI}}}nil")
  return (nil_attr or "").lower() == "true"


# --------- generic XML -> Python structure ---------


def _xml_to_obj(el: ET.Element) -> dict[str, Any]:
  if _is_xsi_nil(el):
    return None

  children = list(el)
  if not children:
    return _parse_scalar(el.text)

  # group by localname
  grouped: dict[str, list[ET.Element]] = {}
  for ch in children:
    grouped.setdefault(_localname(ch.tag), []).append(ch)

  out: dict[str, Any] = {}
  for name, items in grouped.items():
    if len(items) == 1:
      out[name] = _xml_to_obj(items[0])
    else:
      out[name] = [_xml_to_obj(i) for i in items]
  return out


# --------- SOAP decode helpers ---------


def soap_body_payload(xml: str) -> ET.Element:
  """
  Returns the first element inside SOAP Body (e.g., GetStatusResponse).
  """
  root = ET.fromstring(xml)
  body = root.find(f".//{{{SOAP_ENV}}}Body")
  if body is None:
    raise ValueError("SOAP Body not found")
  for child in list(body):
    return child
  raise ValueError("SOAP Body is empty")


def soap_decode(xml: str, *, take: str | None = None) -> Any:
  """
  Decode a SOAP response into Python structures.
  - If take is provided, finds the first element with that localname anywhere
    under the SOAP Body payload and returns its decoded value.
  - If take is None, returns the decoded payload element.
  """
  payload = soap_body_payload(xml)

  if take is None:
    return {_localname(payload.tag): _xml_to_obj(payload)}

  for el in payload.iter():
    if _localname(el.tag) == take:
      return _xml_to_obj(el)

  raise KeyError(f"Element {take!r} not found under SOAP Body payload")


# --------- SOAP encode helpers ---------


def _append_value(parent: ET.Element, ns: str, name: str, value: Any) -> None:
  el = ET.SubElement(parent, f"{{{ns}}}{name}")

  if value is None:
    el.set(f"{{{XSI}}}nil", "true")
    return

  if isinstance(value, Mapping):
    for k, v in value.items():
      _append_value(el, ns, str(k), v)
    return

  if isinstance(value, (list, tuple)):
    # repeated child elements with the same name (common in SOAP doc/literal)
    # here we encode as <name><item>...</item></name> by default
    for item in value:
      _append_value(el, ns, "item", item)
    return

  el.text = _format_scalar(value)


def soap_encode(
  method: str,
  params: Mapping[str, Any] | None = None,
  *,
  method_ns: str,
  extra_method_xmlns: Mapping[str, str] | None = None,
) -> str:
  """
  Builds a doc/literal style SOAP 1.1 envelope:
    <s:Envelope><s:Body><Method xmlns="method_ns">...</Method></s:Body></s:Envelope>

  extra_method_xmlns lets you add xmlns:prefix="uri" attributes on the method element.
  """
  env = ET.Element(f"{{{SOAP_ENV}}}Envelope")
  body = ET.SubElement(env, f"{{{SOAP_ENV}}}Body")

  # Method element uses the *default* namespace (no prefix) like your example.
  method_el = ET.SubElement(body, f"{{{method_ns}}}{method}")

  if extra_method_xmlns:
    for prefix, uri in extra_method_xmlns.items():
      method_el.set(f"xmlns:{prefix}", uri)

  for k, v in (params or {}).items():
    _append_value(method_el, method_ns, str(k), v)

  return ET.tostring(env, encoding="unicode", xml_declaration=False)
