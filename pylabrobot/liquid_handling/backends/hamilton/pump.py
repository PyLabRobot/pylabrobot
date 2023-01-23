from pylabrobot.resources import Coordinate, Resource
from pylabrobot.liquid_handling.backends.hamilton.STAR import STAR


class Pump(Resource):
  """ Pump is the washer. Will rename later. """

  def __init__(self, backend: STAR, name="pump"):
    super().__init__(name, size_x=1, size_y=1, size_z=1, category="pump")
    self.backend = backend

    # assign two chambers to the pump
    self.chamber_1 = Resource("chamber_1", size_x=121, size_y=85, size_z=2, category="chamber")
    self.assign_child_resource(self.chamber_1, location=Coordinate(18.05, 371.500-63, 99))
    self.chamber_2 = Resource("chamber_2", size_x=121, size_y=85, size_z=2, category="chamber")
    self.assign_child_resource(self.chamber_2, location=Coordinate(18.05, 241.500-63, 100))

  async def refill(self):
    await self.backend.drain_dual_chamber_system(pump_station=1)
    await self.backend.fill_selected_dual_chamber(pump_station=1, drain_before_refill=False,
      wash_fluid=2, chamber=1, waste_chamber_suck_time_after_sensor_change=0)
    await self.backend.fill_selected_dual_chamber(pump_station=1, drain_before_refill=False,
      wash_fluid=1, chamber=2, waste_chamber_suck_time_after_sensor_change=0)
