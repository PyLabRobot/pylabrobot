"""STAR firmware response parsing utilities."""

import datetime
import re


def parse_star_fw_string(resp: str, fmt: str = "") -> dict:
  """Parse a machine command or response string according to a format string.

  The format contains names of parameters (always length 2),
  followed by an arbitrary number of the following, but always
  the same:
  - '&': char
  - '#': decimal
  - '*': hex

  The order of parameters in the format and response string do not
  have to (and often do not) match.

  The identifier parameter (id####) is added automatically.

  TODO: string parsing
  The firmware docs mention strings in the following format: '...'
  However, the length of these is always known (except when reading
  barcodes), so it is easier to convert strings to the right number
  of '&'. With barcode reading the length of the barcode is included
  with the response string. We'll probably do a custom implementation
  for that.

  TODO: spaces
  We should also parse responses where integers are separated by spaces,
  like this: `ua#### #### ###### ###### ###### ######`

  Args:
    resp: The response string to parse.
    fmt: The format string.

  Raises:
    ValueError: if the format string is incompatible with the response.

  Returns:
    A dictionary containing the parsed values.

  Examples:
    Parsing a string containing decimals (`1111`), hex (`0xB0B`) and chars (`'rw'`):

    ```
    >>> parse_fw_string("aa1111bbrwccB0B", "aa####bb&&cc***")
    {'aa': 1111, 'bb': 'rw', 'cc': 2827}
    ```
  """

  # Remove device and cmd identifier from response.
  resp = resp[4:]

  # Parse the parameters in the fmt string.
  info = {}

  def find_param(param):
    name, data = param[0:2], param[2:]
    type_ = {"#": "int", "*": "hex", "&": "str"}[data[0]]

    # Build a regex to match this parameter.
    exp = {
      "int": r"[-+]?[\d ]",
      "hex": r"[\da-fA-F ]",
      "str": ".",
    }[type_]
    len_ = len(data.split(" ")[0])  # Get length of first block.
    regex = f"{name}((?:{exp}{ {len_} }"

    if param.endswith(" (n)"):
      regex += " ?)+)"
      is_list = True
    else:
      regex += "))"
      is_list = False

    # Match response against regex, save results in right datatype.
    r = re.search(regex, resp)
    if r is None:
      raise ValueError(f"could not find matches for parameter {name}")

    g = r.groups()
    if len(g) == 0:
      raise ValueError(f"could not find value for parameter {name}")
    m = g[0]

    if is_list:
      m = m.split(" ")

      if type_ == "str":
        info[name] = m
      elif type_ == "int":
        info[name] = [int(m_) for m_ in m if m_ != ""]
      elif type_ == "hex":
        info[name] = [int(m_, base=16) for m_ in m if m_ != ""]
    else:
      if type_ == "str":
        info[name] = m
      elif type_ == "int":
        info[name] = int(m)
      elif type_ == "hex":
        info[name] = int(m, base=16)

  # Find params in string. All params are identified by 2 lowercase chars.
  param = ""
  prevchar = None
  for char in fmt:
    if char.islower() and prevchar != "(":
      if len(param) > 2:
        find_param(param)
        param = ""
    param += char
    prevchar = char
  if param != "":
    find_param(param)  # last parameter is not closed by loop.

  # If id not in fmt, add it.
  if "id" not in info:
    find_param("id####")

  return info


def parse_star_firmware_version_date(fw_version: str) -> datetime.date:
  """Extract a date from a firmware version string.

  Supports several common Hamilton firmware version formats:
    - Full dates: ``"v2021.03.15"`` or ``"2023_01_05"`` or ``"2020-06-12"``
    - Quarter formats: ``"2023_Q2"`` -> first day of the quarter (2023-04-01)
    - Year only: ``"2021"`` -> January 1st of that year

  Args:
    fw_version: Firmware version string.

  Returns:
    A ``datetime.date`` representing the extracted date.

  Raises:
    ValueError: If no year can be parsed from the string.
  """
  # Prefer full date patterns like YYYY.MM.DD / YYYY_MM_DD / YYYY-MM-DD
  date_match = re.search(r"(20\d{2})[._-](\d{2})[._-](\d{2})", fw_version)
  if date_match:
    y, m, d = map(int, date_match.groups())
    return datetime.date(y, m, d)

  # Handle quarter formats like 2023_Q2 -> first day of the quarter
  q_match = re.search(r"(20\d{2})_Q([1-4])", fw_version, flags=re.IGNORECASE)
  if q_match:
    y = int(q_match.group(1))
    q = int(q_match.group(2))
    month = (q - 1) * 3 + 1
    return datetime.date(y, month, 1)

  # Fall back to year only -> Jan 1st of that year
  year_match = re.search(r"(20\d{2})", fw_version)
  if year_match is None:
    raise ValueError(f"Could not parse year from firmware version string: '{fw_version}'")
  return datetime.date(int(year_match.group(1)), 1, 1)
