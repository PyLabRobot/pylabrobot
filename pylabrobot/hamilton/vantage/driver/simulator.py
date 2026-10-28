"""A simulated Vantage, which answers where a real one would be written to."""

import re
from typing import Callable, Dict, List, Optional, Tuple

from pylabrobot.hamilton.vantage.driver.errors import check_fw_string_error
from pylabrobot.hamilton.vantage.driver.master import VantageDriver
from pylabrobot.io.io import IOBase
from pylabrobot.resources.hamilton.vantage_decks import VantageDeck

NODES_WITHOUT_IPG = "AM 01 02 03 04 05 06 PX PX PX PX PX PX PX PX 16 17"
NODES_WITH_IPG = "AM 01 02 03 04 05 06 R0 PX PX PX PX PX PX PX PX 16 17"

# The arm types a simulated machine reports: one with an IPG, and one without.
ARM_TYPE_WITH_IPG = 6
ARM_TYPE_WITHOUT_IPG = 13

_COMMAND = re.compile(r"^(\w{4})(\w{2})(?:id(\d+))?")
_PARAMETER = re.compile(r"([a-z]{2})([^a-z]*)")


def parameters(command: str) -> Dict[str, str]:
  """The parameters a command carries, by their two-letter key, as they were written."""
  found = _COMMAND.match(command)
  assert found is not None, command
  return dict(_PARAMETER.findall(command[found.end() :]))


def channel_pattern(value: str, num_channels: int) -> List[bool]:
  """A tip pattern as the command writes it, one entry per channel of the machine.

  A `&` after the last value means "that value again for every channel that is left", so it is
  expanded rather than dropped.
  """
  repeated = value.rstrip().endswith("&")
  digits = value.replace("&", " ").split()
  if repeated and digits:
    digits += [digits[-1]] * (num_channels - len(digits))
  return [digit == "1" for digit in digits[:num_channels]]


class _UnusedTransport(IOBase):
  """Stands where the transport would be. Nothing should reach it."""

  async def setup(self, *args, **kwargs):
    pass

  async def stop(self):
    pass

  async def write(self, data: bytes, *args, **kwargs):
    raise RuntimeError(f"the simulator tried to write to a transport: {data!r}")

  async def read(self, *args, **kwargs) -> bytes:
    raise RuntimeError("the simulator tried to read from a transport")


class VantageSimulationDriver(VantageDriver):
  """A simulated Vantage, driven exactly like a real one.

  The driver assembles each command, emits its events and parses the reply as it does for a
  machine. Only the link is replaced: a command is answered from a small model of the machine,
  which knows what a machine that has just been switched on knows. It answers the commands the
  driver sends, and reports anything else as an unknown command.

  Like the machine, it refuses to initialize or park the IPG, or to initialize the channels, until
  the arm has been initialized, and answers error 16.
  """

  def __init__(
    self,
    deck: Optional[VantageDeck] = None,
    num_channels: int = 8,
    tips_mounted: Optional[List[bool]] = None,
    ipg_installed: bool = True,
    initialized: bool = False,
    ipg_plate: bool = False,
  ):
    """
    Args:
      deck: the deck to reflect the machine into. Optional, as for the driver.
      num_channels: how many pipetting channels the machine has.
      tips_mounted: one entry per channel, `True` where a tip sits on the channel. Defaults to no
        tips on any of them.
      ipg_installed: whether the machine has an internal plate gripper.
      initialized: whether the arm, the channels and the IPG report themselves already
        initialized, and the IPG parked. A machine that has just been switched on does not.
      ipg_plate: whether the IPG holds a plate.

    Raises:
      ValueError: If `num_channels` is not positive, `tips_mounted` does not have one entry for
        each channel, or `ipg_plate` is given for a machine without an IPG.
    """
    if num_channels < 1:
      raise ValueError(f"a machine has at least one channel, not {num_channels}")
    if tips_mounted is not None and len(tips_mounted) != num_channels:
      raise ValueError(
        f"tips_mounted must have one entry for each of the {num_channels} channels, "
        f"but has {len(tips_mounted)}"
      )
    if ipg_plate and not ipg_installed:
      raise ValueError("a machine without an IPG cannot have a plate in it")
    super().__init__(io=_UnusedTransport(), deck=deck)

    self.has_ipg = ipg_installed
    self.tips: List[bool] = list(tips_mounted) if tips_mounted else [False] * num_channels
    self.arm_up = initialized
    self.channels_up = initialized
    self.ipg_up = initialized
    self.ipg_parked = initialized
    self.ipg_plate = ipg_plate

    self.commands: List[str] = []
    """Every command the driver sent, as it would have gone on the wire."""

  async def _open(self):
    """Nothing is opened: there is no link, and no reader to start."""

  async def _close(self):
    """Nothing to close."""

  async def _transmit(
    self,
    cmd: str,
    id_: Optional[int],
    write_timeout: Optional[int],
    read_timeout: Optional[int],
    wait: bool = True,
  ) -> Optional[str]:
    """Answer a command as the machine would, and raise as the driver does for a reported error.

    A command that is not waited for is recorded, and acts on the machine, without an answer being
    returned.
    """
    self.commands.append(cmd)
    found = _COMMAND.match(cmd)
    if found is None:
      raise ValueError(f"not a command: {cmd!r}")
    address, code, written_id = found.groups()
    reply_id = int(written_id) if written_id is not None else 0

    handler = self._handlers().get(address + code)
    if handler is None:
      reply = f'{address}{code}id{reply_id}er30et"Command: Unknown command"'
    else:
      error, written = handler(cmd)
      reply = f"{address}{code}id{reply_id}er{error}{written}"
    check_fw_string_error(reply)
    return reply if wait else None

  def _handlers(self) -> Dict[str, Callable[[str], Tuple[int, str]]]:
    """The commands the machine answers, by module and command, each giving the error code and the
    parameters of its reply.

    A machine without an IPG answers no `A1RM` command, as an unknown one. That is an assumption:
    what a real machine without an IPG answers has not been observed.
    """
    handlers: Dict[str, Callable[[str], Tuple[int, str]]] = {
      "A1AMRF": lambda command: (0, 'rf"simulated"'),
      "A1AMQW": lambda command: (0, f"qw{int(self.arm_up)}"),
      "A1AMQU": self._arm_type,
      "A1AMQN": self._node_names,
      "A1AMMI": self._initialize_arm,
      "A1AMTT": lambda command: (0, ""),
      "A1PMQA": lambda command: (
        0,
        "rt" + " ".join(str(int(tip)) for tip in self.tips),
      ),
      "A1PMQW": lambda command: (0, f"qw{int(self.channels_up)}"),
      "A1PMDI": self._initialize_channels,
      "A1PMTP": self._pick_up_tips,
      "A1PMTR": self._drop_tips,
    }
    if self.has_ipg:
      handlers.update(
        {
          "A1RMQW": lambda command: (0, f"qw{int(self.ipg_up)}"),
          "A1RMRG": lambda command: (0, f"rg{int(self.ipg_parked)}"),
          "A1RMQA": lambda command: (0, f"rt{int(self.ipg_plate)}"),
          "A1RMRR": lambda command: (
            0,
            "rr0 272" if self.ipg_parked else "rr45 271",
          ),
          "A1RMRS": lambda command: (0, "rs6300 2800 700rr0 272"),
          "A1RMDI": self._initialize_ipg,
          "A1RMGP": self._park_ipg,
        }
      )
    return handlers

  def _arm_type(self, command: str) -> Tuple[int, str]:
    kind = ARM_TYPE_WITH_IPG if self.has_ipg else ARM_TYPE_WITHOUT_IPG
    return 0, f"kt{kind}ka1300kd1300ke0kh0"

  def _node_names(self, command: str) -> Tuple[int, str]:
    nodes = NODES_WITH_IPG if self.has_ipg else NODES_WITHOUT_IPG
    return 0, f'xn"{nodes}"'

  def _initialize_arm(self, command: str) -> Tuple[int, str]:
    self.arm_up = True
    return 0, ""

  def _initialize_channels(self, command: str) -> Tuple[int, str]:
    if not self.arm_up:
      return 16, ""
    self.channels_up = True
    self.tips = [False] * len(self.tips)
    return 0, ""

  def _pick_up_tips(self, command: str) -> Tuple[int, str]:
    pattern = channel_pattern(parameters(command)["tm"], len(self.tips))
    for channel, takes in enumerate(pattern):
      if takes:
        self.tips[channel] = True
    return 0, ""

  def _drop_tips(self, command: str) -> Tuple[int, str]:
    pattern = channel_pattern(parameters(command)["tm"], len(self.tips))
    for channel, drops in enumerate(pattern):
      if drops:
        self.tips[channel] = False
    return 0, ""

  def _initialize_ipg(self, command: str) -> Tuple[int, str]:
    if not self.arm_up:
      return 16, ""
    self.ipg_up = True
    return 0, ""

  def _park_ipg(self, command: str) -> Tuple[int, str]:
    if not self.arm_up:
      return 16, ""
    self.ipg_parked = True
    return 0, ""
