""" ML Star plate carriers """

# pylint: skip-file

from functools import partial

from pylabrobot.liquid_handling.resources.abstract import PlateCarrier, Coordinate


#: Plate carrier with 5 adjustable (height) positions for MTP
PLT_CAR_L5FLEX_MD = partial(PlateCarrier,
  size_x=157.5,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(15.25, 8.5, 115.8),
    Coordinate(15.25, 104.5, 115.8),
    Coordinate(15.25, 200.5, 115.8),
    Coordinate(15.25, 296.5, 115.8),
    Coordinate(15.25, 392.5, 115.8)
  ],
  site_size_x=127.0,
  site_size_y=86.0,
  location=Coordinate(0, 0, 0)
)


#: Carrier for 3 96 Deep Well Plates (portrait)
PLT_CAR_P3AC_A00 = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(43.85, 37.5, 86.15),
    Coordinate(43.85, 183.5, 86.15),
    Coordinate(43.85, 329.5, 86.15)
  ],
  site_size_x=86.0,
  site_size_y=127.0,
  location=Coordinate(0, 0, 0)
)


#: Carrier for 3 96 Deep Well Plates (portrait)
PLT_CAR_P3AC_A01 = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(44.1, 37.5, 85.9),
    Coordinate(44.1, 183.5, 85.9),
    Coordinate(44.1, 329.5, 85.9)
  ],
  site_size_x=86.0,
  site_size_y=127.0,
  location=Coordinate(0, 0, 0)
)


#: Plate carrier with 5 adjustable (height) portrait positions for archive plates
PLT_CAR_L5FLEX_AC = partial(PlateCarrier,
  size_x=157.5,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(15.25, 8.5, 89.1),
    Coordinate(15.25, 104.5, 89.1),
    Coordinate(15.25, 200.5, 89.1),
    Coordinate(15.25, 296.5, 89.1),
    Coordinate(15.25, 392.5, 89.1)
  ],
  site_size_x=127.0,
  site_size_y=86.0,
  location=Coordinate(0, 0, 0)
)


#: Plate carrier with 5 adjustable (height) positions for MTP
PLT_CAR_L5FLEX_MD_A00 = partial(PlateCarrier,
  size_x=157.5,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(15.25, 8.5, 115.8),
    Coordinate(15.25, 104.5, 115.8),
    Coordinate(15.25, 200.5, 115.8),
    Coordinate(15.25, 296.5, 115.8),
    Coordinate(15.25, 392.5, 115.8)
  ],
  site_size_x=127.0,
  site_size_y=86.0,
  location=Coordinate(0, 0, 0)
)


#:
PLT_CAR_L4HD = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(4.1, 36.1, 118.25),
    Coordinate(4.1, 146.1, 118.25),
    Coordinate(4.1, 256.1, 118.25),
    Coordinate(4.1, 366.1, 118.25)
  ],
  site_size_x=127.0,
  site_size_y=86.0,
  location=Coordinate(0, 0, 0)
)


#:
PLT_CAR_P3HD = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(43.9, 27.05, 117.65),
    Coordinate(43.9, 173.05, 117.65),
    Coordinate(43.9, 319.05, 117.65)
  ],
  site_size_x=86.0,
  site_size_y=127.0,
  location=Coordinate(0, 0, 0)
)


#: Carrier for 5 deep well 96 Well PCR Plates
PLT_CAR_L5AC_A00 = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(4.0, 8.5, 86.15),
    Coordinate(4.0, 104.5, 86.15),
    Coordinate(4.0, 200.5, 86.15),
    Coordinate(4.0, 296.5, 86.15),
    Coordinate(4.0, 392.5, 86.15)
  ],
  site_size_x=127.0,
  site_size_y=86.0,
  location=Coordinate(0, 0, 0)
)


#: Carrier for 5 96/384-Well Plates
PLT_CAR_L5MD_A00 = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(4.0, 8.5, 111.75),
    Coordinate(4.0, 104.5, 111.75),
    Coordinate(4.0, 200.5, 111.75),
    Coordinate(4.0, 296.5, 111.75),
    Coordinate(4.0, 392.5, 111.75)
  ],
  site_size_x=127.0,
  site_size_y=86.0,
  location=Coordinate(0, 0, 0)
)


#:
PLT_CAR_L5MD = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(4.0, 8.5, 111.75),
    Coordinate(4.0, 104.5, 111.75),
    Coordinate(4.0, 200.5, 111.75),
    Coordinate(4.0, 296.5, 111.75),
    Coordinate(4.0, 392.5, 111.75)
  ],
  site_size_x=127.0,
  site_size_y=86.0,
  location=Coordinate(0, 0, 0)
)


#:
PLT_CAR_L5AC = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(4.0, 8.5, 86.15),
    Coordinate(4.0, 104.5, 86.15),
    Coordinate(4.0, 200.5, 86.15),
    Coordinate(4.0, 296.5, 86.15),
    Coordinate(4.0, 392.5, 86.15)
  ],
  site_size_x=127.0,
  site_size_y=86.0,
  location=Coordinate(0, 0, 0)
)


#: Plate carrier with 5 adjustable (height) portrait positions for archive plates
PLT_CAR_L5FLEX_AC_A00 = partial(PlateCarrier,
  size_x=157.5,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(15.25, 8.5, 89.1),
    Coordinate(15.25, 104.5, 89.1),
    Coordinate(15.25, 200.5, 89.1),
    Coordinate(15.25, 296.5, 89.1),
    Coordinate(15.25, 392.5, 89.1)
  ],
  site_size_x=127.0,
  site_size_y=86.0,
  location=Coordinate(0, 0, 0)
)


#:
PLT_CAR_L5PCR = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(4.0, 8.5, 107.5),
    Coordinate(4.0, 104.5, 107.5),
    Coordinate(4.0, 200.5, 107.5),
    Coordinate(4.0, 296.5, 107.5),
    Coordinate(4.0, 392.5, 107.5)
  ],
  site_size_x=127.0,
  site_size_y=86.0,
  location=Coordinate(0, 0, 0)
)


#:
PLT_CAR_P3MD = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(44.1, 37.5, 111.5),
    Coordinate(44.1, 183.5, 111.5),
    Coordinate(44.1, 329.5, 111.5)
  ],
  site_size_x=86.0,
  site_size_y=127.0,
  location=Coordinate(0, 0, 0)
)


#: Plate carrier for 5 PCR landscape plates [revision 00]
PLT_CAR_L5PCR_A00 = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(5.0, 9.5, 109.2),
    Coordinate(5.0, 105.5, 109.2),
    Coordinate(5.0, 201.5, 109.2),
    Coordinate(5.0, 297.5, 109.2),
    Coordinate(5.0, 393.5, 109.2)
  ],
  site_size_x=127.0,
  site_size_y=86.0,
  location=Coordinate(0, 0, 0)
)


#: Plate carrier for 5 PCR landscape plates [revision 01]
PLT_CAR_L5PCR_A01 = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(5.0, 9.5, 109.2),
    Coordinate(5.0, 105.5, 109.2),
    Coordinate(5.0, 201.5, 109.2),
    Coordinate(5.0, 297.5, 109.2),
    Coordinate(5.0, 393.5, 109.2)
  ],
  site_size_x=127.0,
  site_size_y=86.0,
  location=Coordinate(0, 0, 0)
)


#: Carrier for 3 96/384-Well Plates (Portrait)
PLT_CAR_P3MD_A01 = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(44.1, 37.5, 111.5),
    Coordinate(44.1, 183.5, 111.5),
    Coordinate(44.1, 329.5, 111.5)
  ],
  site_size_x=86.0,
  site_size_y=127.0,
  location=Coordinate(0, 0, 0)
)


#: Carrier for 3 96/384-Well Plates (Portrait)
PLT_CAR_P3MD_A00 = partial(PlateCarrier,
  size_x=135.0,
  size_y=497.0,
  size_z=130.0,
  sites=[
    Coordinate(44.1, 37.5, 111.5),
    Coordinate(44.1, 183.5, 111.5),
    Coordinate(44.1, 329.5, 111.5)
  ],
  site_size_x=86.0,
  site_size_y=127.0,
  location=Coordinate(0, 0, 0)
)
