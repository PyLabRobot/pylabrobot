from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Union

from pylabrobot.capabilities.capability import BackendParams, Capability, need_capability_ready
from pylabrobot.capabilities.electroporation.backend import ElectroporationBackend
from pylabrobot.capabilities.electroporation.standard import (
  ElectroporationCancellationResult,
  ElectroporationProtocol,
  ElectroporationRunResult,
  PreparedElectroporationRun,
)


class Electroporation(Capability):
  """Electroporation capability.

  This frontend intentionally stays small and exposes the prepared-run workflow shared by
  supported electroporators.
  """

  def __init__(self, backend: ElectroporationBackend):
    super().__init__(backend=backend)
    self.backend: ElectroporationBackend = backend

  @need_capability_ready
  async def prepare_temporary_protocol(
    self,
    protocol: ElectroporationProtocol,
    plate_columns: Optional[int] = None,
    prefix: Optional[str] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> PreparedElectroporationRun:
    return await self.backend.prepare_temporary_protocol(
      protocol=protocol,
      plate_columns=plate_columns,
      prefix=prefix,
      backend_params=backend_params,
    )

  @need_capability_ready
  async def start_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
    home_after: bool = True,
    max_run_seconds: float = 420.0,
  ) -> ElectroporationRunResult:
    return await self.backend.start_prepared_run(
      prepared_run=prepared_run,
      home_after=home_after,
      max_run_seconds=max_run_seconds,
    )

  @need_capability_ready
  async def cancel_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
    home_after: bool = True,
  ) -> ElectroporationCancellationResult:
    return await self.backend.cancel_prepared_run(
      prepared_run=prepared_run,
      home_after=home_after,
    )

  @need_capability_ready
  async def get_device_info(self) -> Dict[str, Any]:
    return await self.backend.get_device_info()
