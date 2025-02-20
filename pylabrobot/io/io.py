from abc import ABC, abstractmethod


class IOBase(ABC):
  @abstractmethod
  def write(self, *args, **kwargs):
    pass

  @abstractmethod
  def read(self, *args, **kwargs):
    pass

  def serialize(self):
    return {}
