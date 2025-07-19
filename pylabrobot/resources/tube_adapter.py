'''
PlateAdapter only accepts plates.
But some tube racks are plate-like.
TubeRackAdapter allows tube racks to be loaded into PlateHolders.
A thin wrapper around PlateAdapter that relaxes the child-type restriction
from Plate-only to TubeRack.
'''



from pylabrobot.resources.plate_adapter import PlateAdapter
from pylabrobot.resources.tube_rack     import TubeRack
from pylabrobot.resources.resource       import Coordinate

class TubeRackAdapter(PlateAdapter):
    """ANSI/SBS frame that accepts a TubeRack instead of a Plate."""

    def assign_child_resource(self, resource, location=None, reassign=True):
        if not isinstance(resource, TubeRack):
            raise TypeError("TubeRackAdapter can only hold TubeRack resources")

        # ----- stripped-down copy of PlateAdapter logic (minus the Plate check) -----
        if self._child_resource is not None and not reassign:
            raise ValueError(f"{self.name} already has a child resource assigned")

        # auto-place into the adapter hole if caller didn't give a location
        if location is None:
            location = Coordinate(self.dx, self.dy, self.dz)

        resource.location = location
        resource.parent   = self
        self._child_resource = resource
    def __getitem__(self, key):
        """Delegate bracket-lookup (e.g. ['A1']) to the child TubeRack."""
        return self._child_resource[key]          # _child_resource *is* the TubeRack

    def __getattr__(self, name):
        """Fallback: if the adapter doesn't have the attribute, ask the rack."""
        try:
            return super().__getattribute__(name)
        except AttributeError:
            return getattr(self._child_resource, name)