"""Firmware versions confirmed to work with this driver.

The firmware version is queried via the I3 command (request_firmware_version) during setup().
If the connected device runs a version not in this list, a warning
is logged. Please report untested versions that work so they can
be added.

Only the major.minor version is checked (e.g. "1.10"), not the full
I3 response string (e.g. "1.10 18.6.4.1361.772"), because the second
part is a type definition number that varies by hardware revision and
model while the firmware behavior is determined by the version number.
"""

CONFIRMED_FIRMWARE_VERSIONS = [
  "1.10",
]
