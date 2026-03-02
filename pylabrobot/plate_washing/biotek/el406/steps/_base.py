"""Base mixin providing type stubs for EL406 step sub-mixins.

Sub-mixins inherit from this class so they can reference
``self._send_step_command`` and ``self.timeout`` without circular imports.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from pylabrobot.resources import Plate


class EL406StepsBaseMixin:
  """Type stubs consumed by the per-subsystem step mixins."""

  timeout: float

  if TYPE_CHECKING:

    async def _send_step_command(
      self,
      framed_message: bytes,
      timeout: float | None = None,
    ) -> bytes: ...

    def batch(
      self,
      plate: Plate = ...,
    ) -> AbstractAsyncContextManager[None]: ...
