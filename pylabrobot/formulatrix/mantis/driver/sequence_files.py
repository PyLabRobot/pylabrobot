"""A unit's sequence files (``.seq.txt``): one item per line, its class name first.

Only what moves the arm is read here: each ``MoveSequenceItem`` line becomes a route line, the axes
it names in mm in the files' frame (the arm frame; Z down from its home flag), the others None.
"""

from typing import List, Optional, Tuple

from .errors import MantisConfigurationError

# One line of a route file: x, y and z, each None where the line does not name it, so that axis
# holds; not a pose, which names every axis.
RouteLine = Tuple[Optional[float], Optional[float], Optional[float]]
MOVE_ITEM = "MoveSequenceItem"
# A line that only shows a message; it moves nothing.
REMARK_ITEM = "RemarkSequenceItem"
AXES = ("x", "y", "z")


def _parse_move_line(*, path: str, number: int, tokens: List[str]) -> RouteLine:
  """One move line's axes, from the tokens after its class name.

  A line holds its message, its wait flag, then the axes it moves, each with its value, and a
  delay; it may end in a ``TargetPosition`` and the position's name.

  Raises:
    MantisConfigurationError: if the line is malformed, names one of X and Y alone, moves
      relative to where the head is, does not wait or has a delay other than 0.
  """
  where = f"{path}, line {number}"
  if len(tokens) < 2 or tokens[1].lower() != "true":
    raise MantisConfigurationError(f"{where}: a move that does not wait is not supported")
  axes = {}
  delays = []
  index = 2
  while index < len(tokens):
    token = tokens[index].lower()
    if token in AXES and index + 1 < len(tokens):
      try:
        axes[token] = float(tokens[index + 1])
      except ValueError as error:
        raise MantisConfigurationError(f"{where}: {tokens[index + 1]!r} is no number") from error
      index += 2
    elif token in ("dx", "dy", "dz"):
      raise MantisConfigurationError(f"{where}: a relative move is not supported")
    elif token == "targetposition":
      index += 2  # names the position; it moves nothing
    else:
      try:
        delays.append(int(token))
      except ValueError as error:
        raise MantisConfigurationError(f"{where}: {tokens[index]!r} is not understood") from error
      index += 1
  if any(delay != 0 for delay in delays):
    raise MantisConfigurationError(f"{where}: a delay other than 0 is not supported")
  if ("x" in axes) != ("y" in axes):
    raise MantisConfigurationError(f"{where}: a move names one of X and Y alone")
  return (axes.get("x"), axes.get("y"), axes.get("z"))


def parse_move_sequence(*, path: str) -> List[RouteLine]:
  """The route lines of a sequence file that moves the arm, one per move line, in order.

  Remark lines and blank lines are skipped; a line that names no axis moves nothing and is kept.

  Args:
    path: the ``.seq.txt`` file.

  Raises:
    MantisConfigurationError: if a line is neither a move nor a remark, or a move line is not
      supported (``_parse_move_line``).
  """
  route_lines: List[RouteLine] = []
  # utf-8-sig drops a byte-order mark at the start; one within the file is dropped per line.
  with open(path, encoding="utf-8-sig") as f:
    for number, line in enumerate(f, start=1):
      tokens = line.replace("\ufeff", "").split()
      if not tokens or tokens[0] == REMARK_ITEM:
        continue
      if tokens[0] != MOVE_ITEM:
        raise MantisConfigurationError(
          f"{path}, line {number}: {tokens[0]} does not belong in a sequence that moves the arm"
        )
      route_lines.append(_parse_move_line(path=path, number=number, tokens=tokens[1:]))
  return route_lines
