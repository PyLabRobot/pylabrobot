from abc import ABCMeta, abstractmethod


class LiquidHandlerBackend(object, metaclass=ABCMeta):
  """
  Abstract base class for liquid handling robot backends.
  """

  @abstractmethod
  def __init__(self):
    pass

  @abstractmethod
  def setup(self):
    pass

  @abstractmethod
  def stop(self):
    pass

  def __enter__(self):
    self.setup()
    return self

  def __exit__(self, *exc):
    self.stop()
    return False
