from dataclasses import dataclass, field
from typing import Any, Dict

from pylabrobot.capabilities.capability import BackendParams


@dataclass
class _DictBackendParams(BackendParams):
  """Wraps legacy **backend_kwargs into a BackendParams for the new capability interface."""

  kwargs: Dict[str, Any] = field(default_factory=dict)
