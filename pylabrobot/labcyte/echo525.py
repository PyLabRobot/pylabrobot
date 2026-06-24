"""Labcyte Echo 525 acoustic liquid handler support.

The Echo 525 speaks the exact same Medman SOAP-over-HTTP protocol as the Echo 650
(``POST /Medman``, gzip-compressed SOAP envelopes, identical RPC method names). This
module therefore reuses :class:`~pylabrobot.labcyte.echo.EchoDriver` and
:class:`~pylabrobot.labcyte.echo.Echo` wholesale and only overrides the handful of
device-specific defaults that differ on the 525.

The defaults below were reverse-engineered from a Wireshark capture of an Echo 525
(``Model`` reported as ``Echo 525``, software ``2.7.3``) running a HiFi PCR protocol.
Key differences from the Echo 650:

* **Transfer volume granularity is 25 nL** (the Echo 650 dispenses in 2.5 nL droplets).
  The instrument confirmed this over the wire: ``GetTransferVolIncrNl`` and
  ``GetTransferVolMinimumNl`` both returned ``25``. Requested volumes must be whole
  multiples of 25 nL.
* The captured instrument advertised ``Protocol: 2.6`` / ``Client: 2.7.3`` in its HTTP
  headers, so those are the default version strings here (the 650 backend defaults to the
  newer 3.1 protocol). Override ``protocol_version`` / ``client_version`` if your 525 runs
  newer Echo software.

Everything else - the access-control RPCs, plate survey, ``DoWellTransfer`` ``<wp>``
protocol XML, gripper retract/present, dry-plate, sessions and locking - is inherited
unchanged from the Echo 650 implementation.
"""

from __future__ import annotations

from typing import Optional

from pylabrobot.labcyte.echo import (
  DEFAULT_EVENT_PORT,
  DEFAULT_RPC_PORT,
  DEFAULT_SLOT_A,
  DEFAULT_SLOT_B,
  DEFAULT_TIMEOUT,
  Echo,
  EchoDriver,
)

#: Droplet / transfer volume increment of the Echo 525 (nL). Confirmed by the device's
#: ``GetTransferVolIncrNl`` and ``GetTransferVolMinimumNl`` responses.
ECHO_525_TRANSFER_VOLUME_INCREMENT_NL = 25.0

#: Version strings observed in the Echo 525's Medman HTTP headers.
ECHO_525_CLIENT_VERSION = "2.7.3"
ECHO_525_PROTOCOL_VERSION = "2.6"

#: ``Model`` string the Echo 525 reports from ``GetInstrumentInfo``.
ECHO_525_MODEL_NAME = "Echo 525"


class Echo525Driver(EchoDriver):
  """Driver for the Labcyte Echo 525.

  Identical to :class:`~pylabrobot.labcyte.echo.EchoDriver` except for 525-specific
  defaults (25 nL volume increment and the 2.6/2.7.3 protocol/client versions observed
  on real hardware). All keyword arguments remain overridable.
  """

  def __init__(
    self,
    host: str,
    rpc_port: int = DEFAULT_RPC_PORT,
    event_port: int = DEFAULT_EVENT_PORT,
    timeout: float = DEFAULT_TIMEOUT,
    app_name: str = "PyLabRobot Echo 525",
    owner: Optional[str] = None,
    token: Optional[str] = None,
    token_slot_a: int = DEFAULT_SLOT_A,
    token_slot_b: int = DEFAULT_SLOT_B,
    client_version: str = ECHO_525_CLIENT_VERSION,
    protocol_version: str = ECHO_525_PROTOCOL_VERSION,
    transfer_volume_increment_nl: float = ECHO_525_TRANSFER_VOLUME_INCREMENT_NL,
  ):
    super().__init__(
      host=host,
      rpc_port=rpc_port,
      event_port=event_port,
      timeout=timeout,
      app_name=app_name,
      owner=owner,
      token=token,
      token_slot_a=token_slot_a,
      token_slot_b=token_slot_b,
      client_version=client_version,
      protocol_version=protocol_version,
      transfer_volume_increment_nl=transfer_volume_increment_nl,
    )


class Echo525(Echo):
  """Labcyte Echo 525 device frontend.

  Drop-in replacement for :class:`~pylabrobot.labcyte.echo.Echo` that wires up the
  :class:`Echo525Driver` (25 nL transfer increment, 525 protocol defaults).

  Example:
    >>> echo = Echo525(host="192.168.0.25")
    >>> await echo.setup()
    >>> async with echo.plate_access:  # lock + safe door handling
    ...   await echo.driver.transfer([(source_well, dest_well, 150)])  # 150 nL, a multiple of 25
  """

  driver_class = Echo525Driver
  model_name = ECHO_525_MODEL_NAME
  driver: Echo525Driver
