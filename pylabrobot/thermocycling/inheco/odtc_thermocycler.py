"""ODTC thermocycler resource: subclass that owns connection params and dimensions."""

from __future__ import annotations

from typing import Any, Optional

from pylabrobot.resources import Coordinate, ItemizedResource
from pylabrobot.thermocycling.thermocycler import Thermocycler

from .odtc_backend import ODTCBackend
from .odtc_model import ODTCConfig, ODTCHardwareConstraints, ODTC_DIMENSIONS


def _model_from_variant(variant: int) -> str:
  """Return model string from ODTC variant code."""
  if variant == 960000:
    return "ODTC 96"
  if variant == 384000:
    return "ODTC 384"
  return "ODTC"


class ODTCThermocycler(Thermocycler):
  """Inheco ODTC thermocycler resource.

  Owns connection params (odtc_ip, variant) and creates ODTCBackend by default.
  Dimensions (147 x 298 x 130 mm) are set from ODTC_DIMENSIONS.
  """

  def __init__(
    self,
    name: str,
    odtc_ip: str,
    variant: int = 384,
    child_location: Coordinate = Coordinate.zero(),
    child: Optional[ItemizedResource] = None,
    backend: Optional[ODTCBackend] = None,
    **backend_kwargs: Any,
  ):
    """Initialize ODTC thermocycler.

    Args:
      name: Human-readable name.
      odtc_ip: IP address of the ODTC device.
      variant: Well count (96, 384) or ODTC variant code (960000, 384000, 3840000).
        Normalized via backend; default 384.
      child_location: Position where a plate sits on the block.
      child: Optional plate/rack already loaded on the module.
      backend: Optional pre-constructed ODTCBackend; if None, one is created
        from odtc_ip, variant, and backend_kwargs.
      **backend_kwargs: Passed to ODTCBackend when backend is None (e.g. client_ip,
        logger, poll_interval, lifetime_of_execution, on_response_event_missing).
    """
    backend = backend or ODTCBackend(odtc_ip=odtc_ip, variant=variant, **backend_kwargs)
    model = _model_from_variant(backend.variant)
    super().__init__(
      name=name,
      size_x=ODTC_DIMENSIONS.x,
      size_y=ODTC_DIMENSIONS.y,
      size_z=ODTC_DIMENSIONS.z,
      backend=backend,
      child_location=child_location,
      category="thermocycler",
      model=model,
    )
    self.backend: ODTCBackend = backend
    self.child = child
    if child is not None:
      self.assign_child_resource(child, location=child_location)

  def serialize(self) -> dict:
    """Return a serialized representation of the thermocycler."""
    return {
      **super().serialize(),
      "odtc_ip": self.backend.odtc_ip,
      "variant": self.backend.variant,
    }

  def get_default_config(self, **kwargs: Any) -> ODTCConfig:
    """Get default ODTCConfig for this backend's variant. Delegates to backend."""
    return self.backend.get_default_config(**kwargs)

  def get_constraints(self) -> ODTCHardwareConstraints:
    """Get hardware constraints for this backend's variant. Delegates to backend."""
    return self.backend.get_constraints()

  @property
  def well_count(self) -> int:
    """Well count (96 or 384) from backend variant."""
    if self.backend.variant == 960000:
      return 96
    return 384

  async def is_profile_running(self, **backend_kwargs: Any) -> bool:
    """Return True if a profile (method) is still running.

    For ODTC, this uses device busy state (GetStatus) via
    backend.is_method_running(); ODTC does not report per-step/cycle progress.
    """
    return await self.backend.is_method_running()

  async def wait_for_profile_completion(
    self,
    poll_interval: float = 60.0,
    **backend_kwargs: Any,
  ) -> None:
    """Block until the profile (method) finishes.

    For ODTC, this delegates to backend.wait_for_method_completion(), which
    polls GetStatus until the device returns to idle. Pass timeout=... in
    backend_kwargs to limit wait time.
    """
    timeout = backend_kwargs.get("timeout")
    await self.backend.wait_for_method_completion(
      poll_interval=poll_interval,
      timeout=timeout,
    )
