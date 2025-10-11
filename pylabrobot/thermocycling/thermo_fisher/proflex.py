from pylabrobot.thermocycling.thermo_fisher.thermo_fisher_thermocycler import (
  ThermoFisherThermocyclerBackend,
)


class ProflexBackend(ThermoFisherThermocyclerBackend):
  async def open_lid(self):
    raise NotImplementedError("Open lid command is not implemented for Proflex thermocycler")

  async def close_lid(self):
    raise NotImplementedError("Close lid command is not implemented for Proflex thermocycler")
