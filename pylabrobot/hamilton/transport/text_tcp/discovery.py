"""Finding Hamilton machines on the network, and turning the name of one into its address.

A machine that speaks the text protocol over TCP answers a UDP broadcast with a line of
`bdc_<key> <value>` pairs that says who it is. `discover_instruments` sends that broadcast and
reads the answers, and `resolve_host` uses them to let a user connect by the name of a machine
instead of its IP address.

The broadcast goes out through a plain UDP socket from this module, not through a
`pylabrobot.io` class. Discovery is one datagram out and a handful back, not a connection.
"""

import asyncio
import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

BROADCAST_PORT = 34569
BROADCAST_MESSAGE = b"HAMILTON_BROADCAST bdc_headerid 2271560481 \r\n"

# Seconds to wait for answers. A machine on a busy or slow network can take longer than a fraction
# of a second to answer, so the wait is not shorter than this.
DEFAULT_TIMEOUT = 0.5

# One `bdc_<key> <value>` pair: the value is text in double quotes, or one bare token.
_PAIR = re.compile(r'bdc_(?P<key>\w+)\s+(?:"(?P<quoted>[^"]*)"|(?P<bare>\S+))')

# The keys of a reply that say something about a machine. A datagram with none of them, such as our
# own broadcast heard back on the same network, is not a reply.
_MACHINE_KEYS = ("ipaddress", "systemid", "modulename", "macaddress")


@dataclass(frozen=True)
class HamiltonInstrumentInfo:
  """A Hamilton machine that answered the discovery broadcast.

  Attributes:
    ip_address: Where to connect to it.
    system_id: The machine's system identifier, if it said one.
    module_name: The machine's module name, if it said one.
    module_description: The machine's description of itself, if it said one.
    mac_address: The machine's MAC address, as it wrote it, if it said one. Machines do not agree
      on how to write one.
    raw: Every key of the reply, without its `bdc_` prefix, with its value, quotes removed.
  """

  ip_address: str
  system_id: Optional[str] = None
  module_name: Optional[str] = None
  module_description: Optional[str] = None
  mac_address: Optional[str] = None
  raw: Dict[str, str] = field(default_factory=dict, compare=False, hash=False)

  @property
  def names(self) -> List[str]:
    """The names a user may call this machine by."""
    return [name for name in (self.system_id, self.module_name) if name]


def parse_discovery_reply(data: bytes, sender: str) -> Optional[HamiltonInstrumentInfo]:
  """Read one datagram that may be a machine's answer to the discovery broadcast.

  A reply repeats the broadcast header and then gives `bdc_<key> <value>` pairs separated by
  spaces. A value is either a bare token, as a number is, or text in double quotes, which may
  contain spaces. The order of the keys does not matter.

  Args:
    data: The datagram as received.
    sender: The address it came from, used as the machine's address if the reply does not give a
      valid one.

  Returns:
    What the machine said, or None if the datagram is not a machine's reply.
  """
  text = data.decode("ascii", errors="replace").replace("\x00", " ").strip()

  raw: Dict[str, str] = {}
  for found in _PAIR.finditer(text):
    quoted, bare = found.group("quoted"), found.group("bare")
    raw[found.group("key")] = quoted if quoted is not None else bare

  if not any(key in raw for key in _MACHINE_KEYS):
    return None

  try:
    address = str(ipaddress.IPv4Address(raw.get("ipaddress", "")))
  except ValueError:
    address = sender

  return HamiltonInstrumentInfo(
    ip_address=address,
    system_id=raw.get("systemid") or None,
    module_name=raw.get("modulename") or None,
    module_description=raw.get("moduledesc") or None,
    mac_address=raw.get("macaddress") or None,
    raw=raw,
  )


def _broadcast_targets() -> List[str]:
  """The addresses the broadcast is sent to.

  For each IPv4 address this computer has, the `x.y.z.255` of its network, which assumes a /24,
  and then the limited broadcast address, which leaves by the default route only. Loopback is left
  out.
  """
  try:
    infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
  except socket.gaierror:
    infos = []

  targets: List[str] = []
  for info in infos:
    address = str(info[4][0])
    if ipaddress.IPv4Address(address).is_loopback:
      continue
    target = ".".join(address.split(".")[:3] + ["255"])
    if target not in targets:
      targets.append(target)
  return targets + ["<broadcast>"]


def _exchange(targets: Sequence[str], timeout: float, port: int) -> List[Tuple[bytes, str]]:
  """Send the broadcast to every target and collect the datagrams that come back.

  This blocks for `timeout` seconds, so it runs in an executor.

  Args:
    targets: The addresses to send to.
    timeout: Seconds to keep listening after the last send.
    port: The UDP port the machines listen on.

  Returns:
    Each datagram received, with the address it came from.
  """
  replies: List[Tuple[bytes, str]] = []
  with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    for target in targets:
      try:
        sock.sendto(BROADCAST_MESSAGE, (target, port))
      except OSError as e:
        # A network with no route for a broadcast must not stop the others being tried.
        logger.debug("could not send the discovery broadcast to %s: %s", target, e)

    deadline = time.monotonic() + timeout
    while True:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        break
      sock.settimeout(remaining)
      try:
        data, (sender, _) = sock.recvfrom(65535)
      except OSError:
        break
      replies.append((data, str(sender)))
  return replies


async def discover_instruments(timeout: float = DEFAULT_TIMEOUT) -> List[HamiltonInstrumentInfo]:
  """Find the Hamilton machines on the networks this computer is on.

  The broadcast goes to `x.y.z.255` for each IPv4 address of this computer, assuming a /24, and to
  the limited broadcast address. A machine on a network of another size, or one that answers
  slowly, may be missed: widen `timeout` or connect by IP address.

  Args:
    timeout: Seconds to wait for answers.

  Returns:
    One entry per machine, in the order they answered. A machine that answers on more than one
    network is listed once.
  """
  loop = asyncio.get_running_loop()
  targets = await loop.run_in_executor(None, _broadcast_targets)
  logger.debug("discovery broadcast to %s", targets)
  replies = await loop.run_in_executor(None, _exchange, targets, timeout, BROADCAST_PORT)

  found: Dict[str, HamiltonInstrumentInfo] = {}
  for data, sender in replies:
    info = parse_discovery_reply(data, sender)
    if info is None:
      logger.debug("ignored a datagram from %s that is not a machine's reply: %r", sender, data)
      continue
    found.setdefault(info.ip_address, info)
  return list(found.values())


async def _lookup_dns(name: str) -> str:
  """The IPv4 address the system's resolver gives for a name.

  Raises:
    ValueError: If the name does not resolve.
  """
  loop = asyncio.get_running_loop()
  try:
    infos = await loop.getaddrinfo(name, None, family=socket.AF_INET)
  except socket.gaierror as e:
    raise ValueError(f"{name!r} could not be resolved: {e}") from e
  return str(infos[0][4][0])


async def resolve_host(host: str, timeout: float = DEFAULT_TIMEOUT) -> str:
  """Turn what a user gave to connect to a machine into an IPv4 address.

  - An IPv4 address is returned as it is.
  - `localhost` and names ending in `.local` are resolved by the system.
  - Anything else is taken to be the name of a machine, and is looked for among the machines that
    answer the discovery broadcast, by system identifier or module name, ignoring case.

  Args:
    host: An IPv4 address, a name the system can resolve, or the name of a machine.
    timeout: Seconds to wait for answers to the discovery broadcast.

  Returns:
    The IPv4 address to connect to.

  Raises:
    ValueError: If the name does not resolve, matches no machine, or matches more than one.
  """
  try:
    return str(ipaddress.IPv4Address(host))
  except ValueError:
    pass

  lowered = host.lower()
  if lowered == "localhost" or lowered.endswith(".local"):
    return await _lookup_dns(host)

  instruments = await discover_instruments(timeout)
  matches = [info for info in instruments if lowered in (name.lower() for name in info.names)]
  if len(matches) == 1:
    return matches[0].ip_address
  if matches:
    addresses = ", ".join(info.ip_address for info in matches)
    raise ValueError(f"{host!r} matches more than one machine: {addresses}. Use an IP address.")
  known = ", ".join(f"{name} ({info.ip_address})" for info in instruments for name in info.names)
  raise ValueError(
    f"No machine called {host!r} answered the discovery broadcast. "
    f"Machines that did: {known or 'none'}."
  )
