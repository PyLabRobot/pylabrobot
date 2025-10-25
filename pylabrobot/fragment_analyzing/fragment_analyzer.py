from pylabrobot.fragment_analyzing.backend import FragmentAnalyzerBackend
from pylabrobot.resources import Resource


class FragmentAnalyzer(Resource):
  """Fragment Analyzer frontend"""

  def __init__(self, name: str, backend: FragmentAnalyzerBackend):
    super().__init__(name=name, size_x=0, size_y=0, size_z=0, category="fragment_analyzer")
    self.backend = backend

  async def setup(self):
    """Set up the fragment analyzer."""
    await self.backend.setup()

  async def stop(self):
    """Stop the fragment analyzer."""
    await self.backend.stop()

  async def get_status(self) -> str:
    """Get the status of the fragment analyzer."""
    return await self.backend.get_status()

  async def tray_out(self, tray_number):
    await self.backend.tray_out(tray_number)

  async def store_capillary(self):
    """Move the Capillary Storage Solution tray to the capillary array."""
    await self.backend.store_capillary()

  async def run_method(self, method_name: str):
    """Run a specified Fragment Analyzer separation method."""
    await self.backend.run_method(method_name)

  async def abort(self):
    """Abort a run."""
    await self.backend.abort()
