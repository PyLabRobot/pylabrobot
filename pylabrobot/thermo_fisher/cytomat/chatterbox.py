from pylabrobot.thermo_fisher.cytomat.backend import CytomatBackend


class CytomatChatterbox(CytomatBackend):
  async def setup(self):
    await self.wait_for_task_completion()

  async def stop(self):
    print("closing connection to cytomat")

  async def send_command(self, command_type, command, params):
    print(
      "cytomat", self._assemble_command(command_type=command_type, command=command, params=params)
    )
    if command_type == "ch":
      return "0"
    return "0" * 8

  async def wait_for_transfer_station(self, occupied: bool = False):
    _ = await self.get_overview_register()
