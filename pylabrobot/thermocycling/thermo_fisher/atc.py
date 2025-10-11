from pylabrobot.thermocycling.thermo_fisher.thermo_fisher_thermocycler import (
  ThermoFisherThermocyclerBackend,
)


class ATCBackend(ThermoFisherThermocyclerBackend):
  async def close_lid(self):
    if self.bid != "31":
      raise NotImplementedError("Lid control is only available for BID 31 (ATC)")
    res = await self.send_command({"cmd": "lidclose"}, response_timeout=20, read_once=False)
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to close lid")

  async def open_lid(self):
    if self.bid != "31":
      raise NotImplementedError("Lid control is only available for BID 31 (ATC)")
    res = await self.send_command({"cmd": "lidopen"}, response_timeout=20, read_once=False)
    if self._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to open lid")
