""" ML Star tips """

from functools import partial

from pylabrobot.liquid_handling.resources.abstract import Tips
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
FourmlTF_L = partial(Tips,
  size_x=122.4,
  size_y=82.6,
  size_z=7.0,
  tip_type=four_ml_tip_with_filter,
  dx=16.3,
  dy=14.2,
  dz=-93.2,
  tip_size_x=18.0,
  tip_size_y=18.0,
  num_items_x=6,
  num_items_y=4
)


#: Rack with 96 10ul Low Volume Tip
LT_L = partial(Tips,
  size_x=122.4,
  size_y=82.6,
  size_z=20.0,
  tip_type=low_volume_tip_no_filter,
  dx=11.7,
  dy=9.8,
  dz=-22.5,
  tip_size_x=9.0,
  tip_size_y=9.0,
  num_items_x=12,
  num_items_y=8
)


#: Rack with 96 1000ul High Volume Tip with filter
HTF_L = partial(Tips,
  size_x=122.4,
  size_y=82.6,
  size_z=20.0,
  tip_type=high_volume_tip_with_filter,
  dx=11.7,
  dy=9.8,
  dz=-83.5,
  tip_size_x=9.0,
  tip_size_y=9.0,
  num_items_x=12,
  num_items_y=8
)


#: Rack with 96 1000ul High Volume Tip
HT_L = partial(Tips,
  size_x=122.4,
  size_y=82.6,
  size_z=20.0,
  tip_type=high_volume_tip_no_filter,
  dx=11.7,
  dy=9.8,
  dz=-83.5,
  tip_size_x=9.0,
  tip_size_y=9.0,
  num_items_x=12,
  num_items_y=8
)


#: Rack with 96 10ul Low Volume Tip with filter
LTF_L = partial(Tips,
  size_x=122.4,
  size_y=82.6,
  size_z=20.0,
  tip_type=low_volume_tip_with_filter,
  dx=11.7,
  dy=9.8,
  dz=-22.5,
  tip_size_x=9.0,
  tip_size_y=9.0,
  num_items_x=12,
  num_items_y=8
)


#: Tip Rack 24x 5ml Tip landscape oriented
FivemlT_L = partial(Tips,
  size_x=122.4,
  size_y=82.6,
  size_z=7.0,
  tip_type=five_ml_tip,
  dx=16.3,
  dy=14.2,
  dz=-93.2,
  tip_size_x=18.0,
  tip_size_y=18.0,
  num_items_x=6,
  num_items_y=4
)


#: Rack with 96 300ul Standard Volume Tip with filter
STF_L = partial(Tips,
  size_x=122.4,
  size_y=82.6,
  size_z=20.0,
  tip_type=standard_volume_tip_with_filter,
  dx=11.7,
  dy=9.8,
  dz=-50.5,
  tip_size_x=9.0,
  tip_size_y=9.0,
  num_items_x=12,
  num_items_y=8
)


#: Rack with 96 300ul Standard Volume Tip
ST_L = partial(Tips,
  size_x=122.4,
  size_y=82.6,
  size_z=20.0,
  tip_type=standard_volume_tip_no_filter,
  dx=11.7,
  dy=9.8,
  dz=-50.5,
  tip_size_x=9.0,
  tip_size_y=9.0,
  num_items_x=12,
  num_items_y=8
)
