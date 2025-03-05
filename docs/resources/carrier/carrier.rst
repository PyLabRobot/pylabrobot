Carrier
================

A **carrier** is a component of the deck layout that holds labware such as plates, tip racks, tubes, or troughs/reservoirs. Carriers serve as modular, standardized platforms that allow for flexible deck configurations, ensuring that labware is positioned accurately.

Each carrier type is optimized for specific resource types (like plates, tip racks, etc.), allowing for efficient sample handling and workflow customization.
The :class:`~pylabrobot.resources.carrier.Carrier` class provides a base structure from which specialized carrier types inherit, ensuring modularity and flexibility in system configurations.

-----

Subclasses of `Carrier`
-----------------------

.. toctree::
   :maxdepth: 4

   plate-carrier/plate_carriers
   mfx-carrier/mfx_carriers

-----

Carrier Types
-------------

- **Plate Carriers**: These carriers hold standard microplates and deep-well plates, ensuring proper alignment for automated pipetting and handling.
- **MFX Carriers**: A specific type of carrier designed for flexible reconfiguration of that carrier layout by removably screwing "MFX Modules" onto the MFX Carrier of choice.
