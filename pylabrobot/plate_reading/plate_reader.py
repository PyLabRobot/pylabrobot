import asyncio
from typing import List, cast

from pylabrobot.resources import Coordinate, Resource, Plate
from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.utils import run_with_timeout


class NoPlateError(Exception):
  pass


class PlateReader(Resource):
  """ The front end for plate readers. Plate readers are devices that can read luminescence,
  absorbance, or fluorescence from a plate.

  Plate readers are asynchronous, meaning that their methods will return immediately and
  will not block. If you want to use a plate reader in a synchronous context, use SyncPlateReader
  instead.

  Here's an example of how to use this class in a Juptyer Notebook:

  >>> from pylabrobot.plate_reading.clario_star import CLARIOStar
  >>> pr = PlateReader(backend=CLARIOStar())
  >>> pr.setup()
  >>> await pr.read_luminescence()
  [[value1, value2, value3, ...], [value1, value2, value3, ...], ...

  In a synchronous context, use asyncio.run() to run the asynchronous methods:

  >>> import asyncio
  >>> from pylabrobot.plate_reading.clario_star import CLARIOStar
  >>> pr = SyncPlateReader(backend=CLARIOStar())
  >>> pr.setup()
  >>> asyncio.run(pr.read_luminescence())
  [[value1, value2, value3, ...], [value1, value2, value3, ...], ...
  """

  def __init__(self, name: str, backend: PlateReaderBackend) -> None:
    super().__init__(name=name, size_x=0, size_y=0, size_z=0, category="plate_reader")
    self.backend = backend

  def assign_child_resource(self, resource):
    if len(self.children) >= 1:
      raise ValueError("There already is a plate in the plate reader.")
    if not isinstance(resource, Plate):
      raise ValueError("The resource must be a plate.")
    super().assign_child_resource(resource, location=Coordinate.zero())

  def get_plate(self) -> Plate:
    if len(self.children) == 0:
      raise NoPlateError("There is no plate in the plate reader.")
    return cast(Plate, self.children[0])

  async def setup(self) -> None:
    await self.backend.setup()

  async def stop(self) -> None:
    await self.backend.stop()

  async def open(self) -> None:
    await self.backend.open()

  async def close(self) -> None:
    await self.backend.close()

  async def read_luminescence(self) -> List[List[float]]:
    return await self.backend.read_luminescence()


class SyncPlateReader(Resource):
  """ The front end for plate readers. This is a synchronous version of PlateReader, meaning
  that its methods will block until they return.

  Here's an example of how to use this class:

  >>> from pylabrobot.plate_reading.clario_star import CLARIOStar
  >>> pr = SyncPlateReader(backend=CLARIOStar())
  >>> pr.setup()
  >>> pr.read_luminescence()
  [[value1, value2, value3, ...], [value1, value2, value3, ...], ...

  .. warning:: This class cannot be used in a jupyter notebook, until IPython allows the synchronous
    foreground task to submit asyncio tasks and block while waiting. Currently, it does not
    (https://ipython.readthedocs.io/en/stable/interactive/autoawait.html#difference-between-terminal
    -ipython-and-ipykernel). I think this is a bug. Luckily, you can the asynchronous PlateReader
    instead. It's actually pretty nice in a jupyter notebook, because you can use `await` anywhere
    to wait for the plate reader to finish reading.
  """

  def __init__(self, name: str, backend: PlateReaderBackend) -> None:
    super().__init__(name=name, size_x=0, size_y=0, size_z=0, category="plate_reader")

    try:
      _ = get_ipython() # type: ignore
      raise RuntimeError("SyncPlateReader cannot be used in a jupyter notebook. Use PlateReader "
        "instead.")
    except NameError:
      pass

    self.pr = PlateReader(name=name, backend=backend)
    self._loop = asyncio.new_event_loop()
    asyncio.set_event_loop(self._loop)

  def assign_child_resource(self, resource):
    self.pr.assign_child_resource(resource)

  def get_plate(self) -> Plate:
    return self.pr.get_plate()

  def setup(self) -> None:
    run_with_timeout(self.pr.setup(), loop=self._loop)

  def stop(self) -> None:
    run_with_timeout(self.pr.stop(), loop=self._loop)

  def read_luminescence(self, timeout=None) -> List[List[float]]:
    return run_with_timeout(coro=self.pr.read_luminescence(), timeout=timeout, loop=self._loop)
