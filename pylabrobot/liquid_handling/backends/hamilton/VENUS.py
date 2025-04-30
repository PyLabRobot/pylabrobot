


# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
# WILL BE FIXED WHEN PYHAMILTON IS UPDATED WITH DYNAMIC RESOURCE MODEL
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!



# # type: ignore

# """ VENUS backend runs through the VENUS program (Windows only) """

# import logging
# import re
# from typing import List

# try:
#   from pyhamilton.deckresource import (
#     DeckResource,
#     LayoutManager,
#     ResourceType,
#     Plate24,
#     Plate96,
#     Plate384,
#     Tip96
#   )
#   from pyhamilton.interface import (
#     HamiltonInterface,
#     INITIALIZE,
#   )
#   from . import venus_utils
#   USE_VENUS = True
# except (ImportError, ModuleNotFoundError):
#   USE_VENUS = False

# from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
# from pylabrobot.resources import Resource, TipRack
# import pylabrobot.utils.file_parsing as file_parser
# from pylabrobot.liquid_handling.standard import (
#   Pickup,
#   Drop,
#   Aspiration,
#   Dispense,
# )

# logger = logging.getLogger("pylabrobot")
# logging.basicConfig()
# logger.setLevel(logging.INFO)


# class VENUS(LiquidHandlerBackend):
#   """ Backend for the VENUS control software.

#   This class servers as a wrapper for the "old" pyhamilton library, which is still available for
#   backwards compatibility, to make it compatible with the new `LiquidHandlerBackend` and
#   `LiquidHandler` classes.
#   """

#   def __init__(self, layout_file: str):
#     """ Initializes the backend.

#     When `setup()` is called, the backend will load the resources from the given layout file and
#     assign them automatically using the name they have in the lay file.
#     """

#     super().__init__()
#     self.layout_file = layout_file
#     self.lmgr = LayoutManager(layout_file)

#     self._venus_resources: dict[str, ResourceType] = {}

#   async def setup(self):
#     if not USE_VENUS:
#       raise RuntimeError("Venus backend requires the pyhamilton library.")

#     super().setup()

#     self._load_resources_from_layfile()

#     self.ham_int = HamiltonInterface()
#     self.ham_int.start()
#     self.ham_int.wait_on_response(self.ham_int.send_command(INITIALIZE))

#   def _load_resources_from_layfile(self) -> List[Resource]:
#     """ Loads the resources from the given layfile.  """

#     with open(self.layout_file, "r", encoding="ISO-8859-1") as f:
#       c = f.read()

#       # Loop through all the resources in the layfile, and create a DeckResource for the ones that
#       # can automatically be parsed.
#       num_items = file_parser.find_int("Labware.Cnt", c)
#       for i in range(1, num_items+1):
#         resource_template = file_parser.find_string(f"Labware.{i}.File", c)
#         name = file_parser.find_string(f"Labware.{i}.Id", c)

#         # use the name as a proxy to the type of labware
#         if re.search(r"Cos_[\S]+\.rck", resource_template):
#           if "24" in resource_template:
#             resource_type = Plate24
#           elif "96" in resource_template:
#             resource_type = Plate96
#           elif "384" in resource_template:
#             resource_type = Plate384
#           else:
#             raise ValueError(f"Unknown plate type: {name}")

#           resource = ResourceType(resource_type, name)
#         elif re.search(r"([LHS]|([45]ml))TF?(_[LP])?\.rck", resource_template):
#           resource = ResourceType(Tip96, name)
#         else:
#           print("Unknown resource type:", resource_template, "for", name)
#           continue

#         self._venus_resources[name] = self.lmgr.assign_unused_resource(resource)
#         print("Assigned resource:", name)

#   def assign_unused_resource(self, name: str, resource_type: ResourceType):
#     """ Assign a resource using the layout manager.

#    While many resources are automatically assigned, some are not. This method allows you to assign
#     resources that were not automatically assigned.
#     """

#     self._venus_resources[name] = self.lmgr.assign_unused_resource(resource_type)

#   async def stop(self):
#     self.ham_int.stop()
#     super().stop()

#   async def assigned_resource_callback(self, resource: Resource):
#     raise RuntimeError("VENUS backend does not support assigning resources.")

#   async def unassigned_resource_callback(self, name: str):
#     raise RuntimeError("VENUS backend does not support assigning resources.")

#   def _get_venus_resource(self, resource: Resource) -> DeckResource:
#     return self._venus_resources[resource.name]

#   def pick_up_tips(self, ops: List[Pickup], **backend_kwargs):
#     """ Pick up tips from the specified resource. """

#     pos_tuples = []
#     last_column = None

#     for channel in channels:
#       if channel is not None:
#         venus_resource = self._get_venus_resource(channel.resource)
#         tip_rack = channel.resource.parent
#         resource_idx = tip_rack.index_of_item(channel.resource)
#         column = resource_idx // tip_rack.num_items_y
#         if last_column and column < last_column:
#           raise ValueError("With this backend, tips must be picked up in ascending column order.")
#         pos_tuples.append((venus_resource, column))
#       else:
#         pos_tuples.append(None)

#     venus_utils.tip_pick_up(self.ham_int, pos_tuples, **backend_kwargs)

#   def drop_tips(self, ops: List[Drop], **backend_kwargs):
#     """ Drop tips from the specified resource. """
#     pos_tuples = []
#     last_column = None

#     for channel in channels:
#       if channel is not None:
#         venus_resource = self._get_venus_resource(channel.resource)
#         tip_rack = channel.resource.parent
#         resource_idx = tip_rack.index_of_item(channel.resource)
#         column = resource_idx // tip_rack.num_items_y
#         if last_column and column < last_column:
#           raise ValueError("With this backend, tips must be droped in ascending column order.")
#         pos_tuples.append((venus_resource, column))
#       else:
#         pos_tuples.append(None)

#     venus_utils.tip_eject(self.ham_int, pos_tuples, **backend_kwargs)

#   def aspirate(self, ops: List[Aspiration], **backend_kwargs):
#     """ Aspirate liquid from the specified resource using pip. """
#     pos_tuples = []
#     volumes = []
#     last_column = None

#     for op in ops:
#       venus_resource = self._get_venus_resource(channel.resource)
#       tip_rack = channel.resource.parent
#       resource_idx = tip_rack.index_of_item(channel.resource)
#       column = resource_idx // tip_rack.num_items_y
#       if last_column and column < last_column:
#         raise ValueError("With this backend, aspirations must be in ascending column order.")
#       pos_tuples.append((venus_resource, column))
#       volumes.append(channel.volume)

#     # get first non-None channel
#     channel = next(c for c in channels if c is not None)
#     liquid_height = channel.offset.z # can only get one in this backend

#     venus_utils.aspirate(self.ham_int, pos_tuples, volumes, liquidHeight=liquid_height,
#       **backend_kwargs)

#   def dispense(self, ops: List[Dispense], **backend_kwargs):
#     """ Dispense liquid from the specified resource using pip. """
#     pos_tuples = []
#     volumes = []
#     last_column = None

#     for channel in channels:
#       if channel is not None:
#         venus_resource = self._get_venus_resource(channel.resource)
#         tip_rack = channel.resource.parent
#         resource_idx = tip_rack.index_of_item(channel.resource)
#         column = resource_idx // tip_rack.num_items_y
#         if last_column and column < last_column:
#           raise ValueError("With this backend, tips must be picked up in ascending column order.")
#         pos_tuples.append((venus_resource, column))
#         volumes.append(channel.volume)
#       else:
#         pos_tuples.append(None)
#         volumes.append(None)

#     venus_utils.dispense(self.ham_int, pos_tuples, volumes, **backend_kwargs)

#   def pick_up_tips96(self, tip_rack: TipRack, **backend_kwargs):
#     """ Pick up tips from the specified resource using CoRe 96. """
#     venus_resource = self._get_venus_resource(tip_rack)
#     venus_utils.tip_pick_up_96(self.ham_int, venus_resource, **backend_kwargs)

#   def drop_tips96(self, tip_rack: TipRack, **backend_kwargs):
#     """ Drop tips to the specified resource using CoRe 96. """
#     venus_resource = self._get_venus_resource(tip_rack)
#     venus_utils.tip_eject_96(self.ham_int, venus_resource, **backend_kwargs)

#   def aspirate96(self, aspiration: AspirationPlate, **backend_kwargs):
#     """ Aspirate liquid from the specified resource using CoRe 96. """
#     venus_resource = self._get_venus_resource(aspiration.resource)
#     venus_utils.aspirate_96(self.ham_int, venus_resource, aspiration.volume,
#       aspiration_speed=aspiration.flow_rate, **backend_kwargs)

#   def dispense96(self, dispense: DispensePlate, **backend_kwargs):
#     """ Dispense liquid to the specified resource using CoRe 96. """
#     venus_resource = self._get_venus_resource(dispense.resource)
#     venus_utils.dispense_96(self.ham_int, venus_resource, dispense.volume,
#       dispense_speed=dispense.flow_rate, **backend_kwargs)
