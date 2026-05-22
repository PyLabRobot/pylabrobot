"""Darwin controller-node topology.

Three Two-Axis BLDC nodes, each with two devices. Ported from the
``Build-System`` function in ``darwin_bridge.ps1``:

    node 4 (DarwinYX):  device 0 = Y, device 1 = X
    node 5 (DarwinZW):  device 0 = Z, device 1 = W
    node 6 (DarwinGZg): device 0 = G, device 1 = Zg

The master node lives at ``InstructionAddress(1, 0)`` (:data:`MASTER_ADDRESS`).
"""

from __future__ import annotations

from dataclasses import dataclass

from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.packet import (
  InstructionAddress,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.types import Axis


@dataclass(frozen=True)
class NodeSpec:
  """A Two-Axis BLDC controller node in the Darwin tree."""

  name: str
  node_id: int
  axes: tuple[Axis, Axis]  # (device 0, device 1)

  def device_address(self, axis: Axis) -> InstructionAddress:
    dev_id = self.axes.index(axis)
    return InstructionAddress(node_id=self.node_id, dev_id=dev_id)

  @property
  def address(self) -> InstructionAddress:
    """Address of the node itself (device 0 — used for node-level subcommands)."""
    return InstructionAddress(node_id=self.node_id, dev_id=0)


DARWIN_YX = NodeSpec("DarwinYX", node_id=4, axes=(Axis.Y, Axis.X))
DARWIN_ZW = NodeSpec("DarwinZW", node_id=5, axes=(Axis.Z, Axis.W))
DARWIN_GZG = NodeSpec("DarwinGZg", node_id=6, axes=(Axis.G, Axis.Zg))

CONTROLLER_NODES: tuple[NodeSpec, ...] = (DARWIN_YX, DARWIN_ZW, DARWIN_GZG)


# Axis → (node, dev_id) lookup, derived once at import time
_AXIS_TO_NODE: dict[Axis, NodeSpec] = {}
for _node in CONTROLLER_NODES:
  for _axis in _node.axes:
    _AXIS_TO_NODE[_axis] = _node
del _node, _axis


def axis_address(axis: Axis) -> InstructionAddress:
  """Return the Gemini ``InstructionAddress`` for the given axis's motor device."""
  try:
    node = _AXIS_TO_NODE[axis]
  except KeyError as exc:
    raise ValueError(f"No Darwin topology entry for axis {axis!r}") from exc
  return node.device_address(axis)


def axis_node(axis: Axis) -> NodeSpec:
  """Return the ``NodeSpec`` that owns the given axis."""
  try:
    return _AXIS_TO_NODE[axis]
  except KeyError as exc:
    raise ValueError(f"No Darwin topology entry for axis {axis!r}") from exc


def all_axes() -> tuple[Axis, ...]:
  """All six Darwin axes in a consistent order: X, Y, Z, W, G, Zg."""
  return (Axis.X, Axis.Y, Axis.Z, Axis.W, Axis.G, Axis.Zg)
