"""Shared utilities for STAR backend modules.

Contains firmware parsing, decorators, typing base, and re-exports error classes
from STAR_errors.py. Used by both STAR_backend.py and STAR_backend_96head.py.
"""

import datetime
import functools
import re
import sys
from dataclasses import dataclass
from typing import (
  TYPE_CHECKING,
  Any,
  Callable,
  Coroutine,
  Literal,
  Optional,
  TypeVar,
)

if sys.version_info < (3, 10):
  from typing_extensions import Concatenate, ParamSpec
else:
  from typing import Concatenate, ParamSpec

from pylabrobot.liquid_handling.backends.hamilton.star.errors import (  # noqa: F401
  AntiDropControlError,
  AreaAlreadyOccupiedError,
  AspirationError,
  BarcodeAlreadyUsedError,
  BarcodeMaskError,
  BarcodeNotUniqueError,
  BarcodeUnreadableError,
  ClotDetectedError,
  CommandNotCompletedError,
  CommandSyntaxError,
  CoverCloseError,
  DecapperError,
  DecapperHandlingError,
  DelimiterError,
  DispenseWithPressureLLDError,
  ElementLostError,
  ElementStillHoldingError,
  HamiltonNoTipError,
  HardwareError,
  IllegalTargetPlatePositionError,
  IllegalUserAccessError,
  ImpossibleToOccupyAreaError,
  ImproperDispensationError,
  IncubationError,
  KitLotExpiredError,
  LiquidLevelError,
  LoadingTrayError,
  NoCarrierBarcodeError,
  NoCarrierError,
  NoElementError,
  NoLabwareError,
  NoTeachInSignalError,
  NotAllowedParameterCombinationError,
  NotAspiratedError,
  NotCompletedError,
  NotDetectedError,
  PositionNotReachableError,
  STARFirmwareError,
  STARModuleError,
  SequencedAspirationWithPressureLLDError,
  SlaveError,
  StopError,
  TADMMeasurementError,
  TipAlreadyFittedError,
  TipTooLittleVolumeError,
  UnexpectedLLDError,
  UnexpectedLabwareError,
  UnknownHamiltonError,
  WashFluidOrWasteError,
  WrongCarrierError,
  WrongLabwareError,
  _module_id_to_module_name,
  convert_star_firmware_error_to_plr_error,
  convert_star_module_error_to_plr_error,
  error_code_to_exception,
  star_firmware_string_to_error,
  trace_information_to_string,
)

if TYPE_CHECKING:
  pass

_P = ParamSpec("_P")
_R = TypeVar("_R")
_S = TypeVar("_S", bound="STARBaseMixin")


class STARBaseMixin:
  """Typing base for STAR mixins.

  Declares the attributes and methods that STAR mixins (e.g. STARBackend96HeadMixin)
  and decorators (need_iswap_parked, _requires_head96) expect from the host class.
  These are provided by STARBackend / HamiltonLiquidHandler at runtime.

  Uses bare annotations only — no values or method bodies — so nothing shadows
  real implementations in the MRO.
  """

  iswap_installed: Optional[bool]
  core96_head_installed: Optional[bool]
  _channel_traversal_height: float
  _iswap_traversal_height: float
  _head96_information: Optional["Head96Information"]

  deck: property
  iswap_parked: property

  send_command: Callable[..., Coroutine[Any, Any, Any]]
  get_or_assign_tip_type_index: Callable[..., Coroutine[Any, Any, int]]
  _parse_firmware_version_datetime: Callable[[str], datetime.date]
  park_iswap: Callable[..., Coroutine[Any, Any, None]]


def need_iswap_parked(
  method: Callable[Concatenate[_S, _P], Coroutine[Any, Any, _R]],
) -> Callable[Concatenate[_S, _P], Coroutine[Any, Any, _R]]:
  """Ensure that the iSWAP is in parked position before running command.

  If the iSWAP is not parked, it get's parked before running the command.
  """

  @functools.wraps(method)
  async def wrapper(self: _S, *args, **kwargs):
    if self.iswap_installed and not self.iswap_parked:
      await self.park_iswap(
        minimum_traverse_height_at_beginning_of_a_command=int(self._iswap_traversal_height * 10)
      )

    return await method(self, *args, **kwargs)

  return wrapper


def _requires_head96(
  method: Callable[Concatenate[_S, _P], Coroutine[Any, Any, _R]],
) -> Callable[Concatenate[_S, _P], Coroutine[Any, Any, _R]]:
  """Ensure that a 96-head is installed before running the command."""

  @functools.wraps(method)
  async def wrapper(self: _S, *args, **kwargs):
    if not self.core96_head_installed:
      raise RuntimeError(
        "This command requires a 96-head, but none is installed. "
        "Check your instrument configuration."
      )
    return await method(self, *args, **kwargs)

  return wrapper


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


# ============== Shared Helpers ==============


def _dispensing_mode_for_op(empty: bool, jet: bool, blow_out: bool) -> int:
  """from docs:
  0 = Partial volume in jet mode
  1 = Blow out in jet mode, called "empty" in the VENUS liquid editor
  2 = Partial volume at surface
  3 = Blow out at surface, called "empty" in the VENUS liquid editor
  4 = Empty tip at fix position
  """

  if empty:
    return 4
  if jet:
    return 1 if blow_out else 0
  else:
    return 3 if blow_out else 2


@dataclass
class Head96Information:
  """Information about the installed 96-head."""

  StopDiscType = Literal["core_i", "core_ii"]
  InstrumentType = Literal["legacy", "FM-STAR"]
  HeadType = Literal["Low volume head", "High volume head", "96 head II", "96 head TADM", "unknown"]

  fw_version: datetime.date
  supports_clot_monitoring_clld: bool
  stop_disc_type: StopDiscType
  instrument_type: InstrumentType
  head_type: HeadType
