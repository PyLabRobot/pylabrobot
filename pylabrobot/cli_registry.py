"""Device CLI registrations, loaded only after a device is selected."""

from dataclasses import dataclass
from importlib import import_module
from typing import Mapping, Optional, Protocol, Sequence, cast


class DeviceCLIHandler(Protocol):
  """Interface implemented by each device's CLI module."""

  def main(self, argv: Optional[Sequence[str]] = None) -> None:
    """Parse device arguments and execute the selected command."""
    ...


@dataclass(frozen=True)
class DeviceCLI:
  """Describe a device's CLI without importing its drivers or optional dependencies."""

  module: str
  description: str

  def load(self) -> DeviceCLIHandler:
    """Import the registered module, which exposes a main(argv) function."""
    return cast(DeviceCLIHandler, import_module(self.module))


DEVICE_CLIS: Mapping[str, DeviceCLI] = {
  "celigo": DeviceCLI("pylabrobot.revvity.celigo.cli", "Celigo gRPC service and inspection tools"),
}
