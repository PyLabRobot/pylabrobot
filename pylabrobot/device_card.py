"""DeviceCard — machine-readable device description.

A :class:`DeviceCard` is a metadata bag attached to a :class:`Device`.
It carries:

- **Identity** — persistent identifier (e.g. PIDInst Handle URI),
  landing page, friendly name. Empty in the model-base card; each
  deployment populates it with its own unit's identity.
- **Capability specs** — operating ranges, supported settings, and
  feature flags pulled from the operator's manual. Lets UIs and
  validators reason about what a unit can actually do.
- **Connection metadata** — protocol, port, auth method, discovery
  mechanism. Configuration the model defines once, used by every
  deployment.

Two-tier design::

    base = ODYSSEY_CLASSIC_BASE                 # ships with the device package
    instance = DeviceCard.instance(identity={   # per-deployment
        "pid": "http://hdl.handle.net/21.11157/psf97-zv353",
        "landing_page": "https://b2inst.gwdg.de/records/psf97-zv353",
        "name": "Odyssey, Lab 3 (WUR HAP)",
    })
    card = base.merge(instance)                 # effective deployed card

Devices that carry a card declare the :class:`HasDeviceCard` mixin so
the attribute is discoverable via type checks. Tooling that consumes
identity (TIFF tagging, provenance writers, dataset registration) can
duck-type ``hasattr(device, "card")`` or rely on the mixin.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class DeviceCard:
  """Machine-readable device description with merge and introspection.

  Fields:
    name: Friendly model name (e.g. "Odyssey Classic").
    vendor: Vendor name (e.g. "LI-COR Biosciences").
    model: Model number / SKU (e.g. "9120").
    capabilities: Per-capability spec sheet — keys are capability
      names (``"scanning"``, ``"image_retrieval"``), values are dicts
      of features / specs / ranges.
    connection: Connection metadata (protocol, port, auth, network).
    identity: Per-unit identity — PID, landing page, friendly name.
      Empty in the model-base card; populated at the instance layer.
  """

  name: str = ""
  vendor: str = ""
  model: str = ""
  capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)
  connection: dict[str, Any] = field(default_factory=dict)
  identity: dict[str, Any] = field(default_factory=dict)

  @classmethod
  def instance(cls, **kwargs: Any) -> "DeviceCard":
    """Build a partial card for instance-level overrides.

    Use when populating per-deployment identity (PID, landing page) or
    overriding a model-base spec for a non-standard unit.
    """
    return cls(**kwargs)

  def merge(self, other: "DeviceCard") -> "DeviceCard":
    """Deep-merge ``other`` on top of ``self``. Returns a new card.

    Merge rules:
      - Scalar fields (name, vendor, model): ``other`` wins if non-empty.
      - capabilities: per-capability shallow merge; ``other`` keys
        override; new capabilities from ``other`` are added.
      - connection, identity: shallow dict merge, ``other`` wins on key
        collision.

    Neither input is mutated.
    """
    merged_caps = copy.deepcopy(self.capabilities)
    for cap_name, cap_data in other.capabilities.items():
      if cap_name in merged_caps:
        merged_caps[cap_name].update(cap_data)
      else:
        merged_caps[cap_name] = copy.deepcopy(cap_data)

    return DeviceCard(
      name=other.name or self.name,
      vendor=other.vendor or self.vendor,
      model=other.model or self.model,
      capabilities=merged_caps,
      connection={**self.connection, **other.connection},
      identity={**self.identity, **other.identity},
    )

  def has(self, capability: str) -> bool:
    """Return True if the card declares ``capability``."""
    return capability in self.capabilities

  def get(self, capability: str, key: str, default: Any = None) -> Any:
    """Return a single feature / spec value for ``capability``."""
    return self.capabilities.get(capability, {}).get(key, default)

  def features(self, capability: str) -> dict[str, bool]:
    """Return all boolean feature flags for ``capability``."""
    cap = self.capabilities.get(capability, {})
    return {k: v for k, v in cap.items() if isinstance(v, bool)}

  def specs(self, capability: str) -> dict[str, Any]:
    """Return all non-boolean specs for ``capability``."""
    cap = self.capabilities.get(capability, {})
    return {k: v for k, v in cap.items() if not isinstance(v, bool)}

  def to_dict(self) -> dict:
    """Serialize to a JSON-compatible dictionary."""
    return {
      "name": self.name,
      "vendor": self.vendor,
      "model": self.model,
      "capabilities": copy.deepcopy(self.capabilities),
      "connection": copy.deepcopy(self.connection),
      "identity": copy.deepcopy(self.identity),
    }

  @classmethod
  def from_dict(cls, data: dict) -> "DeviceCard":
    """Reconstruct from a dictionary (e.g. loaded from JSON)."""
    return cls(
      name=data.get("name", ""),
      vendor=data.get("vendor", ""),
      model=data.get("model", ""),
      capabilities=data.get("capabilities", {}),
      connection=data.get("connection", {}),
      identity=data.get("identity", {}),
    )

  def to_json(self, indent: int = 2) -> str:
    """Serialize to JSON string."""
    return json.dumps(self.to_dict(), indent=indent)

  @classmethod
  def from_json(cls, json_str: str) -> "DeviceCard":
    """Reconstruct from JSON string."""
    return cls.from_dict(json.loads(json_str))


class HasDeviceCard:
  """Mixin for devices that carry a :class:`DeviceCard`.

  Devices that want a card declare this mixin and assign ``self.card``
  in their constructor. The mixin makes the attribute discoverable
  via type checks::

      if isinstance(device, HasDeviceCard):
        embed_identity(device.card.identity)

  Same shape as :class:`pylabrobot.capabilities.loading_tray.HasLoadingTray`
  — a Device-attribute marker mixin, not a Backend mixin.
  """

  card: Optional[DeviceCard] = None
