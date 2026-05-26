from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Any, Dict, Mapping, Optional, Union

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend
from pylabrobot.capabilities.electroporation.standard import (
  ElectroporationCancellationResult,
  ElectroporationProtocol,
  ElectroporationRunResult,
  PreparedElectroporationRun,
)


class ElectroporationBackend(CapabilityBackend, metaclass=ABCMeta):
  """Minimal backend contract for electroporators.

  The common surface is built around the real lab workflow:
  prepare a temporary protocol before loading the plate, then start or cancel the prepared run.
  Device-specific developer helpers belong on concrete vendor backends.
  """

  @abstractmethod
  async def prepare_temporary_protocol(
    self,
    protocol: ElectroporationProtocol,
    plate_columns: Optional[int] = None,
    prefix: Optional[str] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> PreparedElectroporationRun:
    """Create a temporary protocol and leave the device armed on the pre-run screen."""

  @abstractmethod
  async def start_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
    home_after: bool = True,
    max_run_seconds: float = 420.0,
  ) -> ElectroporationRunResult:
    """Verify and start a previously prepared run."""

  @abstractmethod
  async def cancel_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
    home_after: bool = True,
  ) -> ElectroporationCancellationResult:
    """Cancel a previously prepared run and remove the temporary protocol."""

  @abstractmethod
  async def get_device_info(self) -> Dict[str, Any]:
    """Return device identity and capability information."""
