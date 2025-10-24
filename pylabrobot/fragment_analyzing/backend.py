""" Abstract base class for fragment analyzer backends. """

from abc import ABC, abstractmethod


class FragmentAnalyzerBackend(ABC):
  """ Abstract base class for fragment analyzer backends. """

  @abstractmethod
  async def setup(self):
    """ Set up the backend. """

  @abstractmethod
  async def stop(self):
    """ Stop the backend. """

  @abstractmethod
  async def get_status(self) -> str:
    """ Get the status of the fragment analyzer. """

  @abstractmethod
  async def tray_out(self, tray_number: int = 5):
    """ Push a tray out. """

  @abstractmethod
  async def store_capillary(self):
    """ Move the Capillary Storage Solution tray to the capillary array. """


  @abstractmethod
  async def run_method(self, method_name: str):
    """ Run a specified Fragment Analyzer separation method. """

  @abstractmethod
  async def abort(self):
    """ Abort a run. """
