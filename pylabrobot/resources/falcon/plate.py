
from typing import Optional

from pylabrobot.resources.height_volume_functions import compute_height_from_volume_conical_frustum, compute_volume_from_height_conical_frustum
from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import Well, WellBottomType


def Falcon_96_WP_Fl(name: str, lid: Optional[Lid] = None) -> Plate:
    BOTTOM_RADIUS = 3.175
    TOP_RADIUS = 3.425

    return Plate(
        name=name,
        size_x=127.76,  # directly from reference manual
        size_y=85.11,  # directly from reference manual
        size_z=14.30,  # without lid, directly from reference manual
        lid=lid,
        model=Falcon_96_WP_Fl.__name__,
        ordered_items=create_ordered_items_2d(
            Well,
            num_items_x=12,
            num_items_y=8,
            dx=11.05,  # measured
            dy=7.75,  # measured
            dz=1.11,  # bottom thickness since the plate bottom touches the carrier, directly from reference manual
            item_dx=8.99,
            item_dy=8.99,
            size_x=6.35,
            size_y=6.35,
            size_z=14.30,
            bottom_type=WellBottomType.FLAT,
            compute_volume_from_height=lambda liquid_height: compute_volume_from_height_conical_frustum(
                liquid_height, BOTTOM_RADIUS, TOP_RADIUS
            ),
            compute_height_from_volume=lambda liquid_volume: compute_height_from_volume_conical_frustum(
                liquid_volume, BOTTOM_RADIUS, TOP_RADIUS
            ),
        ),
    )