"""SoftMax filter-slide catalog support for FilterMax readers."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from .errors import (
  FilterMaxAmbiguousFilterError,
  FilterMaxCatalogError,
  FilterMaxFilterNotFoundError,
)
from .models import FilterDefinition, FilterKind, FilterSelection, FilterTechnique

_TECHNIQUE_BITS: Tuple[Tuple[int, FilterTechnique], ...] = (
  (1, "absorbance"),
  (2, "fluorescence"),
  (4, "fluorescence_polarization"),
  (8, "time_resolved_fluorescence"),
  (16, "luminescence"),
)


def _local_name(tag: str) -> str:
  """Remove an XML namespace from an element name."""
  return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element) -> Dict[str, str]:
  """Read trimmed text fields from one catalog record."""
  return {_local_name(child.tag): (child.text or "").strip() for child in element}


@dataclass(frozen=True)
class FilterSlideCatalog:
  """Filter definitions indexed by slide, position, wavelength, and technique."""

  filters: Tuple[FilterDefinition, ...]

  @classmethod
  def from_softmax_xml(cls, path: Union[str, Path]) -> "FilterSlideCatalog":
    """Load a SoftMax XML export locally, without communicating with the reader."""
    root = ET.parse(path).getroot()
    slides: Dict[int, Tuple[int, str, FilterKind]] = {}
    for element in root:
      if _local_name(element.tag) != "MeasFilterSlider":
        continue
      values = _children(element)
      try:
        index = int(values["Index"])
        slide_id = int(values["ID"])
        name = values.get("Name", str(slide_id))
        kind: FilterKind = "excitation" if int(values["Type"]) == 1 else "emission"
      except (KeyError, ValueError) as exc:
        raise FilterMaxCatalogError("Malformed MeasFilterSlider entry") from exc
      if index in slides:
        raise FilterMaxCatalogError(f"Duplicate filter-slide index {index}")
      slides[index] = (slide_id, name, kind)

    parsed: List[FilterDefinition] = []
    seen_positions = set()
    for element in root:
      if _local_name(element.tag) != "Filter":
        continue
      values = _children(element)
      try:
        slide_index = int(values["Index_Slider"])
        slide_id, slide_name, kind = slides[slide_index]
        position = int(values["Position"])
        apply_to = int(values["ApplyTo"])
        wavelength = int(values["WL"])
        bandwidth = int(values["Bandwidth"])
      except (KeyError, ValueError) as exc:
        raise FilterMaxCatalogError("Malformed Filter entry") from exc
      key = (kind, slide_id, position)
      if key in seen_positions:
        raise FilterMaxCatalogError(f"Duplicate {kind} slide {slide_id} position {position}")
      seen_positions.add(key)
      techniques = frozenset(name for bit, name in _TECHNIQUE_BITS if apply_to & bit)
      parsed.append(
        FilterDefinition(
          kind=kind,
          slide_id=slide_id,
          slide_name=slide_name,
          position=position,
          wavelength=wavelength,
          bandwidth=bandwidth,
          apply_to=apply_to,
          techniques=techniques,
          molecular_devices_number=values.get("MDNumber") or None,
        )
      )
    if not parsed:
      raise FilterMaxCatalogError("The SoftMax XML contains no filter definitions")
    return cls(tuple(parsed))

  def filters_for_slide(self, kind: FilterKind, slide_id: int) -> Tuple[FilterDefinition, ...]:
    """Return the selected slide's definitions in one-based slot order."""
    return tuple(
      sorted(
        (item for item in self.filters if item.kind == kind and item.slide_id == slide_id),
        key=lambda item: item.position,
      )
    )

  def resolve(
    self,
    *,
    kind: FilterKind,
    technique: FilterTechnique,
    wavelength: int,
    slide_id: Optional[int] = None,
    position: Optional[int] = None,
  ) -> FilterSelection:
    """Resolve a wavelength in nm to one compatible slide and one-based slot.

    Ambiguous matches require a slide ID or position; absent matches raise
    FilterMaxFilterNotFoundError. Resolution does not query installed hardware.
    """
    matches = [
      item
      for item in self.filters
      if item.kind == kind
      and technique in item.techniques
      and item.wavelength == wavelength
      and (slide_id is None or item.slide_id == slide_id)
      and (position is None or item.position == position)
    ]
    if not matches:
      raise FilterMaxFilterNotFoundError(f"No {kind} filter for {technique} at {wavelength} nm")
    if len(matches) != 1:
      locations = ", ".join(f"slide {item.slide_id}/slot {item.position}" for item in matches)
      raise FilterMaxAmbiguousFilterError(
        f"{kind.title()} {wavelength} nm is ambiguous ({locations}); select slide and slot"
      )
    item = matches[0]
    return FilterSelection(
      kind=item.kind,
      slide_id=item.slide_id,
      position=item.position,
      wavelength=item.wavelength,
      bandwidth=item.bandwidth,
    )
