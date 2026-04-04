"""Constants for the Formulatrix Mantis liquid dispenser.

Includes motor status codes, pressure control status, velocity profiles,
chip kinematic paths, and PPI (Programmable Pulse Interface) sequences.
"""

from enum import IntEnum, IntFlag
from typing import Dict, List, Tuple


class MotorStatusCode(IntFlag):
  """Bitmask status codes for Mantis motors."""

  NONE = 0
  IS_MOVING = 1
  IS_HOMING = 2
  IS_HOMED = 4
  LOWER_LIMIT = 8
  UPPER_LIMIT = 16
  OVER_CURRENT = 32
  ABORTED = 64
  FOLLOWING_ERROR_IDLE = 128
  FOLLOWING_ERROR_MOVING = 256
  ENCODER_ERROR = 512
  UNSTABLE_CURRENT = 1024

  @classmethod
  def error_mask(cls) -> "MotorStatusCode":
    return (
      cls.OVER_CURRENT
      | cls.ABORTED
      | cls.FOLLOWING_ERROR_IDLE
      | cls.FOLLOWING_ERROR_MOVING
      | cls.ENCODER_ERROR
      | cls.UNSTABLE_CURRENT
    )


class PressureControlStatus(IntEnum):
  """Status codes for the pressure controller."""

  OFF = 0
  SETTLED = 1
  UNSETTLED = 2


# Sensor IDs
SENSOR_PRESSURE = 0
SENSOR_VACUUM = 1

# Velocity / Acceleration tuples: (v1, a1, v2, a2, v_z, a_z)
VEL_DEFAULT: Tuple[float, ...] = (10000.0, 1500.0, 10000.0, 1500.0, 55.0, 200.0)
VEL_HOME: Tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 55.0, 200.0)
VEL_XY_ONLY: Tuple[float, ...] = (10000.0, 1500.0, 10000.0, 1500.0, 0.0, 0.0)

# Named positions: (x, y, z) in mm
XY_HOME: Tuple[float, float, float] = (15.0, 31.177, 0.0)
XY_READY: Tuple[float, float, float] = (15.0, 30.0, 0.0)

# Type alias for a waypoint: ((x, y, z), velocity_tuple)
Waypoint = Tuple[Tuple[float, float, float], Tuple[float, ...]]

# PPI sequence entry: (duration_ms, address, values)
PPIEntry = Tuple[int, int, List[int]]

# ---------------------------------------------------------------------------
# Chip kinematic paths
# Each path is a list of waypoints the head follows to attach/detach a chip.
# ---------------------------------------------------------------------------

CHIP_1_PATH: List[Waypoint] = [
  ((70.394, 63.965, -1.5), VEL_DEFAULT),
  ((81.902, 29.298, -1.5), VEL_DEFAULT),
  ((95.902, 9.298, -1.5), VEL_DEFAULT),
  ((125.902, 19.298, -1.5), VEL_DEFAULT),
  ((125.902, 29.298, -1.5), VEL_DEFAULT),
  ((125.902, 29.298, 13.067), VEL_DEFAULT),
  ((125.902, 29.298, 13.067), VEL_DEFAULT),
  ((123.731, 29.384, 13.117), VEL_DEFAULT),
  ((123.731, 29.384, 0.0), VEL_XY_ONLY),
  ((123.731, 29.384, 0.0), VEL_DEFAULT),
  ((91.060, 27.755, -1.5), VEL_DEFAULT),
]

CHIP_2_PATH: List[Waypoint] = [
  ((80.808, 73.114, -1.5), VEL_DEFAULT),
  ((85.961, 44.334, -1.5), VEL_DEFAULT),
  ((95.961, 44.334, -1.5), VEL_DEFAULT),
  ((105.961, 45.334, -1.5), VEL_DEFAULT),
  ((110.961, 46.334, -1.5), VEL_DEFAULT),
  ((116.961, 47.334, -1.5), VEL_DEFAULT),
  ((119.961, 49.334, -1.5), VEL_DEFAULT),
  ((121.961, 52.334, -1.5), VEL_DEFAULT),
  ((123.461, 55.334, -1.5), VEL_DEFAULT),
  ((122.961, 58.334, -1.5), VEL_DEFAULT),
  ((122.961, 58.334, 13.253), VEL_DEFAULT),
  ((122.961, 58.334, 13.253), VEL_DEFAULT),
  ((120.450, 57.048, 13.459), VEL_DEFAULT),
  ((120.450, 57.048, 0.0), VEL_XY_ONLY),
  ((120.450, 57.048, 0.0), VEL_DEFAULT),
  ((90.246, 52.850, -1.5), VEL_DEFAULT),
]

CHIP_3_PATH: List[Waypoint] = [
  ((-44.967, 67.275, 0.0), VEL_DEFAULT),
  ((-56.838, 76.393, -1.5), VEL_DEFAULT),
  ((-68.838, 77.393, -1.5), VEL_DEFAULT),
  ((-68.838, 77.393, 13.022), VEL_DEFAULT),
  ((-68.838, 77.393, 13.022), VEL_DEFAULT),
  ((-68.636, 75.270, 12.637), VEL_DEFAULT),
  ((-68.636, 75.270, 0.0), VEL_XY_ONLY),
  ((-68.636, 75.270, 0.0), VEL_DEFAULT),
  ((-56.636, 74.270, -1.5), VEL_DEFAULT),
]

CHIP_4_PATH: List[Waypoint] = [
  ((-50.317, 42.819, -1.5), VEL_DEFAULT),
  ((-53.677, 42.742, -1.5), VEL_DEFAULT),
  ((-63.677, 39.742, -1.5), VEL_DEFAULT),
  ((-73.677, 49.742, -1.5), VEL_DEFAULT),
  ((-78.677, 54.742, -1.5), VEL_DEFAULT),
  ((-82.677, 58.742, -1.5), VEL_DEFAULT),
  ((-86.677, 62.742, -1.5), VEL_DEFAULT),
  ((-91.677, 60.742, -1.5), VEL_DEFAULT),
  ((-91.677, 60.742, 12.911), VEL_DEFAULT),
  ((-91.677, 60.742, 12.911), VEL_DEFAULT),
  ((-90.476, 58.677, 12.945), VEL_DEFAULT),
  ((-90.476, 58.677, 0.0), VEL_XY_ONLY),
  ((-90.476, 58.677, 0.0), VEL_DEFAULT),
  ((-79.116, 48.277, -1.5), VEL_DEFAULT),
  ((-65.916, 31.317, -1.5), VEL_DEFAULT),
  ((-37.776, 45.537, -1.5), VEL_DEFAULT),
]

CHIP_5_PATH: List[Waypoint] = [
  ((-70.0, 35.0, -1.5), VEL_DEFAULT),
  ((-82.48, 32.654, -1.5), VEL_DEFAULT),
  ((-97.48, 40.654, -1.5), VEL_DEFAULT),
  ((-104.48, 39.654, -1.5), VEL_DEFAULT),
  ((-107.48, 37.654, -1.5), VEL_DEFAULT),
  ((-107.48, 37.654, 13.1), VEL_DEFAULT),
  ((-107.48, 37.654, 13.1), VEL_DEFAULT),
  ((-105.791, 35.948, 13.334), VEL_DEFAULT),
  ((-105.791, 35.948, 0.0), VEL_XY_ONLY),
  ((-105.791, 35.948, 0.0), VEL_DEFAULT),
  ((-90.471, 28.058, -1.5), VEL_DEFAULT),
  ((-68.121, 32.298, -1.5), VEL_DEFAULT),
  ((-36.491, 59.968, -1.5), VEL_DEFAULT),
]

CHIP_6_PATH: List[Waypoint] = [
  ((-57.0, 0.0, 0.0), VEL_DEFAULT),
  ((-68.436, 6.404, -1.5), VEL_DEFAULT),
  ((-88.436, 6.404, -1.5), VEL_DEFAULT),
  ((-98.436, 9.404, -1.5), VEL_DEFAULT),
  ((-108.436, 12.404, -1.5), VEL_DEFAULT),
  ((-112.436, 8.404, -1.5), VEL_DEFAULT),
  ((-112.436, 8.404, 12.991), VEL_DEFAULT),
  ((-112.436, 8.404, 12.991), VEL_DEFAULT),
  ((-110.384, 7.333, 12.822), VEL_DEFAULT),
  ((-110.384, 7.333, 0.0), VEL_XY_ONLY),
  ((-110.384, 7.333, 0.0), VEL_DEFAULT),
  ((-95.384, 7.333, -1.5), VEL_DEFAULT),
  ((-80.384, 7.333, -1.5), VEL_DEFAULT),
  ((-60.384, 7.333, -1.5), VEL_DEFAULT),
]

CHIP_PATHS: Dict[int, List[Waypoint]] = {
  1: CHIP_1_PATH,
  2: CHIP_2_PATH,
  3: CHIP_3_PATH,
  4: CHIP_4_PATH,
  5: CHIP_5_PATH,
  6: CHIP_6_PATH,
}

XY_WASTE_PATH: List[Waypoint] = [
  ((15.0, 31.177, -1.5), VEL_HOME),
  ((15.0, 31.17, 0.0), VEL_XY_ONLY),
  ((64.0, 60.0, 0.0), VEL_XY_ONLY),
  ((97.4331, -15.2603, 0.0), VEL_XY_ONLY),
  ((108.4284, -44.4724, 0.0), VEL_XY_ONLY),
  ((15.0, 31.177, -1.5), VEL_HOME),
  ((119.756, -52.28, 5.191), VEL_DEFAULT),
]

# ---------------------------------------------------------------------------
# PPI (Programmable Pulse Interface) sequences
# Keyed by chip type, then by sequence name.
# Each sequence is a list of (duration_ms, address, [ppi_values]).
# ---------------------------------------------------------------------------

PPI_SEQUENCES: Dict[str, Dict[str, List[PPIEntry]]] = {
  "high_volume": {
    "detachrecovery": [
      (84, 40, [25]),
      (15, 40, [27]),
      (14, 40, [30]),
      (136, 40, [31]),
    ]
    * 5
    + [(100, 40, [31])],
    "dispense_1uL": [
      (34, 40, [30]),
      (14, 40, [31]),
      (15, 40, [29]),
      (21, 40, [29]),
      (13, 40, [31]),
    ],
    "dispense_5uL": [
      (136, 40, [26]),
      (14, 40, [27]),
      (15, 40, [25]),
      (84, 40, [29]),
      (13, 40, [31]),
    ],
    "postprime": [
      (13, 40, [31]),
      (12, 40, [30]),
      (34, 40, [30]),
      (14, 40, [31]),
      (15, 40, [29]),
      (21, 40, [29]),
      (13, 40, [31]),
      (12, 40, [30]),
      (34, 40, [30]),
      (14, 40, [31]),
      (15, 40, [29]),
      (21, 40, [29]),
      (13, 40, [31]),
    ],
    "preattach": [(100, 40, [31])],
    "predetach": [
      (100, 40, [24]),
      (100, 40, [26]),
      (100, 40, [30]),
      (100, 40, [31]),
    ],
    "primepump": [
      (136, 40, [26]),
      (14, 40, [27]),
      (15, 40, [25]),
      (84, 40, [29]),
      (13, 40, [31]),
    ],
    "reversepump": [
      (84, 40, [25]),
      (15, 40, [27]),
      (14, 40, [30]),
      (136, 40, [31]),
    ],
    "washinput": [(35, 40, [25]), (1, 40, [27]), (25, 40, [30]), (1, 40, [31])] * 100
    + [(35, 40, [25])],
  },
  "high_volume_pfe": {
    "detachrecovery": [
      (54, 40, [29]),
      (204, 40, [25]),
      (35, 40, [27]),
      (53, 40, [30]),
      (86, 40, [31]),
    ]
    * 5
    + [(100, 40, [31])],
    "dispense_1uL": [
      (26, 40, [30]),
      (121, 40, [30]),
      (34, 40, [31]),
      (35, 40, [29]),
      (51, 40, [29]),
      (33, 40, [31]),
    ],
    "dispense_5uL": [
      (26, 40, [30]),
      (273, 40, [26]),
      (90, 40, [27]),
      (35, 40, [25]),
      (84, 40, [29]),
      (101, 40, [31]),
    ],
    "postprime": [
      (33, 40, [31]),
      (26, 40, [30]),
      (121, 40, [30]),
      (34, 40, [31]),
      (35, 40, [29]),
      (51, 40, [29]),
      (33, 40, [31]),
      (26, 40, [30]),
      (121, 40, [30]),
      (34, 40, [31]),
      (35, 40, [29]),
      (51, 40, [29]),
      (33, 40, [31]),
    ],
    "preattach": [(100, 40, [31])],
    "predetach": [
      (100, 40, [24]),
      (100, 40, [26]),
      (100, 40, [30]),
      (100, 40, [31]),
    ],
    "primepump": [
      (36, 40, [30]),
      (271, 40, [26]),
      (64, 40, [27]),
      (35, 40, [25]),
      (84, 40, [29]),
      (33, 40, [31]),
    ],
    "reversepump": [
      (204, 40, [25]),
      (35, 40, [27]),
      (33, 40, [26]),
      (53, 40, [30]),
      (86, 40, [31]),
      (54, 40, [29]),
    ],
    "washinput": [(35, 40, [25]), (1, 40, [27]), (25, 40, [30]), (1, 40, [31])] * 100
    + [(35, 40, [25])],
  },
  "hv_continuous_flow": {
    "checkbottleleak": [
      (100, 40, [30]),
      (5000, 40, [30]),
      (100, 40, [30]),
      (500, 40, [28]),
    ],
    "closeoutput": [(100, 40, [31])],
    "detachrecovery": [(100, 40, [27])],
    "dispense_1uL": [(100, 40, [30])],
    "postdetach": [(100, 40, [27])],
    "postprime": [(100, 40, [30]), (30, 40, [28]), (100, 40, [30])],
    "postwashinput": [(4000, 40, [30]), (500, 40, [28])],
    "preattach": [(1000, 40, [27])],
    "predetach": [(100, 40, [27])],
    "predispense": [(100, 40, [30]), (30, 40, [28]), (100, 40, [30])],
    "preparingpressure": [(100, 40, [31]), (100, 40, [30]), (100, 40, [31])],
    "preparingvacuum": [(100, 40, [31]), (12000, 40, [30])],
    "presafestate": [(100, 40, [30]), (500, 40, [28])],
    "prewashinput": [(100, 40, [31]), (100, 40, [30])],
    "primepump": [(100, 40, [30])],
    "releasepressure": [(100, 40, [31]), (200, 40, [31])],
    "releasevacuum": [(100, 40, [31]), (100, 40, [27])],
    "reversepump": [(4000, 40, [30]), (500, 40, [28])],
    "washinput": [(100, 40, [30]), (1000, 40, [28]), (100, 40, [30])],
  },
  "low_volume": {
    "detachrecovery": [
      (21, 40, [25]),
      (15, 40, [27]),
      (14, 40, [30]),
      (77, 40, [31]),
    ]
    * 5
    + [(100, 40, [31])],
    "dispense_100nL": [
      (27, 40, [30]),
      (14, 40, [31]),
      (15, 40, [29]),
      (21, 40, [29]),
      (13, 40, [31]),
    ],
    "dispense_500nL": [
      (77, 40, [26]),
      (14, 40, [27]),
      (15, 40, [25]),
      (21, 40, [29]),
      (13, 40, [31]),
    ],
    "postprime": [
      (13, 40, [31]),
      (12, 40, [30]),
      (22, 40, [30]),
      (14, 40, [31]),
      (15, 40, [29]),
      (21, 40, [29]),
      (13, 40, [31]),
      (12, 40, [30]),
      (22, 40, [30]),
      (14, 40, [31]),
      (15, 40, [29]),
      (21, 40, [29]),
      (13, 40, [31]),
    ],
    "preattach": [(100, 40, [31])],
    "predetach": [
      (100, 40, [24]),
      (100, 40, [26]),
      (100, 40, [30]),
      (100, 40, [31]),
    ],
    "primepump": [
      (77, 40, [26]),
      (14, 40, [27]),
      (15, 40, [25]),
      (21, 40, [29]),
      (13, 40, [31]),
    ],
    "reversepump": [(21, 40, [25]), (15, 40, [27]), (14, 40, [30]), (77, 40, [31])],
    "washinput": [(35, 40, [25]), (1, 40, [27]), (25, 40, [30]), (1, 40, [31])] * 100
    + [(35, 40, [25])],
  },
  "low_volume_pfe": {
    "detachrecovery": [
      (81, 40, [29]),
      (153, 40, [25]),
      (25, 40, [27]),
      (51, 40, [26]),
      (85, 40, [30]),
      (25, 40, [31]),
    ]
    * 5
    + [(100, 40, [31])],
    "dispense_100nL": [
      (81, 40, [30]),
      (127, 40, [30]),
      (25, 40, [31]),
      (51, 40, [29]),
      (75, 40, [29]),
      (25, 40, [31]),
    ],
    "dispense_500nL": [
      (83, 40, [30]),
      (151, 40, [26]),
      (25, 40, [27]),
      (50, 40, [25]),
      (86, 40, [29]),
      (25, 40, [31]),
    ],
    "postprime": [
      (25, 40, [31]),
      (81, 40, [30]),
      (127, 40, [30]),
      (25, 40, [31]),
      (51, 40, [29]),
      (75, 40, [29]),
      (25, 40, [31]),
      (81, 40, [30]),
      (127, 40, [30]),
      (25, 40, [31]),
      (51, 40, [29]),
      (75, 40, [29]),
      (25, 40, [31]),
    ],
    "preattach": [(100, 40, [31])],
    "predetach": [
      (100, 40, [24]),
      (100, 40, [26]),
      (100, 40, [30]),
      (100, 40, [31]),
    ],
    "primepump": [
      (83, 40, [30]),
      (153, 40, [26]),
      (25, 40, [27]),
      (50, 40, [25]),
      (86, 40, [29]),
      (25, 40, [31]),
    ],
    "reversepump": [
      (200, 40, [25]),
      (55, 40, [27]),
      (33, 40, [26]),
      (77, 40, [30]),
      (34, 40, [31]),
      (81, 40, [29]),
    ],
    "washinput": [(35, 40, [25]), (1, 40, [27]), (25, 40, [30]), (1, 40, [31])] * 100
    + [(35, 40, [25])],
  },
}

# Default plate geometry for coordinate generation
DEFAULT_PLATE_GEOMETRY = {
  "a1_x": 14.35,
  "a1_y": 11.23,
  "row_pitch": 9.02,
  "col_pitch": 9.02,
  "rows": 8,
  "cols": 12,
  "z": 44.331,
}
