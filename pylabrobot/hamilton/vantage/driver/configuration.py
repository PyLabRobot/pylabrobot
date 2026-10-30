from dataclasses import dataclass
from typing import Iterable

# The arm types whose name includes an internal plate gripper.
ARM_TYPES_WITH_IPG = frozenset({1, 2, 3, 5, 6, 7, 8, 10})


def has_ipg_node(node_names: Iterable[str]) -> bool:
  """Whether a list of node names has the IPG's, which is `R0`."""
  return any(name.startswith("R") for name in node_names)


def ipg_is_fitted(arm_type: int, node_names: Iterable[str]) -> bool:
  """Whether an internal plate gripper is fitted, as far as the arm says.

  It is, if the arm type names one or the stored node names have one. Either is enough: an IPG
  that is fitted and not noticed can be driven into, so a doubt counts as fitted.
  """
  return arm_type in ARM_TYPES_WITH_IPG or has_ipg_node(node_names)


@dataclass
class DeviceConfiguration:
  """What a Vantage turns out to be, read from it by `VantageDriver.discover`.

  Only what the machine can be asked is held here, and nothing is assumed about the rest.
  """

  num_channels: int
  """How many pipetting channels the machine has: one for each entry of the `A1PM QA` reply."""

  arm_type: int = 0
  """Which arm the machine has, as `A1AM QU` reports it. 0 until read."""

  ipg_installed: bool = False
  """Whether an internal plate gripper is fitted: see `ipg_is_fitted`."""
