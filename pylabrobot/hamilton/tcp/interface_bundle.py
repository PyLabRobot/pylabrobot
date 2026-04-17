"""Resolve logical interface roles to firmware :class:`Address` values via dot-paths.

Drivers supply a mapping of role name → :class:`InterfacePathSpec` (path, required flags).
This module performs the shared ``resolve_path`` loop and logging; product-specific
typed bundles (e.g. ``PrepResolvedInterfaces``) live next to each driver.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional

from pylabrobot.hamilton.tcp.packets import Address

if TYPE_CHECKING:
  from pylabrobot.hamilton.tcp.client import HamiltonTCPClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterfacePathSpec:
  """Single logical interface: strict dot-path and resolution policy."""

  path: str
  required: bool
  raise_when_missing: bool = True


async def resolve_interface_path_specs(
  client: HamiltonTCPClient,
  specs: Mapping[str, InterfacePathSpec],
  *,
  instrument_label: str = "instrument",
) -> dict[str, Optional[Address]]:
  """Resolve each path; required interfaces fail fast on :exc:`KeyError` from ``resolve_path``."""
  resolved: dict[str, Optional[Address]] = {}
  for name, spec in specs.items():
    try:
      addr = await client.resolve_path(spec.path)
      resolved[name] = addr
      logger.debug(
        "Resolved %s interface %s → %s (%s)",
        instrument_label,
        name,
        addr,
        spec.path,
      )
    except KeyError:
      if spec.required:
        raise RuntimeError(
          f"Could not find required interface '{name}' ({spec.path}) on {instrument_label}."
        ) from None
      resolved[name] = None
      if spec.raise_when_missing:
        logger.warning(
          "Optional %s interface missing: %s (%s)",
          instrument_label,
          name,
          spec.path,
        )

  found = sorted(n for n, a in resolved.items() if a is not None)
  missing_opt = sorted(
    n for n, s in specs.items() if not s.required and resolved.get(n) is None
  )
  logger.info("%s interfaces: %s", instrument_label, ", ".join(found))
  if missing_opt:
    logger.info("%s optional not present: %s", instrument_label, ", ".join(missing_opt))

  return resolved
