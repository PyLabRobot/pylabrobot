""" ML Star tips """

# pylint: skip-file

from pylabrobot.liquid_handling.resources.abstract import TipRack
from pylabrobot.liquid_handling.resources.abstract.itemized_resource import create_equally_spaced
from pylabrobot.liquid_handling.resources.abstract.tiprack import Tip
from .tip_types import (
  low_volume_tip_no_filter,
  low_volume_tip_with_filter,
  standard_volume_tip_no_filter,
  standard_volume_tip_with_filter,
  high_volume_tip_no_filter,
  high_volume_tip_with_filter,
  four_ml_tip_with_filter,
  five_ml_tip
)


#: Tip Rack 24x 4ml Tip with Filter landscape oriented
def FourmlTF_L(name: str) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=7.0,
    tip_type=four_ml_tip_with_filter,
    items=create_equally_spaced(Tip,
      num_items_x=6,
      num_items_y=4,
      dx=16.3,
      dy=14.2,
      dz=-93.2,
      item_size_x=18.0,
      item_size_y=18.0,
      tip_type=four_ml_tip_with_filter,
    ),
  )


#: Rack with 96 10ul Low Volume Tip
def LT_L(name: str) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    tip_type=low_volume_tip_no_filter,
    items=create_equally_spaced(Tip,
      num_items_x=12,
      num_items_y=8,
      dx=11.7,
      dy=9.8,
      dz=-22.5,
      item_size_x=9.0,
      item_size_y=9.0,
      tip_type=low_volume_tip_no_filter,
    ),
  )


#: Rack with 96 1000ul High Volume Tip with filter
def HTF_L(name: str) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    tip_type=high_volume_tip_with_filter,
    items=create_equally_spaced(Tip,
      num_items_x=12,
      num_items_y=8,
      dx=11.7,
      dy=9.8,
      dz=-83.5,
      item_size_x=9.0,
      item_size_y=9.0,
      tip_type=high_volume_tip_with_filter,
    ),
  )


#: Rack with 96 1000ul High Volume Tip
def HT_L(name: str) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    tip_type=high_volume_tip_no_filter,
    items=create_equally_spaced(Tip,
      num_items_x=12,
      num_items_y=8,
      dx=11.7,
      dy=9.8,
      dz=-83.5,
      item_size_x=9.0,
      item_size_y=9.0,
      tip_type=high_volume_tip_no_filter,
    ),
  )


#: Rack with 96 10ul Low Volume Tip with filter
def LTF_L(name: str) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    tip_type=low_volume_tip_with_filter,
    items=create_equally_spaced(Tip,
      num_items_x=12,
      num_items_y=8,
      dx=11.7,
      dy=9.8,
      dz=-22.5,
      item_size_x=9.0,
      item_size_y=9.0,
      tip_type=low_volume_tip_with_filter,
    ),
  )


#: Tip Rack 24x 5ml Tip landscape oriented
def FivemlT_L(name: str) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=7.0,
    tip_type=five_ml_tip,
    items=create_equally_spaced(Tip,
      num_items_x=6,
      num_items_y=4,
      dx=16.3,
      dy=14.2,
      dz=-93.2,
      item_size_x=18.0,
      item_size_y=18.0,
      tip_type=five_ml_tip,
    ),
  )


#: Rack with 96 300ul Standard Volume Tip with filter
def STF_L(name: str) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    tip_type=standard_volume_tip_with_filter,
    items=create_equally_spaced(Tip,
      num_items_x=12,
      num_items_y=8,
      dx=11.7,
      dy=9.8,
      dz=-50.5,
      item_size_x=9.0,
      item_size_y=9.0,
      tip_type=standard_volume_tip_with_filter,
    ),
  )


#: Rack with 96 300ul Standard Volume Tip
def ST_L(name: str) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    tip_type=standard_volume_tip_no_filter,
    items=create_equally_spaced(Tip,
      num_items_x=12,
      num_items_y=8,
      dx=11.7,
      dy=9.8,
      dz=-50.5,
      item_size_x=9.0,
      item_size_y=9.0,
      tip_type=standard_volume_tip_no_filter,
    ),
  )
