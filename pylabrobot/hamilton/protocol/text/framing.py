"""Hamilton firmware reply parsing.

A reply repeats the module and command it answers, then carries fields whose names are two
characters followed by their value: `id####` for the identifier a command was sent with, and one
per parameter the machine reports.

This grammar is shared across Hamilton machines and across transports - the same shape comes back
from a STAR over USB and from a tilt module over a serial line - so nothing here is specific to
either. What a given value *means* belongs with the machine that reports it.
"""

import datetime
import re
from typing import Any, Dict, List, Optional, Sequence, TypeVar

T = TypeVar("T")

# Error fields that mean "no error".
_NO_ERROR_FIELDS = ("00", "00/00")


def parse_fw_string(resp: str, fmt: str = "") -> dict:
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


def parse_firmware_version_date(fw_version: str) -> datetime.date:
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
  date_match = re.search(r"\b(20\d{2})[._-](\d{2})[._-](\d{2})\b", fw_version)
  if date_match:
    y, m, d = map(int, date_match.groups())
    return datetime.date(y, m, d)

  # Handle quarter formats like 2023_Q2 -> first day of the quarter
  q_match = re.search(r"\b(20\d{2})_Q([1-4])\b", fw_version, flags=re.IGNORECASE)
  if q_match:
    y = int(q_match.group(1))
    q = int(q_match.group(2))
    month = (q - 1) * 3 + 1
    return datetime.date(y, month, 1)

  # Fall back to year only -> Jan 1st of that year
  year_match = re.search(r"\b(20\d{2})\b", fw_version)
  if year_match is None:
    raise ValueError(f"Could not parse year from firmware version string: '{fw_version}'")
  return datetime.date(int(year_match.group(1)), 1, 1)


def to_list(val: List[T], tip_pattern: List[bool]) -> List[T]:
  """Convert a list of values to a list of values with the correct length.

  This is roughly one-hot encoding. STAR expects a value for a list parameter at the position
  for the corresponding channel. If `tip_pattern` is False, there, the value itself is ignored,
  but it must be present.

  Args:
    val: A list of values, exactly one for each channel that is involved in the operation.
    tip_pattern: A list of booleans indicating whether a channel is involved in the operation.

  Returns:
    A list of values with the correct length. Each value that is not involved in the operation
    is set to the first value in `val`, which is ignored by STAR.
  """

  # use the default value if a channel is not involved, otherwise use the value in val
  if len(val) == 0:
    raise ValueError("val must not be empty")
  if len(val) > len(tip_pattern):
    raise ValueError(f"val has more entries ({len(val)}) than tip_pattern ({len(tip_pattern)})")

  result: List[T] = []
  arg_index = 0
  for channel_involved in tip_pattern:
    if channel_involved:
      if arg_index >= len(val):
        raise ValueError(f"Too few values for tip pattern {tip_pattern}: {val}")
      result.append(val[arg_index])
      arg_index += 1
    else:
      # this value will be ignored, so just use a value we know is valid
      result.append(val[0])
  if arg_index < len(val):
    raise ValueError(f"Too many values for tip pattern {tip_pattern}: {val}")
  return result


def assemble_command(
  module: str,
  command: str,
  id_: Optional[int] = None,
  **kwargs,
) -> str:
  """Assemble a firmware command.

  Args:
    module: 2 character module identifier (C0 for master, ...)
    command: 2 character command identifier (QM for request status, ...)
    id_: The command id, written as `id####` immediately after the command. Omitted when None.
    kwargs: any named parameters. The parameter name should also be 2 characters long. The value
      can be any size. A trailing underscore is stripped, so reserved words can be parameters.

  Returns:
    The assembled command string.

  Raises:
    ValueError: If a keyword argument is not 2 characters long.
  """
  cmd = module + command
  if id_ is not None:
    cmd += f"id{id_:04}"  # id has to be the first param

  for k, v in kwargs.items():
    if isinstance(v, datetime.datetime):
      v = v.strftime("%Y-%m-%d %h:%M")
    elif isinstance(v, bool):
      v = 1 if v else 0
    elif isinstance(v, list):
      v = " ".join(str(e) for e in v)
    if k.endswith("_"):
      k = k[:-1]
    if len(k) != 2:
      raise ValueError("Keyword arguments should be 2 characters long, but got: " + k)
    cmd += f"{k}{v}"

  return cmd


def encode_channel_list(
  values: List[Any],
  tip_pattern: List[bool],
  num_channels: int,
) -> str:
  """Encode one parameter that carries a value per channel.

  Args:
    values: One value per involved channel, or one per channel of the machine.
    tip_pattern: Which channels are involved.
    num_channels: The machine's channel count.

  Returns:
    The values separated by spaces, terminated with `&` when they stop short of the machine.
  """
  if len(values) != len(tip_pattern):
    values = to_list(values, tip_pattern)
  if isinstance(values[0], bool):
    values = [int(x) for x in values]
  return " ".join(str(v) for v in values) + ("&" if len(values) < num_channels else "")


def assemble_channel_command(
  module: str,
  command: str,
  tip_pattern: Optional[List[bool]],
  num_channels: int,
  id_: Optional[int] = None,
  **kwargs,
) -> str:
  """Assemble a firmware command whose list parameters carry a value per channel.

  Args:
    module: 2 character module identifier.
    command: 2 character command identifier.
    tip_pattern: Which channels are involved, used to expand a list given per involved channel.
      When None, a list is taken to already hold one value per channel.
    num_channels: The machine's channel count.
    id_: The command id. Omitted when None.
    kwargs: any named parameters.

  Returns:
    The assembled command string.
  """
  encoded: Dict[str, Any] = {
    k: encode_channel_list(v, tip_pattern or [True] * len(v), num_channels)
    if isinstance(v, list)
    else v
    for k, v in kwargs.items()
  }
  return assemble_command(module, command, id_=id_, **encoded)


def find_error_fields(
  resp: str,
  module_id_length: int,
  master_module_id: str,
  other_module_ids: Sequence[str],
) -> Dict[str, str]:
  """Find the error field each module reported in a reply.

  The master reports `er<code>/<trace>` and may append one field per failing module. Any other
  module, addressed directly, reports `er<trace>` and never carries nested fields. Fields meaning
  "no error" are dropped, so an empty result means the reply is clean.

  Args:
    resp: The reply as received from the machine.
    module_id_length: Number of characters in a module identifier.
    master_module_id: The identifier of the master module, e.g. `"C0"`.
    other_module_ids: Every module the master may report alongside itself, in the order it lists
      them.

  Returns:
    The error field of each failing module, keyed by module identifier. Empty if none failed.
  """
  module = resp[:module_id_length]

  if module == master_module_id:
    exp = rf"er(?P<{master_module_id}>[0-9]{{2}}/[0-9]{{2}})"
    for other in other_module_ids:
      exp += f" ?(?:{other}(?P<{other}>[0-9]{{2}}/[0-9]{{2}}))?"
  else:
    exp = f"er(?P<{module}>[0-9]{{2}})"

  match = re.search(exp, resp)
  if match is None:
    return {}

  return {
    module_id: field
    for module_id, field in match.groupdict().items()
    if field is not None and field not in _NO_ERROR_FIELDS
  }


def read_id(command: str) -> Optional[int]:
  """Read the `id####` a raw command string carries, if any.

  Args:
    command: A fully assembled command string.

  Returns:
    Its id, or None if it carries none.

  Raises:
    ValueError: If an `id` marker is present but not followed by 4 digits.
  """
  id_index = command.find("id")
  if id_index == -1:
    return None

  id_str = command[id_index + 2 : id_index + 6]
  if not id_str.isdigit():
    raise ValueError("Id must be a 4 digit int.")
  return int(id_str)
