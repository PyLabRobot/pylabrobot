"""Base mixin providing type stubs for EL406 step sub-mixins.

Sub-mixins inherit from this class so they can reference
``self._send_step_command`` and ``self.timeout`` without circular imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..enums import EL406PlateType


class EL406StepsBaseMixin:
  """Type stubs consumed by the per-subsystem step mixins."""

  plate_type: EL406PlateType
  timeout: float

  if TYPE_CHECKING:

    async def _send_step_command(
      self,
      framed_message: bytes,
      timeout: float | None = None,
    ) -> bytes:
      ...
