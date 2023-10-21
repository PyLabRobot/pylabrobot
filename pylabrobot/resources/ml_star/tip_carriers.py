""" ML Star tip carriers """

# pylint: disable=invalid-name
# pylint: disable=line-too-long

from pylabrobot.resources.carrier import TipCarrier, create_homogeneous_carrier_sites
from pylabrobot.resources.coordinate import Coordinate


def TIP_CAR_120BC_4mlTF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 4ml tip with filter racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_120BC_4mlTF_A00"
  )


def TIP_CAR_120BC_5mlT_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 5ml tip racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_120BC_5mlT_A00"
  )


def TIP_CAR_288_A00(name: str) -> TipCarrier:
  """ Carrier for 3 Racks with 96 Tips portrait  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(26.3, 36.3, 114.9),
        Coordinate(26.3, 182.213, 114.9),
        Coordinate(26.3, 328.213, 114.9)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_A00"
  )


def TIP_CAR_288_B00(name: str) -> TipCarrier:
  """ Carrier for 3 Racks with 96 Tips portrait [revision B00] """
  return TipCarrier(
    name=name,
    size_x=112.5,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(17.1, 36.25, 115.15),
        Coordinate(17.1, 182.25, 115.15),
        Coordinate(17.1, 328.25, 115.15)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_B00"
  )


def TIP_CAR_288_C00(name: str) -> TipCarrier:
  """ Carrier for 3 Racks with 96 Tips portrait [revision C00] """
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_C00"
  )


def TIP_CAR_288_HTF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 3 high volume tip with filter racks portrait  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(26.3, 36.3, 114.95),
        Coordinate(26.3, 182.213, 114.95),
        Coordinate(26.3, 328.213, 114.95)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_HTF_A00"
  )


def TIP_CAR_288_HTF_B00(name: str) -> TipCarrier:
  """ Tip carrier with 3 high volume tip with filter racks portrait  [revision B00] """
  return TipCarrier(
    name=name,
    size_x=112.5,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(17.1, 36.25, 115.15),
        Coordinate(17.1, 182.25, 115.15),
        Coordinate(17.1, 328.25, 115.15)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_HTF_B00"
  )


def TIP_CAR_288_HTF_C00(name: str) -> TipCarrier:
  """ Tip carrier with 3 high volume tip with filter racks portrait  [revision C00] """
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_HTF_C00"
  )


def TIP_CAR_288_HT_A00(name: str) -> TipCarrier:
  """ Tip carrier with 3 high volume tip racks portrait  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(26.3, 36.3, 114.95),
        Coordinate(26.3, 182.213, 114.95),
        Coordinate(26.3, 328.213, 114.95)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_HT_A00"
  )


def TIP_CAR_288_HT_B00(name: str) -> TipCarrier:
  """ Tip carrier with 3 high volume tip racks portrait  [revision B00] """
  return TipCarrier(
    name=name,
    size_x=112.5,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(17.1, 36.25, 115.15),
        Coordinate(17.1, 182.25, 115.15),
        Coordinate(17.1, 328.25, 115.15)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_HT_B00"
  )


def TIP_CAR_288_HT_C00(name: str) -> TipCarrier:
  """ Tip carrier with 3 high volume tip racks portrait  [revision C00] """
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_HT_C00"
  )


def TIP_CAR_288_LTF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 3 low volume tip with filter racks portrait  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(26.3, 36.3, 114.95),
        Coordinate(26.3, 182.213, 114.95),
        Coordinate(26.3, 328.213, 114.95)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_LTF_A00"
  )


def TIP_CAR_288_LTF_B00(name: str) -> TipCarrier:
  """ Tip carrier with 3 low volume tip with filter racks portrait  [revision B00] """
  return TipCarrier(
    name=name,
    size_x=112.5,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(17.1, 36.25, 115.15),
        Coordinate(17.1, 182.25, 115.15),
        Coordinate(17.1, 328.25, 115.15)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_LTF_B00"
  )


def TIP_CAR_288_LTF_C00(name: str) -> TipCarrier:
  """ Tip carrier with 3 low volume tip with filter racks portrait  [revision C00] """
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_LTF_C00"
  )


def TIP_CAR_288_LT_A00(name: str) -> TipCarrier:
  """ Tip carrier with 3 low volume tip racks portrait  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(26.3, 36.3, 114.9),
        Coordinate(26.3, 182.213, 114.95),
        Coordinate(26.3, 328.213, 114.95)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_LT_A00"
  )


def TIP_CAR_288_LT_B00(name: str) -> TipCarrier:
  """ Tip carrier with 3 low volume tip racks portrait  [revision B00] """
  return TipCarrier(
    name=name,
    size_x=112.5,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(17.1, 36.25, 115.15),
        Coordinate(17.1, 182.25, 115.15),
        Coordinate(17.1, 328.25, 115.15)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_LT_B00"
  )


def TIP_CAR_288_LT_C00(name: str) -> TipCarrier:
  """ Tip carrier with 3 low volume tip racks portrait  [revision C00] """
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_LT_C00"
  )


def TIP_CAR_288_STF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 3 standard volume tip with filter racks portrait  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(26.3, 36.3, 114.95),
        Coordinate(26.3, 182.213, 114.95),
        Coordinate(26.3, 328.213, 114.95)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_STF_A00"
  )


def TIP_CAR_288_STF_B00(name: str) -> TipCarrier:
  """ Tip carrier with 3 standard volume tip with filter racks portrait  [revision B00] """
  return TipCarrier(
    name=name,
    size_x=112.5,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(17.1, 36.25, 115.15),
        Coordinate(17.1, 182.25, 115.15),
        Coordinate(17.1, 328.25, 115.15)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_STF_B00"
  )


def TIP_CAR_288_STF_C00(name: str) -> TipCarrier:
  """ Tip carrier with 3 standard volume tip with filter racks portrait  [revision C00] """
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_STF_C00"
  )


def TIP_CAR_288_ST_A00(name: str) -> TipCarrier:
  """ Tip carrier with 3 standard volume tip racks portrait  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(26.3, 36.3, 114.95),
        Coordinate(26.3, 182.213, 114.95),
        Coordinate(26.3, 328.213, 114.95)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_ST_A00"
  )


def TIP_CAR_288_ST_B00(name: str) -> TipCarrier:
  """ Tip carrier with 3 standard volume tip racks portrait  [revision B00] """
  return TipCarrier(
    name=name,
    size_x=112.5,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(17.1, 36.25, 115.15),
        Coordinate(17.1, 182.25, 115.15),
        Coordinate(17.1, 328.25, 115.15)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_ST_B00"
  )


def TIP_CAR_288_ST_C00(name: str) -> TipCarrier:
  """ Tip carrier with 3 standard volume tip racks portrait  [revision C00] """
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_ST_C00"
  )


def TIP_CAR_288_TIP_50ulF_C00(name: str) -> TipCarrier:
  """ Tip carrier with 3 50ul tip with filter racks portrait  [revision C00] """
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_TIP_50ulF_C00"
  )


def TIP_CAR_288_TIP_50ul_C00(name: str) -> TipCarrier:
  """ Tip carrier with 3 50ul tip racks portrait  [revision C00] """
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_288_TIP_50ul_C00"
  )


def TIP_CAR_384BC_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 empty tip rack positions landscape, with Barcode Identification  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384BC_A00"
  )


def TIP_CAR_384BC_HTF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 high volume tip with filter racks for 12/16 channel instruments """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384BC_HTF_A00"
  )


def TIP_CAR_384BC_HT_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 high volume tip racks for 12/16 channel instruments """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384BC_HT_A00"
  )


def TIP_CAR_384BC_LTF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 low volume tip with filter racks for 12/16 channel instruments """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384BC_LTF_A00"
  )


def TIP_CAR_384BC_LT_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 low volume tip racks for 12/16 channel instruments """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384BC_LT_A00"
  )


def TIP_CAR_384BC_STF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 standard volume tip with filter racks for 12/16 channel instruments """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384BC_STF_A00"
  )


def TIP_CAR_384BC_ST_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 standard volume tip with filter racks for 12/16 channel instruments """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384BC_ST_A00"
  )


def TIP_CAR_384BC_TIP_50ulF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 50ul tip with filter racks landscape  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384BC_TIP_50ulF_A00"
  )


def TIP_CAR_384BC_TIP_50ul_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 50ul tip racks landscape  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384BC_TIP_50ul_A00"
  )


def TIP_CAR_384_A00(name: str) -> TipCarrier:
  """ Carrier for 4 Racks with 96 Tips landscape [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384_A00"
  )


def TIP_CAR_384_HT_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 high volume tip racks for 12/16 channel instruments, no barcode identification """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384_HT_A00"
  )


def TIP_CAR_384_LTF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 low volume tip with filter racks for 12/16 channel instruments, no barcode identification """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384_LTF_A00"
  )


def TIP_CAR_384_LT_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 low volume tip racks for 12/16 channel instruments, no barcode identification """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384_LT_A00"
  )


def TIP_CAR_384_STF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 standard volume tip with filter racks for 12/16 channel instruments, no barcode identification  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384_STF_A00"
  )


def TIP_CAR_384_ST_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 standard volume tip racks for 12/16 channel instruments, no barcode identification  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384_ST_A00"
  )


def TIP_CAR_384_TIP_50ulF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 50ul tip with filter racks landscape  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384_TIP_50ulF_A00"
  )


def TIP_CAR_384_TIP_50ul_A00(name: str) -> TipCarrier:
  """ Tip carrier with 4 50ul tip racks landscape  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_384_TIP_50ul_A00"
  )


def TIP_CAR_480(name: str) -> TipCarrier:
  """ Carrier for 5 Racks with 96 Tips landscape [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480"
  )


def TIP_CAR_480BC_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 tip rack positions landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_A00"
  )


def TIP_CAR_480BC_HTF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 high volume tip with filter racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_HTF_A00"
  )


def TIP_CAR_480BC_HT_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 high volume tip racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_HT_A00"
  )


def TIP_CAR_480BC_LTF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 low volume tip with filter racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_LTF_A00"
  )


def TIP_CAR_480BC_LT_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 low volume tip racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_LT_A00"
  )


def TIP_CAR_480BC_PiercingTip150ulFilter_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 Piercing Tips 150ul tip racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_PiercingTip150ulFilter_A00"
  )


def TIP_CAR_480BC_PiercingTips_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 Piercing Tips 250ul tip racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_PiercingTips_A00"
  )


def TIP_CAR_480BC_STF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 standard volume tip with filter racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_STF_A00"
  )


def TIP_CAR_480BC_ST_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 standard volume tip racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_ST_A00"
  )


def TIP_CAR_480BC_SlimTips300ulFilter_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 Slim Tips 300ul with filters tip racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_SlimTips300ulFilter_A00"
  )


def TIP_CAR_480BC_SlimTips_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 Slim Tips 300ul tip racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_SlimTips_A00"
  )


def TIP_CAR_480BC_TIP_50ulF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 50ul tip with filter racks landscape  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_TIP_50ulF_A00"
  )


def TIP_CAR_480BC_TIP_50ul_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 50ul tip racks landscape  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480BC_TIP_50ul_A00"
  )


def TIP_CAR_480_A00(name: str) -> TipCarrier:
  """ Carrier for 5 Racks with 96 Tips landscape [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480_A00"
  )


def TIP_CAR_480_HTF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 high volume tip with filter racks landscape  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480_HTF_A00"
  )


def TIP_CAR_480_HT_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 high volume tip racks landscape  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480_HT_A00"
  )


def TIP_CAR_480_LTF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 low volume tip with filter racks landscape, no barcode identification """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480_LTF_A00"
  )


def TIP_CAR_480_LT_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 low volume tip racks landscape, no barcode identification """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480_LT_A00"
  )


def TIP_CAR_480_STF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 standard volume tip with filter racks landscape  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480_STF_A00"
  )


def TIP_CAR_480_ST_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 standard volume tip racks landscape  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480_ST_A00"
  )


def TIP_CAR_480_TIP_50ulF_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 50ul tip with filter racks landscape  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480_TIP_50ulF_A00"
  )


def TIP_CAR_480_TIP_50ul_A00(name: str) -> TipCarrier:
  """ Tip carrier with 5 50ul tip racks landscape  [revision A00] """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 114.95),
        Coordinate(6.2, 106.0, 114.95),
        Coordinate(6.2, 202.0, 114.95),
        Coordinate(6.2, 298.0, 114.95),
        Coordinate(6.2, 394.0, 114.95)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_480_TIP_50ul_A00"
  )


def TIP_CAR_72_4mlTF_C00(name: str) -> TipCarrier:
  """ Tip carrier with 3 4ml tip with filter racks portrait  [revision C00] """
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_72_4mlTF_C00"
  )


def TIP_CAR_72_5mlT_C00(name: str) -> TipCarrier:
  """ Tip carrier with 3 5ml tip racks portrait  [revision C00] """
  return TipCarrier(
    name=name,
    size_x=90.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(3.7, 36.3, 114.7),
        Coordinate(3.7, 182.3, 114.7),
        Coordinate(3.7, 328.3, 114.7)
      ],
      site_size_x=82.6,
      site_size_y=122.4,
    ),
    model="TIP_CAR_72_5mlT_C00"
  )


def TIP_CAR_96BC_4mlTF_A00(name: str) -> TipCarrier:
  """ Carrier for 4 4ml with filter tip racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_96BC_4mlTF_A00"
  )


def TIP_CAR_96BC_5mlT_A00(name: str) -> TipCarrier:
  """ Carrier for 4 5ml tip racks landscape """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.3, 78.2, 114.8),
        Coordinate(6.3, 163.1, 114.8),
        Coordinate(6.3, 248.1, 114.8),
        Coordinate(6.3, 333.1, 114.8)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_96BC_5mlT_A00"
  )


def TIP_CAR_NTR_A00(name: str) -> TipCarrier:
  """ Carrier with 5 nestable tip rack positions """
  return TipCarrier(
    name=name,
    size_x=135.0,
    size_y=497.0,
    size_z=130.0,
    sites=create_homogeneous_carrier_sites([
        Coordinate(6.2, 10.0, 29.0),
        Coordinate(6.2, 106.0, 29.0),
        Coordinate(6.2, 202.0, 29.0),
        Coordinate(6.2, 298.0, 29.0),
        Coordinate(6.2, 394.0, 29.0)
      ],
      site_size_x=122.4,
      site_size_y=82.6,
    ),
    model="TIP_CAR_NTR_A00"
  )
