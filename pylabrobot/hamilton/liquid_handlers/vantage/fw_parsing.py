"""Vantage-specific firmware response parsing."""

import re
from typing import Dict, Optional


def parse_vantage_fw_string(s: str, fmt: Optional[Dict[str, str]] = None) -> dict:
  """Parse a Vantage firmware string into a dict.

  The identifier parameter (id<int>) is added automatically.

  ``fmt`` is a dict that specifies the format of the string. The keys are the parameter names and
  the values are the types. The following types are supported:

    - ``"int"``: a single integer
    - ``"str"``: a string
    - ``"[int]"``: a list of integers
    - ``"hex"``: a hexadecimal number

  Example:
    >>> parse_vantage_fw_string("id0xs30 -100 +1 1000", {"id": "int", "x": "[int]"})
    {"id": 0, "x": [30, -100, 1, 1000]}

    >>> parse_vantage_fw_string('es"error string"', {"es": "str"})
    {"es": "error string"}
  """

  parsed: dict = {}

  if fmt is None:
    fmt = {}

  if not isinstance(fmt, dict):
    raise TypeError(f"invalid fmt for fmt: expected dict, got {type(fmt)}")

  if "id" not in fmt:
    fmt["id"] = "int"

  for key, data_type in fmt.items():
    if data_type == "int":
      matches = re.findall(rf"{key}([-+]?\d+)", s)
      if len(matches) != 1:
        raise ValueError(f"Expected exactly one match for {key} in {s}")
      parsed[key] = int(matches[0])
    elif data_type == "str":
      matches = re.findall(rf"{key}\"(.*)\"", s)
      if len(matches) != 1:
        raise ValueError(f"Expected exactly one match for {key} in {s}")
      parsed[key] = matches[0]
    elif data_type == "[int]":
      matches = re.findall(rf"{key}((?:[-+]?[\d ]+)+)", s)
      if len(matches) != 1:
        raise ValueError(f"Expected exactly one match for {key} in {s}")
      parsed[key] = [int(x) for x in matches[0].split()]
    elif data_type == "hex":
      matches = re.findall(rf"{key}([0-9a-fA-F]+)", s)
      if len(matches) != 1:
        raise ValueError(f"Expected exactly one match for {key} in {s}")
      parsed[key] = int(matches[0], 16)
    else:
      raise ValueError(f"Unknown data type {data_type}")

  return parsed
