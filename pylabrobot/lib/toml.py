"""A small, dependency-free TOML parser for Python 3.9+.

This intentionally implements the most useful TOML subset: key/value pairs,
tables, dotted keys, strings, booleans, numbers, date/time values, arrays, and
inline tables.  Arrays of tables and multiline strings are not supported.
"""

import datetime as _datetime
import re
from typing import Any, BinaryIO, Dict, List, TextIO, Union


class TOMLDecodeError(ValueError):
  """Raised when the input is not valid for the supported TOML subset."""


_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_INTEGER = re.compile(
  r"[+-]?(?:0|[1-9](?:_?\d)*)|0x[0-9A-Fa-f](?:_?[0-9A-Fa-f])*|"
  r"0o[0-7](?:_?[0-7])*|0b[01](?:_?[01])*"
)
_FLOAT = re.compile(
  r"[+-]?(?:(?:0|[1-9](?:_?\d)*)\.\d(?:_?\d)*(?:[eE][+-]?\d(?:_?\d)*)?"
  r"|(?:0|[1-9](?:_?\d)*)[eE][+-]?\d(?:_?\d)*|inf|nan)"
)
_DATETIME = re.compile(
  r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
  r"(?:[Zz]|[+-]\d{2}:\d{2})?"
)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME = re.compile(r"\d{2}:\d{2}:\d{2}(?:\.\d+)?")


def _error(message: str, line: int) -> TOMLDecodeError:
  """Build a parse error that includes its source line."""
  return TOMLDecodeError("line {}: {}".format(line, message))


def _strip_comment(line: str) -> str:
  """Remove a comment while preserving hash characters inside strings."""
  quote = ""
  escaped = False
  for index, char in enumerate(line):
    if quote == '"':
      if escaped:
        escaped = False
      elif char == "\\":
        escaped = True
      elif char == quote:
        quote = ""
    elif quote == "'":
      if char == quote:
        quote = ""
    elif char in "\"'":
      quote = char
    elif char == "#":
      return line[:index]
  return line


def _statement_complete(text: str) -> bool:
  """Return whether brackets, braces, and quotes are balanced."""
  quote = ""
  escaped = False
  square = curly = 0
  for char in text:
    if quote == '"':
      if escaped:
        escaped = False
      elif char == "\\":
        escaped = True
      elif char == quote:
        quote = ""
    elif quote == "'":
      if char == quote:
        quote = ""
    elif char in "\"'":
      quote = char
    elif char == "[":
      square += 1
    elif char == "]":
      square -= 1
    elif char == "{":
      curly += 1
    elif char == "}":
      curly -= 1
  return not quote and square <= 0 and curly <= 0


class _ValueParser:
  """Parse a TOML value using a cursor over one logical statement."""

  def __init__(self, text: str):
    """Initialize a parser at the start of ``text``."""
    self.text = text
    self.pos = 0

  def parse(self) -> Any:
    """Parse exactly one value and reject trailing text."""
    value = self._value()
    self._space()
    if self.pos != len(self.text):
      raise ValueError("unexpected text {!r}".format(self.text[self.pos :]))
    return value

  def _space(self) -> None:
    """Advance past whitespace."""
    while self.pos < len(self.text) and self.text[self.pos].isspace():
      self.pos += 1

  def _value(self) -> Any:
    """Parse the value at the current cursor position."""
    self._space()
    if self.pos >= len(self.text):
      raise ValueError("missing value")
    char = self.text[self.pos]
    if char in "\"'":
      return self._string()
    if char == "[":
      return self._array()
    if char == "{":
      return self._inline_table()
    return self._bare_value()

  def _string(self) -> str:
    """Parse a basic or literal string."""
    quote = self.text[self.pos]
    self.pos += 1
    result = []  # type: List[str]
    escapes = {
      "b": "\b",
      "t": "\t",
      "n": "\n",
      "f": "\f",
      "r": "\r",
      '"': '"',
      "\\": "\\",
    }
    while self.pos < len(self.text):
      char = self.text[self.pos]
      self.pos += 1
      if char == quote:
        return "".join(result)
      if quote == '"' and char == "\\":
        if self.pos >= len(self.text):
          break
        escape = self.text[self.pos]
        self.pos += 1
        if escape in escapes:
          result.append(escapes[escape])
        elif escape in ("u", "U"):
          length = 4 if escape == "u" else 8
          digits = self.text[self.pos : self.pos + length]
          if len(digits) != length or not re.fullmatch(r"[0-9A-Fa-f]+", digits):
            raise ValueError("invalid Unicode escape")
          codepoint = int(digits, 16)
          if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError("invalid Unicode code point")
          result.append(chr(codepoint))
          self.pos += length
        else:
          raise ValueError("unknown escape \\{}".format(escape))
      else:
        if char in "\n\r":
          raise ValueError("multiline strings are not supported")
        result.append(char)
    raise ValueError("unterminated string")

  def _array(self) -> List[Any]:
    """Parse an array, including an optional trailing comma."""
    self.pos += 1
    result = []  # type: List[Any]
    self._space()
    if self._take("]"):
      return result
    while True:
      result.append(self._value())
      self._space()
      if self._take("]"):
        return result
      if not self._take(","):
        raise ValueError("expected ',' or ']' in array")
      self._space()
      if self._take("]"):  # Trailing commas are valid in TOML arrays.
        return result

  def _inline_table(self) -> Dict[str, Any]:
    """Parse an inline table."""
    self.pos += 1
    result = {}  # type: Dict[str, Any]
    self._space()
    if self._take("}"):
      return result
    while True:
      equals = _find_unquoted(self.text, "=", self.pos)
      if equals < 0:
        raise ValueError("expected '=' in inline table")
      path = _parse_key(self.text[self.pos : equals].strip())
      self.pos = equals + 1
      _assign(result, path, self._value())
      self._space()
      if self._take("}"):
        return result
      if not self._take(","):
        raise ValueError("expected ',' or '}' in inline table")
      self._space()
      if self.pos < len(self.text) and self.text[self.pos] == "}":
        raise ValueError("inline tables cannot have a trailing comma")

  def _bare_value(self) -> Any:
    """Parse a boolean, number, date, or time."""
    remaining = self.text[self.pos :]
    for pattern, converter in (
      (_DATETIME, _to_datetime),
      (_DATE, _datetime.date.fromisoformat),
      (_TIME, _datetime.time.fromisoformat),
    ):
      match = pattern.match(remaining)
      if match and _value_boundary(remaining, match.end()):
        self.pos += match.end()
        try:
          return converter(match.group(0))
        except ValueError:
          raise ValueError("invalid date or time {!r}".format(match.group(0)))

    end = self.pos
    while end < len(self.text) and not self.text[end].isspace() and self.text[end] not in ",]}":
      end += 1
    token = self.text[self.pos : end]
    self.pos = end
    if token == "true":
      return True
    if token == "false":
      return False
    if _INTEGER.fullmatch(token):
      cleaned = token.replace("_", "")
      sign = -1 if cleaned.startswith("-") else 1
      unsigned = cleaned.lstrip("+-")
      if unsigned.startswith("0x"):
        base = 16
      elif unsigned.startswith("0o"):
        base = 8
      elif unsigned.startswith("0b"):
        base = 2
      else:
        base = 10
      return sign * int(unsigned, base)
    if _FLOAT.fullmatch(token):
      return float(token.replace("_", ""))
    raise ValueError("unsupported or invalid value {!r}".format(token))

  def _take(self, char: str) -> bool:
    """Consume ``char`` if it is at the current cursor position."""
    if self.pos < len(self.text) and self.text[self.pos] == char:
      self.pos += 1
      return True
    return False


def _to_datetime(value: str) -> _datetime.datetime:
  """Convert a TOML date-time literal to ``datetime``."""
  value = value.replace("t", "T")
  if value.endswith(("Z", "z")):
    value = value[:-1] + "+00:00"
  return _datetime.datetime.fromisoformat(value)


def _value_boundary(text: str, pos: int) -> bool:
  """Return whether ``pos`` can end a scalar value."""
  return pos == len(text) or text[pos].isspace() or text[pos] in ",]}"


def _find_unquoted(text: str, wanted: str, start: int = 0) -> int:
  """Find a character outside quoted strings, or return -1."""
  quote = ""
  escaped = False
  for index in range(start, len(text)):
    char = text[index]
    if quote == '"':
      if escaped:
        escaped = False
      elif char == "\\":
        escaped = True
      elif char == quote:
        quote = ""
    elif quote == "'":
      if char == quote:
        quote = ""
    elif char in "\"'":
      quote = char
    elif char == wanted:
      return index
  return -1


def _parse_key(text: str) -> List[str]:
  """Split a bare or quoted dotted key into path components."""
  if not text:
    raise ValueError("empty key")
  parts = []  # type: List[str]
  pos = 0
  while pos < len(text):
    while pos < len(text) and text[pos].isspace():
      pos += 1
    if pos >= len(text):
      raise ValueError("key cannot end with '.'")
    if text[pos] in "\"'":
      parser = _ValueParser(text[pos:])
      part = parser._string()
      pos += parser.pos
    else:
      match = _BARE_KEY.match(text, pos)
      if not match:
        raise ValueError("invalid key {!r}".format(text))
      part = match.group(0)
      pos = match.end()
    parts.append(part)
    while pos < len(text) and text[pos].isspace():
      pos += 1
    if pos == len(text):
      return parts
    if text[pos] != ".":
      raise ValueError("invalid key {!r}".format(text))
    pos += 1
  raise ValueError("key cannot end with '.'")


def _assign(table: Dict[str, Any], path: List[str], value: Any) -> None:
  """Assign a value at a key path without overwriting existing data."""
  target = table
  for part in path[:-1]:
    existing = target.get(part)
    if existing is None:
      existing = {}
      target[part] = existing
    elif not isinstance(existing, dict):
      raise ValueError("key {!r} is already a value".format(part))
    target = existing
  final = path[-1]
  if final in target:
    raise ValueError("duplicate key {!r}".format(final))
  target[final] = value


def _table(root: Dict[str, Any], path: List[str]) -> Dict[str, Any]:
  """Create or retrieve a nested table."""
  target = root
  for part in path:
    existing = target.get(part)
    if existing is None:
      existing = {}
      target[part] = existing
    elif not isinstance(existing, dict):
      raise ValueError("table {!r} conflicts with a value".format(part))
    target = existing
  return target


def loads(text: str) -> Dict[str, Any]:
  """Parse TOML text and return nested dictionaries.

  Args:
    text: TOML text using the subset described in the module docstring.

  Returns:
    The parsed TOML document.

  Raises:
    TOMLDecodeError: The document contains invalid or unsupported TOML.
    TypeError: ``text`` is not a string.
  """
  if not isinstance(text, str):
    raise TypeError("loads() expects str, not {}".format(type(text).__name__))

  root = {}  # type: Dict[str, Any]
  current = root
  declared = set()  # type: set
  statement = ""
  statement_line = 1

  def process(source: str, line: int) -> None:
    """Parse one logical statement into the result document."""
    nonlocal current
    source = source.strip()
    try:
      if source.startswith("[["):
        raise ValueError("arrays of tables are not supported")
      if source.startswith("["):
        if not source.endswith("]"):
          raise ValueError("invalid table header")
        path = _parse_key(source[1:-1].strip())
        path_tuple = tuple(path)
        if path_tuple in declared:
          raise ValueError("table is declared more than once")
        current = _table(root, path)
        declared.add(path_tuple)
        return
      equals = _find_unquoted(source, "=")
      if equals < 0:
        raise ValueError("expected a key/value assignment")
      path = _parse_key(source[:equals].strip())
      value = _ValueParser(source[equals + 1 :]).parse()
      _assign(current, path, value)
    except (ValueError, OverflowError) as exc:
      if isinstance(exc, TOMLDecodeError):
        raise
      raise _error(str(exc), line)

  for line_number, raw_line in enumerate(text.splitlines(), 1):
    clean = _strip_comment(raw_line).strip()
    if not clean:
      continue
    if not statement:
      statement_line = line_number
      statement = clean
    else:
      statement += "\n" + clean
    if _statement_complete(statement):
      process(statement, statement_line)
      statement = ""

  if statement:
    raise _error("unterminated statement", statement_line)
  return root


def load(file: Union[TextIO, BinaryIO]) -> Dict[str, Any]:
  """Parse TOML from an open text or binary file object.

  Args:
    file: An open readable file object.

  Returns:
    The parsed TOML document.

  Raises:
    TOMLDecodeError: The document contains invalid or unsupported TOML.
  """
  data = file.read()
  if isinstance(data, bytes):
    data = data.decode("utf-8")
  return loads(data)


__all__ = ["TOMLDecodeError", "load", "loads"]
