""" ML Star plate carriers """

# pylint: skip-file

from pyhamilton.liquid_handling.resources.abstract import PlateCarrier, Coordinate, CarrierSite


class PLT_CAR_L5FLEX_MD(PlateCarrier):
  """ Plate carrier with 5 adjustable (height) positions for MTP """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=157.5,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(15.25, 8.5, 115.8), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 104.5, 115.8), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 200.5, 115.8), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 296.5, 115.8), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 392.5, 115.8), width=127.0, height=86.0, resource=None)
      ]
    )


class PLT_CAR_P3AC_A00(PlateCarrier):
  """ Carrier for 3 96 Deep Well Plates (portrait) """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(43.85, 37.5, 86.15), width=86.0, height=127.0, resource=None),
        CarrierSite(location=Coordinate(43.85, 183.5, 86.15), width=86.0, height=127.0, resource=None),
        CarrierSite(location=Coordinate(43.85, 329.5, 86.15), width=86.0, height=127.0, resource=None)
      ]
    )


class PLT_CAR_P3AC_A01(PlateCarrier):
  """ Carrier for 3 96 Deep Well Plates (portrait) """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(44.1, 37.5, 85.9), width=86.0, height=127.0, resource=None),
        CarrierSite(location=Coordinate(44.1, 183.5, 85.9), width=86.0, height=127.0, resource=None),
        CarrierSite(location=Coordinate(44.1, 329.5, 85.9), width=86.0, height=127.0, resource=None)
      ]
    )


class PLT_CAR_L5FLEX_AC(PlateCarrier):
  """ Plate carrier with 5 adjustable (height) portrait positions for archive plates """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=157.5,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(15.25, 8.5, 89.1), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 104.5, 89.1), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 200.5, 89.1), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 296.5, 89.1), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 392.5, 89.1), width=127.0, height=86.0, resource=None)
      ]
    )


class PLT_CAR_L5FLEX_MD_A00(PlateCarrier):
  """ Plate carrier with 5 adjustable (height) positions for MTP """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=157.5,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(15.25, 8.5, 115.8), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 104.5, 115.8), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 200.5, 115.8), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 296.5, 115.8), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 392.5, 115.8), width=127.0, height=86.0, resource=None)
      ]
    )


class PLT_CAR_L4HD(PlateCarrier):
  """  """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(4.1, 36.1, 118.25), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.1, 146.1, 118.25), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.1, 256.1, 118.25), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.1, 366.1, 118.25), width=127.0, height=86.0, resource=None)
      ]
    )


class PLT_CAR_P3HD(PlateCarrier):
  """  """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(43.9, 27.05, 117.65), width=86.0, height=127.0, resource=None),
        CarrierSite(location=Coordinate(43.9, 173.05, 117.65), width=86.0, height=127.0, resource=None),
        CarrierSite(location=Coordinate(43.9, 319.05, 117.65), width=86.0, height=127.0, resource=None)
      ]
    )


class PLT_CAR_L5AC_A00(PlateCarrier):
  """ Carrier for 5 deep well 96 Well PCR Plates """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(4.0, 8.5, 86.15), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 104.5, 86.15), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 200.5, 86.15), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 296.5, 86.15), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 392.5, 86.15), width=127.0, height=86.0, resource=None)
      ]
    )


class PLT_CAR_L5MD_A00(PlateCarrier):
  """ Carrier for 5 96/384-Well Plates """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(4.0, 8.5, 111.75), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 104.5, 111.75), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 200.5, 111.75), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 296.5, 111.75), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 392.5, 111.75), width=127.0, height=86.0, resource=None)
      ]
    )


class PLT_CAR_L5MD(PlateCarrier):
  """  """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(4.0, 8.5, 111.75), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 104.5, 111.75), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 200.5, 111.75), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 296.5, 111.75), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 392.5, 111.75), width=127.0, height=86.0, resource=None)
      ]
    )


class PLT_CAR_L5AC(PlateCarrier):
  """  """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(4.0, 8.5, 86.15), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 104.5, 86.15), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 200.5, 86.15), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 296.5, 86.15), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 392.5, 86.15), width=127.0, height=86.0, resource=None)
      ]
    )


class PLT_CAR_L5FLEX_AC_A00(PlateCarrier):
  """ Plate carrier with 5 adjustable (height) portrait positions for archive plates """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=157.5,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(15.25, 8.5, 89.1), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 104.5, 89.1), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 200.5, 89.1), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 296.5, 89.1), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(15.25, 392.5, 89.1), width=127.0, height=86.0, resource=None)
      ]
    )


class PLT_CAR_L5PCR(PlateCarrier):
  """  """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(4.0, 8.5, 107.5), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 104.5, 107.5), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 200.5, 107.5), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 296.5, 107.5), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(4.0, 392.5, 107.5), width=127.0, height=86.0, resource=None)
      ]
    )


class PLT_CAR_P3MD(PlateCarrier):
  """  """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(44.1, 37.5, 111.5), width=86.0, height=127.0, resource=None),
        CarrierSite(location=Coordinate(44.1, 183.5, 111.5), width=86.0, height=127.0, resource=None),
        CarrierSite(location=Coordinate(44.1, 329.5, 111.5), width=86.0, height=127.0, resource=None)
      ]
    )


class PLT_CAR_L5PCR_A00(PlateCarrier):
  """ Plate carrier for 5 PCR landscape plates [revision 00] """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(5.0, 9.5, 109.2), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(5.0, 105.5, 109.2), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(5.0, 201.5, 109.2), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(5.0, 297.5, 109.2), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(5.0, 393.5, 109.2), width=127.0, height=86.0, resource=None)
      ]
    )


class PLT_CAR_L5PCR_A01(PlateCarrier):
  """ Plate carrier for 5 PCR landscape plates [revision 01] """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(5.0, 9.5, 109.2), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(5.0, 105.5, 109.2), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(5.0, 201.5, 109.2), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(5.0, 297.5, 109.2), width=127.0, height=86.0, resource=None),
        CarrierSite(location=Coordinate(5.0, 393.5, 109.2), width=127.0, height=86.0, resource=None)
      ]
    )


class PLT_CAR_P3MD_A01(PlateCarrier):
  """ Carrier for 3 96/384-Well Plates (Portrait) """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(44.1, 37.5, 111.5), width=86.0, height=127.0, resource=None),
        CarrierSite(location=Coordinate(44.1, 183.5, 111.5), width=86.0, height=127.0, resource=None),
        CarrierSite(location=Coordinate(44.1, 329.5, 111.5), width=86.0, height=127.0, resource=None)
      ]
    )


class PLT_CAR_P3MD_A00(PlateCarrier):
  """ Carrier for 3 96/384-Well Plates (Portrait) """

  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=135.0,
      size_y=497.0,
      size_z=130.0,
      sites=[
        CarrierSite(location=Coordinate(44.1, 37.5, 111.5), width=86.0, height=127.0, resource=None),
        CarrierSite(location=Coordinate(44.1, 183.5, 111.5), width=86.0, height=127.0, resource=None),
        CarrierSite(location=Coordinate(44.1, 329.5, 111.5), width=86.0, height=127.0, resource=None)
      ]
    )
