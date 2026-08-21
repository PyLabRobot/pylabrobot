"""Darwin controller-node topology.

Three Two-Axis BLDC nodes, each with two devices:

  node 4 (DarwinYX):  device 0 = Y, device 1 = X
  node 5 (DarwinZW):  device 0 = Z, device 1 = W
  node 6 (DarwinGZg): device 0 = G, device 1 = Zg

The master node lives at ``InstructionAddress(1, 0)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from ..protocol.gemini.packet import InstructionAddress
from ..types import Axis


@dataclass(frozen=True)
class NodeSpec:
  """A Two-Axis BLDC controller node in the Darwin tree.

  Attributes:
    name: The node's descriptive name, e.g. ``"DarwinYX"``.
    node_id: The node's address on the controller tree.
    axes: The axis driven by device 0 and the axis driven by device 1.
  """

  name: str
  node_id: int
  axes: Tuple[Axis, Axis]

  def device_address(self, axis: Axis) -> InstructionAddress:
    """Return this node's device address for one of its two axes.

    Args:
      axis: The axis to address; must be one of :attr:`axes`.

    Returns:
      The controller-tree address of the device driving ``axis``.
    """
    dev_id = self.axes.index(axis)
    return InstructionAddress(node_id=self.node_id, dev_id=dev_id)

  @property
  def address(self) -> InstructionAddress:
    """Address of the node itself (device 0 -- used for node-level subcommands)."""
    return InstructionAddress(node_id=self.node_id, dev_id=0)


DARWIN_YX = NodeSpec("DarwinYX", node_id=4, axes=("y", "x"))
DARWIN_ZW = NodeSpec("DarwinZW", node_id=5, axes=("z", "w"))
DARWIN_GZG = NodeSpec("DarwinGZg", node_id=6, axes=("g", "zg"))

CONTROLLER_NODES: Tuple[NodeSpec, ...] = (DARWIN_YX, DARWIN_ZW, DARWIN_GZG)


# Axis -> NodeSpec lookup, built once at import time.
_AXIS_TO_NODE = {}
for _node in CONTROLLER_NODES:
  for _axis in _node.axes:
    _AXIS_TO_NODE[_axis] = _node
del _node, _axis


def axis_address(axis: Axis) -> InstructionAddress:
  """Return the Gemini controller-tree address for the given axis's motor device.

  Args:
    axis: The axis to look up.

  Returns:
    The device address that owns ``axis``.

  Raises:
    ValueError: If ``axis`` has no entry in the Darwin topology.
  """
  try:
    node = _AXIS_TO_NODE[axis]
  except KeyError as exc:
    raise ValueError(f"No Darwin topology entry for axis {axis!r}") from exc
  return node.device_address(axis)


def axis_node(axis: Axis) -> NodeSpec:
  """Return the node that owns the given axis.

  Args:
    axis: The axis to look up.

  Returns:
    The owning :class:`NodeSpec`.

  Raises:
    ValueError: If ``axis`` has no entry in the Darwin topology.
  """
  try:
    return _AXIS_TO_NODE[axis]
  except KeyError as exc:
    raise ValueError(f"No Darwin topology entry for axis {axis!r}") from exc


def all_axes() -> Tuple[Axis, ...]:
  """Return all six Darwin axes in a consistent order: X, Y, Z, W, G, Zg."""
  return ("x", "y", "z", "w", "g", "zg")
