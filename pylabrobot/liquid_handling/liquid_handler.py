""" Defines LiquidHandler class, the coordinator for liquid handling operations. """

import functools
import logging
import numbers
import time
from typing import Dict, Union, Optional, List, Callable, Sequence

from pylabrobot import utils
# from pylabrobot.default import DEFAULT
from pylabrobot.liquid_handling.resources.abstract import Deck
from pylabrobot.liquid_handling.tip_tracker import ChannelTipTracker, does_tip_tracking
from pylabrobot.utils.list import expand

from .backends import LiquidHandlerBackend
from .liquid_classes import (
  LiquidClass,
  StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol
)
from .resources import (
  Resource,
  ResourceStack,
  Coordinate,
  Carrier,
  CarrierSite,
  Lid,
  Plate,
  PlateReader,
  Tip,
  TipRack,
  Well
)
from .standard import (
  Pickup,
  Drop,
  Aspiration,
  Dispense,
  Move
)

logger = logging.getLogger(__name__) # TODO: get from somewhere else?


def need_setup_finished(func: Callable): # pylint: disable=no-self-argument
  """ Decorator for methods that require the liquid handler to be set up.

  Checked by verifying `self.setup_finished` is `True`.

  Raises:
    RuntimeError: If the liquid handler is not set up.
  """

  @functools.wraps(func)
  def wrapper(self, *args, **kwargs):
    if not self.setup_finished:
      raise RuntimeError("The setup has not finished. See `LiquidHandler.setup`.")
    func(self, *args, **kwargs) # pylint: disable=not-callable
  return wrapper


class LiquidHandler:
  """
  Front end for liquid handlers.

  This class is the front end for liquid handlers; it provides a high-level interface for
  interacting with liquid handlers. In the background, this class uses the low-level backend (
  defined in `pyhamilton.liquid_handling.backends`) to communicate with the liquid handler.

  This class is responsible for:
    - Parsing and validating the layout.
    - Performing liquid handling operations. This includes:
      - Aspirating from / dispensing liquid to a location.
      - Transporting liquid from one location to another.
      - Picking up tips from and dropping tips into a tip box.
    - Serializing and deserializing the liquid handler deck. Decks are serialized as JSON and can
      be loaded from a JSON or .lay (legacy) file.
    - Static analysis of commands. This includes checking the presence of tips on the head, keeping
      track of the number of tips in the tip box, and checking the volume of liquid in the liquid
      handler.

  Attributes:
    setup_finished: Whether the liquid handler has been setup.
  """

  def __init__(self, backend: LiquidHandlerBackend, deck: Deck):
    """ Initialize a LiquidHandler.

    Args:
      backend: Backend to use.
      deck: Deck to use.
    """

    self.backend = backend
    self.setup_finished = False
    self._picked_up_tips: Optional[List[Tip]] = None
    self._picked_up_tips96: Optional[TipRack] = None

    self.deck = deck
    self.deck.resource_assigned_callback_callback = self.resource_assigned_callback
    self.deck.resource_unassigned_callback_callback = self.resource_unassigned_callback

    self.trackers: Dict[int, ChannelTipTracker] = {}

  def __del__(self):
    # If setup was finished, close automatically to prevent blocking the USB device.
    if self.setup_finished:
      self.stop()

  def setup(self):
    """ Prepare the robot for use. """

    if self.setup_finished:
      raise RuntimeError("The setup has already finished. See `LiquidHandler.stop`.")

    self.backend.setup()
    self.setup_finished = True

    for channel in range(self.backend.num_channels):
      self.trackers[channel] = ChannelTipTracker()

  def stop(self):
    self.backend.stop()
    self.setup_finished = False

  def __enter__(self):
    self.setup()
    return self

  def __exit__(self, *exc):
    self.stop()
    return False

  # TODO: artifact until we move .summary() to STARLetDeck
  @staticmethod
  def _rails_for_x_coordinate(x: int):
    """ Convert an x coordinate to a rail identifier (1-30 for STARLet, max 54 for STAR). """
    # pylint: disable=invalid-name
    _RAILS_WIDTH = 22.5 # TODO: this entire function is gonna be removed.
    return int((x - 100.0) / _RAILS_WIDTH) + 1

  def resource_assigned_callback(self, resource: Resource):
    self.backend.assigned_resource_callback(resource)

  def resource_unassigned_callback(self, resource: Resource):
    self.backend.unassigned_resource_callback(resource.name)

  def unassign_resource(self, resource: Union[str, Resource]): # TODO: remove this.
    """ Unassign an assigned resource.

    Args:
      resource: The resource to unassign.

    Raises:
      KeyError: If the resource is not currently assigned to this liquid handler.
    """

    if isinstance(resource, Resource):
      resource = resource.name

    r = self.deck.get_resource(resource)
    if r is None:
      raise KeyError(f"Resource '{resource}' is not assigned to this liquid handler.")
    r.unassign()

  def get_resource(self, name: str) -> Resource:
    """ Find a resource on the deck of this liquid handler. Also see :meth:`~Deck.get_resource`.

    Args:
      name: name of the resource.

    Returns:
      The resource with the given name, or None if not found.
    """

    return self.deck.get_resource(name)

  def summary(self):
    """ Prints a string summary of the deck layout.

    Example:
      Printing a summary of the deck layout:

      >>> lh.summary()
      Rail     Resource                   Type                Coordinates (mm)
      ==============================================================================================
      (1) ├── tip_car                    TIP_CAR_480_A00     (x: 100.000, y: 240.800, z: 164.450)
          │   ├── tip_rack_01            STF_L               (x: 117.900, y: 240.000, z: 100.000)
    """

    if len(self.deck.get_all_resources()) == 0:
      raise ValueError(
          "This liquid editor does not have any resources yet. "
          "Build a layout first by calling `assign_resource()`. "
          "See the documentation for details. (TODO: link)"
      )

    # Print header.
    print(utils.pad_string("Rail", 9) + utils.pad_string("Resource", 27) + \
          utils.pad_string("Type", 20) + "Coordinates (mm)")
    print("=" * 95)

    def print_resource(resource):
      # TODO: print something else if resource is not assigned to a rails.
      rails = LiquidHandler._rails_for_x_coordinate(resource.location.x)
      rail_label = utils.pad_string(f"({rails})", 4)
      print(f"{rail_label} ├── {utils.pad_string(resource.name, 27)}"
            f"{utils.pad_string(resource.__class__.__name__, 20)}"
            f"{resource.get_absolute_location()}")

      if isinstance(resource, Carrier):
        for site in resource.get_sites():
          if site.resource is None:
            print("     │   ├── <empty>")
          else:
            subresource = site.resource
            if isinstance(subresource, (TipRack, Plate)):
              location = subresource.get_item("A1").get_absolute_location()
            else:
              location = subresource.get_absolute_location()
            print(f"     │   ├── {utils.pad_string(subresource.name, 27-4)}"
                  f"{utils.pad_string(subresource.__class__.__name__, 20)}"
                  f"{location}")

    # Sort resources by rails, left to right in reality.
    sorted_resources = sorted(self.deck.children, key=lambda r: r.get_absolute_location().x)

    # Print table body.
    print_resource(sorted_resources[0])
    for resource in sorted_resources[1:]:
      print("     │")
      print_resource(resource)

  def _assert_positions_unique(self, positions: List[str]):
    """ Returns whether all items in `positions` are unique where they are not `None`.

    Args:
      positions: List of positions.
    """

    not_none = [p for p in positions if p is not None]
    if len(not_none) != len(set(not_none)):
      raise ValueError("Positions must be unique.")

  def _assert_resources_exist(self, resources: Sequence[Resource]):
    """ Checks that each resource in `resources` is assigned to the deck.

    Args:
      resources: List of resources.

    Raises:
      ValueError: If a resource is not assigned to the deck.
    """

    for resource in resources:
      # see of top parent of resource is the deck (i.e. resource is assigned to deck)
      while resource.parent is not None:
        resource = resource.parent
      if resource != self.deck:
        raise ValueError(f"Resource named '{resource.name}' not found on deck.")

  @need_setup_finished
  def pick_up_tips(
    self,
    tips: List[Tip],
    use_channels: Optional[List[int]] = None,
    offsets: Union[Coordinate, List[Coordinate]] = Coordinate.zero(),
    **backend_kwargs
  ):
    """ Pick up tips from a resource.

    Examples:
      Pick up all tips in the first column.

      >>> lh.pick_up_tips(tips_resource["A1":"H1"])

      Pick up tips on odd numbered rows, skipping the other channels.

      >>> lh.pick_up_tips(channels=tips_resource[
      ...   "A1",
      ...   "C1",
      ...   "E1",
      ...   "G1",
      ... ], use_channels=[0, 2, 4, 6])

      Pick up tips from different tip resources:

      >>> lh.pick_up_tips(tips_resource1["A1"] + tips_resource2["B2"] + tips_resource3["C3"])

      Picking up tips with different offsets:

      >>> lh.pick_up_tips(
      ...   channels=tips_resource["A1":"C1"],
      ...   offsets=[
      ...     Coordinate(0, 0, 0), # A1
      ...     Coordinate(1, 1, 1), # B1
      ...     Coordinate.zero() # C1
      ...   ]
      ... )

    Args:
      tips: Tips to pick up.
      use_channels: List of channels to use. Index from front to back. If `None`, the first
        `len(channels)` channels will be used.
      offsets: List of offsets for each channel, a translation that will be applied to the tip
        drop location. If `None`, no offset will be applied.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If the positions are not unique.

      ChannelHasTipError: If a channel already has a tip.

      SpotHasNoTipError: If a spot does not have a tip.
    """

    self._assert_resources_exist(tips)

    offsets = expand(offsets, len(tips))

    if use_channels is None:
      use_channels = list(range(len(tips)))

    assert len(tips) == len(offsets) == len(use_channels), \
      "Number of tips and offsets and use_channels must be equal."

    pickups = [(Pickup(tip, offset) if tip is not None else None)
            for tip, offset in zip(tips, offsets)]

    for channel, op in zip(use_channels, pickups):
      if does_tip_tracking() and not op.resource.tracker.is_disabled:
        op.resource.tracker.queue_op(op)
        self.trackers[channel].queue_op(op)

    try:
      self.backend.pick_up_tips(ops=pickups, use_channels=use_channels, **backend_kwargs)
    except:
      for channel, op in zip(use_channels, pickups):
        if does_tip_tracking() and not op.resource.tracker.is_disabled:
          op.resource.tracker.rollback()
          self.trackers[channel].rollback()
      raise
    else:
      for channel, op in zip(use_channels, pickups):
        if does_tip_tracking() and not op.resource.tracker.is_disabled:
          op.resource.tracker.commit()
          self.trackers[channel].commit()

  @need_setup_finished
  def drop_tips(
    self,
    tips: List[Tip],
    use_channels: Optional[List[int]] = None,
    offsets: Union[Coordinate, List[Coordinate]] = Coordinate.zero(),
    **backend_kwargs
  ):
    """ Drop tips to a resource.

    Examples:
      Droping tips to the first column.

      >>> lh.pick_up_tips(tip_rack["A1:H1"])

      Droping tips with different offsets:

      >>> lh.drop_tips(
      ...   channels=tips_resource["A1":"C1"],
      ...   offsets=[
      ...     Coordinate(0, 0, 0), # A1
      ...     Coordinate(1, 1, 1), # B1
      ...     Coordinate.zero() # C1
      ...   ]
      ... )

    Args:
      tips: Tip resource locations to drop to.
      use_channels: List of channels to use. Index from front to back. If `None`, the first
        `len(channels)` channels will be used.
      offsets: List of offsets for each channel, a translation that will be applied to the tip
        pickup location. If `None`, no offset will be applied.
      kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If no channel will pick up a tip, in other words, if all channels are `None` or
        if the list of channels is empty.

      ValueError: If the positions are not unique.

      ChannelHasNoTipError: If a channel does not have a tip.

      SpotHasTipError: If a spot already has a tip.
    """

    self._assert_resources_exist(tips)

    offsets = expand(offsets, len(tips))

    if use_channels is None:
      use_channels = list(range(len(tips)))

    assert len(tips) == len(offsets) == len(use_channels), \
      "Number of channels and offsets and use_channels must be equal."

    drops = [(Drop(tip, offset) if tip is not None else None)
            for tip, offset in zip(tips, offsets)]

    for channel, op in zip(use_channels, drops):
      if does_tip_tracking() and not op.resource.tracker.is_disabled:
        op.resource.tracker.queue_op(op)
        self.trackers[channel].queue_op(op)

    try:
      self.backend.drop_tips(ops=drops, use_channels=use_channels, **backend_kwargs)
    except:
      for channel, op in zip(use_channels, drops):
        if does_tip_tracking() and not op.resource.tracker.is_disabled:
          op.resource.tracker.rollback()
          self.trackers[channel].rollback()
      raise
    else:
      for channel, op in zip(use_channels, drops):
        if does_tip_tracking() and not op.resource.tracker.is_disabled:
          op.resource.tracker.commit()
          self.trackers[channel].commit()

  def return_tips(self):
    """ Return all tips that are currently picked up to their original place.

    Examples:
      Return the tips on the head to the tip rack where they were picked up:

      >>> lh.pick_up_tips(tip_rack["A1"])
      >>> lh.return_tips()

    Raises:
      RuntimeError: If no tips have been picked up.
    """

    tips: List[Tip] = []
    channels: List[int] = []

    for channel, tracker in self.trackers.items():
      if tracker.current_tip is not None:
        tips.append(tracker.current_tip)
        channels.append(channel)

    if len(tips) == 0:
      raise RuntimeError("No tips have been picked up.")

    self.drop_tips(tips=tips, use_channels=channels)

  @need_setup_finished
  def aspirate(
    self,
    resources: Union[Sequence[Resource], Resource],
    vols: Union[List[float], float],
    use_channels: Optional[List[int]] = None,
    flow_rates: Optional[Union[float, List[Optional[float]]]] = None,
    liquid_classes: Optional[Union[LiquidClass, List[Optional[LiquidClass]]]] =
      StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol,
    end_delay: float = 0,
    offsets: Optional[Union[Coordinate, List[Coordinate]]] = None,
    **backend_kwargs
  ):
    """ Aspirate liquid from the specified wells.

    Examples:
      Aspirate a constant amount of liquid from the first column:

      >>> lh.aspirate(plate["A1:H1"], 50)

      Aspirate an linearly increasing amount of liquid from the first column:

      >>> lh.aspirate(plate["A1:H1"], range(0, 500, 50))

      Aspirate arbitrary amounts of liquid from the first column:

      >>> lh.aspirate(plate["A1:H1"], [0, 40, 10, 50, 100, 200, 300, 400])

      Aspirate liquid from wells in different plates:

      >>> lh.aspirate(plate["A1"] + plate2["A1"] + plate3["A1"], 50)

      Aspirating with a 10mm z-offset:

      >>> lh.aspirate(plate["A1"], vols=50, offsets=[Coordinate(0, 0, 10)])

      Aspirate from a blue bucket (big container), with the first 4 channels (which will be
      spaced equally apart):

      >>> lh.aspirate(blue_bucket, vols=50, use_channels=[0, 1, 2, 3])

    Args:
      resources: A list of wells to aspirate liquid from. Can be a single resource, or a list of
        resources. If a single resource is specified, all channels will aspirate from the same
        resource.
      vols: A list of volumes to aspirate, one for each channel. If `vols` is a single number, then
        all channels will aspirate that volume.
      use_channels: List of channels to use. Index from front to back. If `None`, the first
        `len(wells)` channels will be used.
      flow_rates: the aspiration speed. In ul/s.
      liquid_classes: the liquid class with which to perform the aspirations. It provides default
        values for parameters flow_rate, and soon others.
      end_delay: The delay after the last aspiration in seconds, optional. This is useful for when
        the tips used in the aspiration are dripping.
      offsets: List of offsets for each channel, a translation that will be applied to the
        aspiration location. If `None`, no offset will be applied.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If all channels are `None`.
    """

    # Start with computing the locations of the aspirations. Can either be a single resource, in
    # which case all channels will aspirate from there, or a list of resources.
    if isinstance(resources, Resource): # if single resource, space channels evenly
      if use_channels is None:
        use_channels = [0]

      n = len(use_channels)

      # If offsets is supplied, make sure it is a list of the correct length. If it is not in this
      # format, raise an error. If it is not supplied, make it a list of the correct length by
      # spreading channels across the resource evenly.
      if offsets is not None:
        if not isinstance(offsets, list) or len(offsets) != n:
          raise ValueError("Number of offsets must match number of channels used when aspirating "
                          "from a resource.")
      else:
        dx = resources.get_size_x() / 2
        dy = resources.get_size_y() / (n+1)
        if dy < 9:
          raise ValueError(f"Resource is too small to space {n} channels evenly.")
        offsets = [Coordinate(dx, dy * (i+1), 0) for i in range(n)]
        offsets = list(reversed(offsets)) # reverse so that first channel is at the front

      resources = [resources] * n
    else:
      if len(resources) == 0:
        raise ValueError("No channels specified")
      self._assert_resources_exist(resources)
      n = len(resources)

      if use_channels is None:
        use_channels = list(range(len(resources)))

      if offsets is None:
        offsets = Coordinate.zero()
      offsets = expand(offsets, n)

    vols = expand(vols, n)
    liquid_classes = expand(liquid_classes, n)

    if flow_rates is None:
      flow_rates = [(lc.flow_rate[0] if lc is not None else None) for lc in liquid_classes]
    elif isinstance(flow_rates, (numbers.Rational, float)):
      flow_rates = [flow_rates] * n

    # Correct volumes using the liquid class' correction curve
    for i, lc in enumerate(liquid_classes):
      if lc is not None:
        vols[i] = lc.compute_corrected_volume(vols[i])

    assert len(vols) == len(offsets) == len(flow_rates)

    aspirations = [Aspiration(r, v, offset=offset, flow_rate=fr)
                   for r, v, offset, fr in zip(resources, vols, offsets, flow_rates)]

    self.backend.aspirate(ops=aspirations, use_channels=use_channels, **backend_kwargs)

    if end_delay > 0:
      time.sleep(end_delay)

  @need_setup_finished
  def dispense(
    self,
    resources: Union[Resource, Sequence[Resource]],
    vols: Union[List[float], float],
    use_channels: Optional[List[int]] = None,
    flow_rates: Optional[Union[float, List[Optional[float]]]] = None,
    liquid_classes: Union[LiquidClass, List[LiquidClass]] =
      StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol,
    end_delay: float = 0,
    offsets: Optional[Union[Coordinate, List[Coordinate]]] = None,
    **backend_kwargs
  ):
    """ Dispense liquid to the specified channels.

    Examples:
      Dispense a constant amount of liquid to the first column:

      >>> lh.dispense(plate["A1:H1"], 50)

      Dispense an linearly increasing amount of liquid to the first column:

      >>> lh.dispense(plate["A1:H1"], range(0, 500, 50))

      Dispense arbitrary amounts of liquid to the first column:

      >>> lh.dispense(plate["A1:H1"], [0, 40, 10, 50, 100, 200, 300, 400])

      Dispense liquid to wells in different plates:

      >>> lh.dispense((plate["A1"], 50), (plate2["A1"], 50), (plate3["A1"], 50))

      Dispensing with a 10mm z-offset:

      >>> lh.dispense(plate["A1"], vols=50, offsets=[Coordinate(0, 0, 10)])

      Dispense a blue bucket (big container), with the first 4 channels (which will be spaced
      equally apart):

      >>> lh.dispense(blue_bucket, vols=50, use_channels=[0, 1, 2, 3])

    Args:
      wells: A list of resources to dispense liquid to. Can be a list of resources, or a single
        resource, in which case all channels will dispense to that resource.
      vols: A list of volumes to dispense, one for each channel, or a single volume to dispense to
        all channels. If `vols` is a single number, then all channels will dispense that volume. In
        units of ul.
      use_channels: List of channels to use. Index from front to back. If `None`, the first
        `len(channels)` channels will be used.
      flow_rates: the flow rates, in ul/s
      liquid_classes: the liquid class with which to perform the dispenses. It provides default
        values for parameters flow_rate, and soon others.
      end_delay: The delay after the last dispense in seconds, optional. This is useful for when
        the tips used in the dispense are dripping.
      offsets: List of offsets for each channel, a translation that will be applied to the
        dispense location. If `None`, no offset will be applied.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If the dispense info is invalid, in other words, when all channels are `None`.

      ValueError: If all channels are `None`.
    """

    # Start with computing the locations of the dispenses. Can either be a single resource, in
    # which case all channels will dispense to there, or a list of resources.
    if isinstance(resources, Resource): # if single resource, space channels evenly
      if use_channels is None:
        use_channels = [0]

      n = len(use_channels)

      # If offsets is supplied, make sure it is a list of the correct length. If it is not in this
      # format, raise an error. If it is not supplied, make it a list of the correct length by
      # spreading channels across the resource evenly.
      if offsets is not None:
        if not isinstance(offsets, list) or len(offsets) != n:
          raise ValueError("Number of offsets must match number of channels used when dispensing "
                          "to a resource.")
      else:
        dx = resources.get_size_x() / 2
        dy = resources.get_size_y() / (n+1)
        if dy < 9:
          raise ValueError(f"Resource is too small to space {n} channels evenly.")
        offsets = [Coordinate(dx, dy * (i+1), 0) for i in range(n)]
        offsets = list(reversed(offsets)) # reverse so that first channel is at the front

      resources = [resources] * n
    else:
      if len(resources) == 0:
        raise ValueError("No channels specified")
      self._assert_resources_exist(resources)
      n = len(resources)

      if use_channels is None:
        use_channels = list(range(len(resources)))

      if offsets is None:
        offsets = Coordinate.zero()
      offsets = expand(offsets, n)

    vols = expand(vols, n)
    liquid_classes = expand(liquid_classes, n)

    if flow_rates is None:
      flow_rates = [(lc.flow_rate[1] if lc is not None else None) for lc in liquid_classes]
    elif isinstance(flow_rates, (numbers.Rational, float)):
      flow_rates = [flow_rates] * n

    # Correct volumes using the liquid class' correction curve
    for i, lc in enumerate(liquid_classes):
      if lc is not None:
        vols[i] = lc.compute_corrected_volume(vols[i])

    assert len(vols) == len(offsets) == len(flow_rates)

    dispenses = [Dispense(r, v, offset=offset, flow_rate=fr)
                   for r, v, offset, fr in zip(resources, vols, offsets, flow_rates)]

    self.backend.dispense(ops=dispenses, use_channels=use_channels, **backend_kwargs)

    if end_delay > 0:
      time.sleep(end_delay)

  def transfer(
    self,
    source: Well,
    targets: Union[Well, List[Well]],
    source_vol: Optional[float] = None,
    ratios: Optional[List[float]] = None,
    target_vols: Optional[List[float]] = None,
    aspiration_flow_rate: Optional[float] = None,
    aspiration_liquid_class: Optional[LiquidClass] =
      StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol,
    dispense_flow_rates: Optional[Union[float, List[float]]] = None,
    dispense_liquid_classes: Optional[Union[LiquidClass, List[LiquidClass]]] =
      StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol,
    **backend_kwargs
  ):
    """Transfer liquid from one well to another.

    Examples:

      Transfer 50 uL of liquid from the first well to the second well:

      >>> lh.transfer(plate["A1"], plate["B1"], source_vol=50)

      Transfer 80 uL of liquid from the first well equally to the first column:

      >>> lh.transfer(plate["A1"], plate["A1:H1"], source_vol=80)

      Transfer 60 uL of liquid from the first well in a 1:2 ratio to 2 other wells:

      >>> lh.transfer(plate["A1"], plate["B1:C1"], source_vol=60, ratios=[2, 1])

      Transfer arbitrary volumes to the first column:

      >>> lh.transfer(plate["A1"], plate["A1:H1"], target_vols=[3, 1, 4, 1, 5, 9, 6, 2])

    Args:
      source: The source well.
      targets: The target wells.
      source_vol: The volume to transfer from the source well.
      ratios: The ratios to use when transferring liquid to the target wells. If not specified, then
        the volumes will be distributed equally.
      target_vols: The volumes to transfer to the target wells. If specified, `source_vols` and
        `ratios` must be `None`.
      liquid_class: The liquid class to use for the transfer, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.
    """

    if isinstance(targets, Well):
      targets = [targets]

    if isinstance(dispense_flow_rates, numbers.Rational):
      dispense_flow_rates = [dispense_flow_rates] * len(targets)

    if target_vols is not None:
      if ratios is not None:
        raise TypeError("Cannot specify ratios and target_vols at the same time")
      if source_vol is not None:
        raise TypeError("Cannot specify source_vol and target_vols at the same time")
    else:
      if source_vol is None:
        raise TypeError("Must specify either source_vol or target_vols")

      if ratios is None:
        ratios = [1] * len(targets)

      target_vols = [source_vol * r / sum(ratios) for r in ratios]

    self.aspirate(
      resources=[source],
      vols=[sum(target_vols)],
      flow_rates=aspiration_flow_rate,
      liquid_classes=aspiration_liquid_class,
      **backend_kwargs)
    self.dispense(
      resources=targets,
      vols=target_vols,
      flow_rates=dispense_flow_rates,
      liquid_classes=dispense_liquid_classes,
      **backend_kwargs)

  def pick_up_tips96(self, tip_rack: TipRack, **backend_kwargs):
    """ Pick up tips using the CoRe 96 head. This will pick up 96 tips.

    Examples:
      Pick up tips from a 96-tip tiprack:

      >>> lh.pick_up_tips96(my_tiprack)

    Args:
      tip_rack: The tip rack to pick up tips from.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    self.backend.pick_up_tips96(tip_rack, **backend_kwargs)

    # Save the tips as picked up.
    self._picked_up_tips96 = tip_rack

  def drop_tips96(self, tip_rack: TipRack, **backend_kwargs):
    """ Drop tips using the CoRe 96 head. This will drop 96 tips.

    Examples:
      Drop tips to a 96-tip tiprack:

      >>> lh.drop_tips96(my_tiprack)

    Args:
      tip_rack: The tip rack to drop tips to.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    self.backend.drop_tips96(tip_rack, **backend_kwargs)

    self._picked_up_tips96 = None

  def return_tips96(self):
    """ Return the tips on the 96 head to the tip rack where they were picked up.

    Examples:
      Return the tips on the 96 head to the tip rack where they were picked up:

      >>> lh.pick_up_tips96(my_tiprack)
      >>> lh.return_tips96()

    Raises:
      RuntimeError: If no tips have been picked up.
    """

    if self._picked_up_tips96 is None:
      raise RuntimeError("No tips have been picked up with the 96 head")

    self.drop_tips96(self._picked_up_tips96)

  def aspirate_plate(
    self,
    plate: Plate,
    volume: float,
    flow_rate: Optional[float] = None,
    end_delay: float = 0,
    liquid_class: Optional[LiquidClass] =
      StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol,
    **backend_kwargs
  ):
    """ Aspirate from all wells in a plate.

    Examples:
      Aspirate an entire 96 well plate:

      >>> lh.aspirate_plate(plate, volume=50)

    Args:
      resource: Resource name or resource object.
      pattern: Either a list of lists of booleans where inner lists represent rows and outer lists
        represent columns, or a string representing a range of positions. Default all.
      volume: The volume to aspirate from each well.
      end_delay: The delay after the last aspiration in seconds, optional. This is useful for when
        the tips used in the aspiration are dripping.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    # Correct volume using the liquid class' correction curve.
    if liquid_class is not None:
      volume = liquid_class.compute_corrected_volume(volume)

    if plate.num_items_x == 12 and plate.num_items_y == 8:
      self.backend.aspirate96(aspiration=Aspiration(
        resource=plate,
        volume=volume,
        flow_rate=flow_rate),
        **backend_kwargs)
    else:
      raise NotImplementedError(f"It is not possible to plate aspirate from an {plate.num_items_x} "
                               f"by {plate.num_items_y} plate")

    if end_delay > 0:
      time.sleep(end_delay)

  def dispense_plate(
    self,
    plate: Plate,
    volume: float,
    flow_rate: Optional[float] = None,
    liquid_class: Optional[LiquidClass] =
      StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol,
    end_delay: float = 0,
    **backend_kwargs
  ):
    """ Dispense to all wells in a plate.

    Examples:
      Dispense an entire 96 well plate:

      >>> dispense96(plate, volume=50)

    Args:
      resource: Resource name or resource object.
      pattern: Either a list of lists of booleans where inner lists represent rows and outer lists
        represent columns, or a string representing a range of positions. Default all.
      volume: The volume to dispense to each well.
      end_delay: The delay after the last dispense in seconds, optional. This is useful for when
        the tips used in the dispense are dripping.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    # Correct volume using the liquid class' correction curve.
    if liquid_class is not None:
      volume = liquid_class.compute_corrected_volume(volume)

    if plate.num_items_x == 12 and plate.num_items_y == 8:
      self.backend.dispense96(dispense=Dispense(
        resource=plate,
        volume=volume,
        flow_rate=flow_rate),
        **backend_kwargs)
    else:
      raise NotImplementedError(f"It is not possible to plate dispense to an {plate.num_items_x} "
                               f"by {plate.num_items_y} plate")

    if end_delay > 0:
      time.sleep(end_delay)

  def stamp(
    self,
    source: Plate,
    target: Plate,
    volume: float,
    aspiration_flow_rate: Optional[float] = None,
    aspiration_liquid_class: Optional[LiquidClass] =
      StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol,
    dispense_flow_rate: Optional[float] = None,
    dispense_liquid_class: Optional[LiquidClass] =
      StandardVolumeFilter_Water_DispenseSurface_Part_no_transport_vol
  ):
    """ Stamp (aspiration and dispense) one plate onto another.

    Args:
      source: the source plate
      target: the target plate
      volume: the volume to be transported
      aspiration_flow_rate: the flow rate for the aspiration, in ul/s
      aspiration_liquid_class: the liquid class for the aspiration, in ul/s
      dispense_flow_rate: the flow rate for the dispense, in ul/s
      dispense_liquid_class: the liquid class for the dispense, in ul/s
    """

    assert (source.num_items_x, source.num_items_y) == (target.num_items_x, target.num_items_y), \
      "Source and target plates must be the same shape"

    self.aspirate_plate(
      plate=source,
      volume=volume,
      flow_rate=aspiration_flow_rate,
      liquid_class=aspiration_liquid_class)
    self.dispense_plate(
      plate=source,
      volume=volume,
      flow_rate=dispense_flow_rate,
      liquid_class=dispense_liquid_class)

  def move_resource(
    self,
    resource: Resource,
    to: Coordinate,
    resource_offset: Coordinate = Coordinate.zero(),
    to_offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: float = 0,
    get_direction: Move.Direction = Move.Direction.FRONT,
    put_direction: Move.Direction = Move.Direction.FRONT,
    **backend_kwargs
  ):
    """ Move a resource to a new location.

    Examples:
      Move a plate to a new location:

      >>> lh.move_resource(plate, to=Coordinate(100, 100, 100))

    Args:
      resource: The Resource object.
      to: The absolute coordinate (meaning relative to deck) to move the resource to.
      resource_offset: The offset from the resource's origin, optional (rarely necessary).
      to_offset: The offset from the location's origin, optional (rarely necessary).
      pickup_distance_from_top: The distance from the top of the resource to pick up from.
      get_direction: The direction from which to pick up the resource.
      put_direction: The direction from which to put down the resource.
    """

    return self.backend.move_resource(Move(
      resource=resource,
      to=to,
      resource_offset=resource_offset,
      to_offset=to_offset,
      pickup_distance_from_top=pickup_distance_from_top,
      get_direction=get_direction,
      put_direction=put_direction),
      **backend_kwargs)

  def move_lid(
    self,
    lid: Lid,
    to: Union[Plate, ResourceStack, Coordinate],
    resource_offset: Coordinate = Coordinate.zero(),
    to_offset: Coordinate = Coordinate.zero(),
    get_direction: Move.Direction = Move.Direction.FRONT,
    put_direction: Move.Direction = Move.Direction.FRONT,
    **backend_kwargs
  ):
    """ Move a lid to a new location.

    A convenience method for :meth:`move_resource`.

    Examples:
      Move a lid to the :class:`~resources.ResourceStack`:

      >>> lh.move_lid(plate.lid, stacking_area)

      Move a lid to the stacking area and back, grabbing it from the left side:

      >>> lh.move_lid(plate.lid, stacking_area, get_direction=Move.Direction.LEFT)
      >>> lh.move_lid(stacking_area.get_top_item(), plate, put_direction=Move.Direction.LEFT)

    Args:
      lid: The lid to move. Can be either a Plate object or a Lid object.
      to: The location to move the lid to, either a plate, ResourceStack or a Coordinate.
      resource_offset: The offset from the resource's origin, optional (rarely necessary).
      to_offset: The offset from the location's origin, optional (rarely necessary).

    Raises:
      ValueError: If the lid is not assigned to a resource.
    """

    if isinstance(to, Plate):
      to_location = to.get_absolute_location()
      to_location = Coordinate(
        x=to_location.x,
        y=to_location.y,
        z=to_location.z  + to.get_size_z() - lid.get_size_z())
    elif isinstance(to, ResourceStack):
      assert to.direction == "z", "Only ResourceStacks with direction 'z' are currently supported"
      to_location = to.get_absolute_location()
      to_location = Coordinate(
        x=to_location.x,
        y=to_location.y,
        z=to_location.z  + to.get_size_z())
    elif isinstance(to, Coordinate):
      to_location = to
    else:
      raise ValueError(f"Cannot move lid to {to}")

    self.move_resource(
      lid,
      to=to_location,
      pickup_distance_from_top=backend_kwargs.pop("pickup_distance_from_top", 5.7),
      resource_offset=resource_offset,
      to_offset=to_offset,
      get_direction=get_direction,
      put_direction=put_direction,
      **backend_kwargs)

    lid.unassign()
    if isinstance(to, Coordinate):
      self.deck.assign_child_resource(lid, location=to_location)
    elif isinstance(to, ResourceStack): # manage its own resources
      to.assign_child_resource(lid)
    else:
      to.assign_child_resource(lid, location=to_location)

  def move_plate(
    self,
    plate: Plate,
    to: Union[ResourceStack, CarrierSite, Resource, Coordinate],
    resource_offset: Coordinate = Coordinate.zero(),
    to_offset: Coordinate = Coordinate.zero(),
    put_direction: Move.Direction = Move.Direction.FRONT,
    get_direction: Move.Direction = Move.Direction.FRONT,
    **backend_kwargs
  ):
    """ Move a plate to a new location.

    A convenience method for :meth:`move_resource`.

    Examples:
      Move a plate to into a carrier spot:

      >>> lh.move_plate(plate, plt_car[1])

      Move a plate to an absolute location:

      >>> lh.move_plate(plate_01, Coordinate(100, 100, 100))

      Move a lid to another carrier spot, grabbing it from the left side:

      >>> lh.move_plate(plate, plt_car[1], get_direction=Move.Direction.LEFT)
      >>> lh.move_plate(plate, plt_car[0], put_direction=Move.Direction.LEFT)

    Args:
      plate: The plate to move. Can be either a Plate object or a CarrierSite object.
      to: The location to move the plate to, either a plate, CarrierSite or a Coordinate.
      resource_offset: The offset from the resource's origin, optional (rarely necessary).
      to_offset: The offset from the location's origin, optional (rarely necessary).
    """

    if isinstance(to, ResourceStack):
      assert to.direction == "z", "Only ResourceStacks with direction 'z' are currently supported"
      to_location = to.get_absolute_location()
      to_location = Coordinate(
        x=to_location.x,
        y=to_location.y,
        z=to_location.z  + to.get_size_z())
    elif isinstance(to, Coordinate):
      to_location = to
    else:
      to_location = to.get_absolute_location()

    self.move_resource(
      plate,
      to=to_location,
      pickup_distance_from_top=backend_kwargs.pop("pickup_distance_from_top", 13.2),
      resource_offset=resource_offset,
      to_offset=to_offset,
      get_direction=get_direction,
      put_direction=put_direction,
      **backend_kwargs)

    plate.unassign()
    if isinstance(to, Coordinate):
      self.deck.assign_child_resource(plate, location=to_location)
    elif isinstance(to, CarrierSite): # .zero() resources
      to.assign_child_resource(plate, location=Coordinate.zero())
    elif isinstance(to, (ResourceStack, PlateReader)): # manage its own resources
      to.assign_child_resource(plate)
    else:
      to.assign_child_resource(plate, location=to_location)
