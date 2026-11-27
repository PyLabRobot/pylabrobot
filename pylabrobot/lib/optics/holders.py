"""How a device holds its filters: per side on slides, each filter chosen on its own, or as a set
in a cube, its excitation filter, dichroic and emission filter chosen together."""

from dataclasses import dataclass
from typing import Dict, Generic, Optional, Type, TypeVar

from .filters import DichroicFilter, OpticalFilter, _FilterBase

F = TypeVar("F", bound=_FilterBase)


class FilterSlide(Generic[F]):
  """The filters a device holds on one side, by slot.

  Attributes:
    maximum_slots: how many positions the side has.
  """

  _CATEGORY: str = ""
  _FILTER_CLASS: Type[F]

  def __init__(self, maximum_slots: int) -> None:
    """
    Args:
      maximum_slots: how many positions the side has, as the device reports or documents it.

    Raises:
      ValueError: if `maximum_slots` is below 1.
    """
    if maximum_slots < 1:
      raise ValueError(f"maximum_slots must be at least 1, got {maximum_slots}")
    self.maximum_slots = maximum_slots
    self._by_slot: Dict[int, F] = {}
    self._by_name: Dict[str, F] = {}

  def register(self, optical_filter: F) -> None:
    """Hold `optical_filter` at its slot, replacing the filter there.

    Raises:
      ValueError: if its slot is outside 1 to `maximum_slots`.
    """
    if not 1 <= optical_filter.slot <= self.maximum_slots:
      raise ValueError(
        f"{self._CATEGORY} filter slot {optical_filter.slot} out of range "
        f"(the device has {self.maximum_slots} {self._CATEGORY} filter slots)"
      )
    replaced = self._by_slot.get(optical_filter.slot)
    if replaced is not None and replaced.name:
      del self._by_name[replaced.name]
    self._by_slot[optical_filter.slot] = optical_filter
    if optical_filter.name:
      self._by_name[optical_filter.name] = optical_filter

  def __getitem__(self, slot: int) -> F:
    """The filter at `slot`, or an anonymous one when none is registered there."""
    if slot in self._by_slot:
      return self._by_slot[slot]
    return self._FILTER_CLASS(slot=slot)

  def by_name(self, name: str) -> F:
    """The registered filter called `name`.

    Raises:
      KeyError: if no registered filter has that name.
    """
    if name not in self._by_name:
      raise KeyError(
        f"no {self._CATEGORY} filter registered as {name!r}; registered: {list(self._by_name)}"
      )
    return self._by_name[name]

  def __repr__(self) -> str:
    held = [f"{slot}: {f.name or '(unnamed)'}" for slot, f in sorted(self._by_slot.items())]
    return f"{type(self).__name__}({self.maximum_slots} slots, {{{', '.join(held)}}})"


class ExcitationFilterSlide(FilterSlide[OpticalFilter]):
  """The excitation filters a device holds, by slot."""

  _CATEGORY = "excitation"
  _FILTER_CLASS = OpticalFilter


class EmissionFilterSlide(FilterSlide[OpticalFilter]):
  """The emission filters a device holds, by slot."""

  _CATEGORY = "emission"
  _FILTER_CLASS = OpticalFilter


class DichroicFilterSlide(FilterSlide[DichroicFilter]):
  """The dichroic filters a device holds, by slot."""

  _CATEGORY = "dichroic"
  _FILTER_CLASS = DichroicFilter


@dataclass(frozen=True)
class FilterCube:
  """A matched set of excitation filter, dichroic and emission filter, held and chosen together
  (a multi-band set's excitation passbands lie in its dichroic's reflection bands).

  Attributes:
    name: what the set is called ("GFP").
    excitation: its excitation filter, or None.
    dichroic: its dichroic, or None.
    emission: its emission filter, or None.
  """

  name: str
  excitation: Optional[OpticalFilter] = None
  dichroic: Optional[DichroicFilter] = None
  emission: Optional[OpticalFilter] = None
