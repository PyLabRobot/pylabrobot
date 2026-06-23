"""Stacker resources for Agilent BenchCel stackers."""

from typing import List

from pylabrobot.resources.resource_stack import ResourceStack


def benchcel_4r_stacks(name_prefix: str = "benchcel_stacker") -> List[ResourceStack]:
  """Create the four LIFO stacks for an Agilent BenchCel 4R.

  Each stacker is modelled as a z-direction
  :class:`~pylabrobot.resources.resource_stack.ResourceStack` (a single-ended LIFO stack), used
  with the :class:`~pylabrobot.storage.Stacker` capability. Stack height is computed from each
  plate's ``stacking_z_height`` as plates are added, so -- unlike the previous fixed-site rack
  model -- no ``num_sites``/``site_pitch``/``site_height`` needs to be supplied.

  The stacks are named ``{name_prefix}_1`` .. ``{name_prefix}_4`` and are ordered to match the
  human stacker numbers 1-4 used by the backend and firmware.
  """
  return [ResourceStack(name=f"{name_prefix}_{i}", direction="z") for i in range(1, 5)]
