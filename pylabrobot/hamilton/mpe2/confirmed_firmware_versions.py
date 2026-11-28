"""Firmware versions this driver has run on.

``setup()`` reads the main controller's module version and logs a warning when it is not listed
here. Please add a version once the driver has worked on it.
"""

CONFIRMED_FIRMWARE_VERSIONS = [
  # Over USB: setup, queries, initialize, the vacuum, the plate steps and processing.
  "V1.9.5.619 95600-02 Rev U",
]
