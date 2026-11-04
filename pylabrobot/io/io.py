from abc import ABC, abstractmethod
from typing import Any

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.serializer import SerializableMixin


class IOBase(SerializableMixin, HasLifespan, ABC):
  @abstractmethod
  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
  ) -> None:
    """Require transport acquisition while allowing cooperative parent entry."""
    await super()._enter_lifespan(stack)

  @abstractmethod
  async def write(self, data: bytes, *args: Any, **kwargs: Any) -> Any:
    """Send data, allowing cancellation before dispatch but joining a dispatched write.

    Once dispatch begins, cancellation cannot leave the transfer running after
    the caller exits. A transport failure or write timeout leaves delivery
    uncertain. Transports retain their established return types and report
    short writes as errors.
    """

  @abstractmethod
  async def read(self, *args: Any, **kwargs: Any) -> bytes:
    """Wait for and return one or more bytes, up to the requested positive size.

    A read is boundary-agnostic: it does not wait for a full buffer, report,
    packet, or quiet gap. Transport-specific boundary APIs must be used when
    the caller needs report or packet framing. EOF raises
    ``anyio.EndOfStream``; cancellation retains any unreturned bytes for the
    next read unless input is explicitly purged or the connection is closed or
    replaced. Public read deadlines belong to the caller; explicit legacy
    timeout arguments remain honored but emit ``DeprecationWarning``.
    """

  def serialize(self):
    return {}


class ContinuousConnectionIO(IOBase, ABC):
  """Preserve connection identity between explicit connection-changing API calls.

  Ordinary I/O never silently replaces the connection. An observed hard disconnect
  invalidates further I/O until a documented operation restores it or a new lifespan
  begins. A failed or cancelled connection-changing call does not restore continuity.

  This promise assumes the driver exclusively controls the transport's public API.
  It does not promise that the device stayed online between observations, that all
  device events were received (for example, after a buffer purge), or that the
  connection confers exclusive ownership or knowledge of device state.
  """


class ReconnectableConnectionIO(ContinuousConnectionIO, ABC):
  """Offer explicit connection replacement within the owning lifespan.

  `reconnect()` ends the previous continuity interval even when it fails or is
  cancelled. Further I/O then requires a successful reconnect or a new lifespan.
  """

  @abstractmethod
  async def reconnect(self) -> None:
    """Discard the old connection and establish a replacement."""
