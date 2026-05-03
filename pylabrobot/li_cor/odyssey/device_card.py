"""Odyssey Classic device card — model base.

Specs sourced from the operator's manual (publication 984-11712,
version 3.0, edition D). The ``identity`` slot is empty: each
deployment populates it with its own unit's PIDInst Handle URI,
landing page, and friendly name via an instance card::

    instance = DeviceCard.instance(identity={
      "pid": "http://hdl.handle.net/21.11157/...",
      "landing_page": "https://b2inst.gwdg.de/records/...",
      "name": "Odyssey, Lab 3",
    })
    card = ODYSSEY_CLASSIC_BASE.merge(instance)
"""

from __future__ import annotations

from pylabrobot.device_card import DeviceCard

ODYSSEY_CLASSIC_BASE = DeviceCard(
  name="Odyssey Classic",
  vendor="LI-COR Biosciences",
  model="9120",
  capabilities={
    "scanning": {
      "resolutions_um": [21, 42, 84, 169, 337],
      "quality_levels": ["lowest", "low", "medium", "high", "highest"],
      "intensity_range": [0.5, 10.0],
      "intensity_step": 0.5,
      "low_intensity_range": [0.5, 2.0],  # L0.5 to L2.0
      "scan_area_cm": [25, 25],
      "focus_offset_range_mm": [0.0, 4.0],
      "scanning_speed_cm_s": [5, 40],
    },
    "channels": {
      "700": {
        "laser_wavelength_nm": 685,
        "laser_type": "solid-state diode",
        "laser_peak_power_mw": 80,
        "detector": "silicon avalanche photodiode",
        "dichroic_split_nm": 750,
      },
      "800": {
        "laser_wavelength_nm": 785,
        "laser_type": "solid-state diode",
        "laser_peak_power_mw": 80,
        "detector": "silicon avalanche photodiode",
        "dichroic_split_nm": 810,
      },
    },
    "image_retrieval": {
      "format": "TIFF",
      "channels_per_file": 1,
      "storage_gb": 25,
    },
    "instrument_status": {
      "states": ["Idle", "Scanning", "Paused"],
      "lid_interlock": True,
    },
  },
  connection={
    "protocol": "http",
    "port": 80,
    "auth": "basic",
    "network": "10/100Base-T Ethernet",
    "discovery": "rendezvous/mdns",
    "cgi_base": "/scanapp/nonjava",
  },
)
