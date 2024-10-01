import abc
import dataclasses


@dataclasses.dataclass
class ParameterSet(abc.ABC):
  @abc.abstractmethod
  def make_asp_kwargs(self) -> dict:
    pass

  @abc.abstractmethod
  def make_disp_kwargs(self) -> dict:
    pass
