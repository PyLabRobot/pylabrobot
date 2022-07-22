""" VENUS backend runs through the VENUS program (Windows only) """

import re
import typing

from pyhamilton.liquid_handling.backends.backend import LiquidHandlerBackend
from pyhamilton.liquid_handling.resources.abstract import Resource
from pyhamilton.liquid_handling.liquid_handler import AspirationInfo, DispenseInfo

from .deckresource import (
  DeckResource,
  LayoutManager,
  ResourceType,
  Plate24,
  Plate96,
  Plate384,
  Tip96
)
from .interface import *

from pyhamilton.utils.positions import string_to_position

import pyhamilton.utils.file_parsing as file_parser

from . import venus_utils


class VENUS(LiquidHandlerBackend):
  """ Backend for the VENUS control software.

  This class servers as a wrapper for the "old" pyhamilton library, which is still available for
  backwards compatibility, to make it compatible with the new `LiquidHandlerBackend` and
  `LiquidHandler` classes.
  """

  def __init__(self, layout_file: str):
    """ Initializes the backend.
    
    When `setup()` is called, the backend will load the resources from the given layout file and
    assign them automatically using the name they have in the lay file.
    """

    super().__init__()
    self.layout_file = layout_file
    self.lmgr = LayoutManager(layout_file)

    self._venus_resources = {}

  def setup(self):
    super().setup()

    self._load_resources_from_layfile()

    self.ham_int = HamiltonInterface()
    self.ham_int.start()
    self.ham_int.wait_on_response(self.ham_int.send_command(INITIALIZE))

  def _load_resources_from_layfile(self) -> typing.List[Resource]:
    """ Loads the resources from the given layfile.  """

    with open(self.layout_file, "r", encoding="ISO-8859-1") as f:
      c = f.read()

      # Loop through all the resources in the layfile, and create a DeckResource for the ones that
      # can automatically be parsed.
      num_items = file_parser.find_int("Labware.Cnt", c)
      for i in range(1, num_items+1):
        resource_template = file_parser.find_string(f"Labware.{i}.File", c)
        name = file_parser.find_string(f"Labware.{i}.Id", c)

        # use the name as a proxy to the type of labware
        if re.search(r"Cos_[\S]+\.rck", resource_template):
          if "24" in resource_template:
            resource_type = Plate24
          elif "96" in resource_template:
            resource_type = Plate96
          elif "384" in resource_template:
            resource_type = Plate384
          else:
            raise ValueError(f"Unknown plate type: {name}")

          resource = ResourceType(resource_type, name)
        elif re.search(r"([LHS]|([45]ml))TF?(_[LP])?\.rck", resource_template):
          resource = ResourceType(Tip96, name)
        else:
          print("Unknown resource type:", resource_template, "for", name)
          continue

        self._venus_resources[name] = self.lmgr.assign_unused_resource(resource)
        print("Assigned resource:", name)
  
  def assign_unused_resource(self, name: str, resource_type: ResourceType):
    """ Assign a resource using the layout manager.
    
    While many resources are automatically assigned, some are not. This method allows you to assign
    resources that were not automatically assigned.
    """

    self._venus_resources[name] = self.lmgr.assign_unused_resource(resource_type)

  def stop(self):
    self.ham_int.stop()
    super().stop()

  def assigned_resource_callback(self, resource: Resource):
    raise RuntimeError("VENUS backend does not support assigning resources.")

  def unassigned_resource_callback(self, name: str):
    raise RuntimeError("VENUS backend does not support assigning resources.")

  def _get_venus_resource(self, resource: typing.Union[str, Resource]) -> DeckResource:
    name = resource if isinstance(resource, str) else resource.name
    return self._venus_resources[resource]

  def pickup_tips(
    self,
    resource: typing.Union[Resource, str],
    channel_1: typing.Optional[str] = None,
    channel_2: typing.Optional[str] = None,
    channel_3: typing.Optional[str] = None,
    channel_4: typing.Optional[str] = None,
    channel_5: typing.Optional[str] = None,
    channel_6: typing.Optional[str] = None,
    channel_7: typing.Optional[str] = None,
    channel_8: typing.Optional[str] = None,
    **backend_kwargs
  ):
    """ Pick up tips from the specified resource. """

    venus_resource = self._get_venus_resource(resource)
    pos_tuples = []
    last_column = None

    for channel in [channel_1, channel_2, channel_3, channel_4,
                    channel_5, channel_6, channel_7, channel_8]:
      if channel is not None:
        column = string_to_position(channel_1)[1]
        if last_column and column < last_column:
          raise ValueError("With this backend, tips must be picked up in ascending column order.")
        pos_tuples.append((venus_resource, column))
      else:
        pos_tuples.append(None)

    venus_utils.tip_pick_up(self.ham_int, pos_tuples, **backend_kwargs)

  def discard_tips(
    self,
    resource: typing.Union[Resource, str],
    channel_1: typing.Optional[str] = None,
    channel_2: typing.Optional[str] = None,
    channel_3: typing.Optional[str] = None,
    channel_4: typing.Optional[str] = None,
    channel_5: typing.Optional[str] = None,
    channel_6: typing.Optional[str] = None,
    channel_7: typing.Optional[str] = None,
    channel_8: typing.Optional[str] = None,
    **backend_kwargs
  ):
    """ Discard tips from the specified resource. """
    venus_resource = self._get_venus_resource(resource)
    pos_tuples = []
    last_column = None

    for channel in [channel_1, channel_2, channel_3, channel_4,
                    channel_5, channel_6, channel_7, channel_8]:
      if channel is not None:
        column = string_to_position(channel_1)[1]
        if last_column and column < last_column:
          raise ValueError("With this backend, tips must be picked up in ascending column order.")
        pos_tuples.append((venus_resource, column))
      else:
        pos_tuples.append(None)

    venus_utils.tip_eject(self.ham_int, pos_tuples, **backend_kwargs)

  def aspirate(
    self,
    resource: typing.Union[Resource, str],
    channel_1: typing.Optional[AspirationInfo] = None,
    channel_2: typing.Optional[AspirationInfo] = None,
    channel_3: typing.Optional[AspirationInfo] = None,
    channel_4: typing.Optional[AspirationInfo] = None,
    channel_5: typing.Optional[AspirationInfo] = None,
    channel_6: typing.Optional[AspirationInfo] = None,
    channel_7: typing.Optional[AspirationInfo] = None,
    channel_8: typing.Optional[AspirationInfo] = None,
    **backend_kwargs
  ):
    """ Aspirate liquid from the specified resource using pip. """
    venus_resource = self._get_venus_resource(resource)
    pos_tuples = []
    volumes = []
    last_column = None

    for channel in [channel_1, channel_2, channel_3, channel_4,
                    channel_5, channel_6, channel_7, channel_8]:
      if channel is not None:
        column = string_to_position(channel_1.position)[1]
        if last_column and column < last_column:
          raise ValueError("With this backend, tips must be picked up in ascending column order.")
        pos_tuples.append((venus_resource, column))
        volumes.append(channel.volume)
      else:
        pos_tuples.append(None)
        volumes.append(None)

    venus_utils.aspirate(self.ham_int, pos_tuples, volumes, **backend_kwargs)

  def dispense(
    self,
    resource: typing.Union[Resource, str],
    channel_1: typing.Optional[DispenseInfo] = None,
    channel_2: typing.Optional[DispenseInfo] = None,
    channel_3: typing.Optional[DispenseInfo] = None,
    channel_4: typing.Optional[DispenseInfo] = None,
    channel_5: typing.Optional[DispenseInfo] = None,
    channel_6: typing.Optional[DispenseInfo] = None,
    channel_7: typing.Optional[DispenseInfo] = None,
    channel_8: typing.Optional[DispenseInfo] = None,
    **backend_kwargs
  ):
    """ Dispense liquid from the specified resource using pip. """
    venus_resource = self._get_venus_resource(resource)
    pos_tuples = []
    volumes = []
    last_column = None

    for channel in [channel_1, channel_2, channel_3, channel_4,
                    channel_5, channel_6, channel_7, channel_8]:
      if channel is not None:
        column = string_to_position(channel_1.position)[1]
        if last_column and column < last_column:
          raise ValueError("With this backend, tips must be picked up in ascending column order.")
        pos_tuples.append((venus_resource, column))
        volumes.append(channel.volume)
      else:
        pos_tuples.append(None)
        volumes.append(None)

    venus_utils.dispense(self.ham_int, pos_tuples, volumes, **backend_kwargs)

  def pickup_tips96(self, resource, **backend_kwargs):
    """ Pick up tips from the specified resource using CoRe 96. """
    venus_resource = self._get_venus_resource(resource)
    venus_utils.tip_pick_up_96(self.ham_int, venus_resource, **backend_kwargs)

  def discard_tips96(self, resource, **backend_kwargs):
    """ Discard tips to the specified resource using CoRe 96. """
    venus_resource = self._get_venus_resource(resource)
    venus_utils.tip_eject_96(self.ham_int, venus_resource, **backend_kwargs)

  def aspirate96(self, resource, pattern, volume, **backend_kwargs):
    """ Aspirate liquid from the specified resource using CoRe 96. """
    venus_resource = self._get_venus_resource(resource)
    venus_utils.aspirate_96(self.ham_int, venus_resource, volume, **backend_kwargs)

  def dispense96(self, resource, pattern, volume, **backend_kwargs):
    """ Dispense liquid to the specified resource using CoRe 96. """
    venus_resource = self._get_venus_resource(resource)
    venus_utils.dispense_96(self.ham_int, venus_resource, volume, **backend_kwargs)
