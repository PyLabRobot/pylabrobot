"""Resolving labware to the format an instrument works it as.

The match is made on the resource's own geometry and never on a nearest fit, so what is tested is
both what resolves and what deliberately refuses to.
"""

from __future__ import annotations

import pytest

from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_type import PlateType
from pylabrobot.agilent.biotek.lhc.plate_geometry import (
  DEEP_WELL_DEPTH,
  SELECTION_ONLY,
  find,
  plates_for,
  resolve,
)
from pylabrobot.agilent.biotek.lhc.tests.helpers import make_plate

EVERY_FAMILY = list(InstrumentFamily)


class TestTheOfferedPlates:
  """Which formats each model works, and at what heights."""

  @pytest.mark.parametrize("family", EVERY_FAMILY)
  def test_every_model_offers_something(self, family: InstrumentFamily):
    """Every model offers something."""
    offered = plates_for(family)
    assert offered
    assert len({record.plate_type for record in offered}) == len(offered)

  def test_a_dispenser_only_model_works_a_plate_at_one_height(self):
    """It has no wash manifold, so its two dispensing heights are the same number."""
    for record in plates_for(InstrumentFamily.MULTIFLO_FX):
      assert record.dispenser_height == record.manifold_dispense_height

  def test_a_model_with_a_wash_manifold_measures_two_heights(self):
    """A model with a wash manifold measures two heights."""
    record = find(PlateType.PLATE_96_WELL, InstrumentFamily.EL406)
    assert record is not None
    assert record.dispenser_height != record.manifold_dispense_height

  def test_a_format_a_model_does_not_offer_is_not_found(self):
    """A format a model does not offer is not found."""
    assert find(PlateType.PLATE_6_WELL, InstrumentFamily.EL406) is None

  def test_a_record_knows_how_many_wells_it_has(self):
    """A record knows how many wells it has."""
    record = find(PlateType.PLATE_384_WELL, InstrumentFamily.EL406)
    assert record is not None
    assert record.wells == 384
    assert record.columns * record.rows == record.wells


class TestResolvingLabware:
  """Turning a resource into one of those formats."""

  @pytest.mark.parametrize(
    "wells, plate_type",
    [
      (96, PlateType.PLATE_96_WELL),
      (384, PlateType.PLATE_384_WELL),
      (1536, PlateType.PLATE_1536_WELL),
    ],
  )
  def test_columns_and_rows_decide_the_format(self, wells: int, plate_type: PlateType):
    """Columns and rows decide the format."""
    assert resolve(make_plate(wells), InstrumentFamily.EL406).plate_type is plate_type

  def test_well_depth_separates_a_deep_well_plate_from_a_standard_one(self):
    """The one place a threshold decides anything, and only between two formats that differ in
    nothing else."""
    family = InstrumentFamily.MULTIFLO
    shallow = resolve(make_plate(96, well_depth=DEEP_WELL_DEPTH - 5), family)
    deep = resolve(make_plate(96, well_depth=DEEP_WELL_DEPTH + 5), family)
    assert shallow.plate_type is PlateType.PLATE_96_WELL
    assert deep.plate_type is PlateType.PLATE_96_DEEP_WELL

  def test_a_format_can_be_named_instead_of_resolved(self):
    """A format can be named instead of resolved."""
    resolved = resolve(
      make_plate(384), InstrumentFamily.EL406, plate_type=PlateType.PLATE_384_WELL_PCR
    )
    assert resolved.plate_type is PlateType.PLATE_384_WELL_PCR

  def test_a_named_format_the_model_does_not_offer_is_refused(self):
    """A named format the model does not offer is refused."""
    with pytest.raises(ValueError, match="does not offer"):
      resolve(make_plate(96), InstrumentFamily.EL406, plate_type=PlateType.PLATE_6_WELL)

  def test_labware_no_format_matches_is_refused_naming_what_is_offered(self):
    """Labware no format matches is refused naming what is offered."""
    with pytest.raises(ValueError, match="works no 3x2 plate"):
      resolve(make_plate(6), InstrumentFamily.EL406)

  def test_the_formats_that_share_a_shape_are_only_ever_named(self):
    """Each of them differs from an ordinary plate in something a resource does not carry -- a well
    shape, a flange, a tube -- so resolving one would be a guess."""
    assert PlateType.PLATE_96_HALF_WELL in SELECTION_ONLY
    assert PlateType.PLATE_384_WELL_PCR in SELECTION_ONLY
    assert PlateType.PLATE_1536_FLANGE in SELECTION_ONLY
    assert PlateType.PLATE_96_WELL not in SELECTION_ONLY

  def test_the_same_labware_is_worked_at_different_heights_by_different_models(self):
    """The format is the same; the heights it is worked at are the model's own."""
    plate = make_plate(96)
    washer = resolve(plate, InstrumentFamily.EL406)
    dispenser = resolve(plate, InstrumentFamily.MULTIFLO_FX)
    assert washer.plate_type is dispenser.plate_type
    assert washer.manifold_aspirate_height != dispenser.manifold_aspirate_height
