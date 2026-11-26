"""A generic Biomek i7: what a simulation is given when it has no instrument's own files.

Made from one instrument's declared configuration and cold-start recording, with every serial
number and name removed. Its physical values (motor tables, conversions, travel, geometry,
settings, the default deck's calibration and layout) are that instrument's, so another
instrument's differ: drive an instrument with its own files. The configuration is marked
`generic` and carries no serial numbers, so a live session refuses it (`BiomekI7Driver.discover`).
"""

from pathlib import Path

HERE = Path(__file__).parent

GENERIC_CONFIGURATION = HERE / "biomek_i7_generic_configuration.json"
GENERIC_RECORDING = HERE / "biomek_i7_generic_coldstart.json"
