Hamilton STAR
=============

.. tabs::

   .. tab:: STAR (54 tracks)

      .. list-table::
         :widths: 50 50
         :header-rows: 1

         * - Summary
           - Image
         * - * **OEM Link:** `Hamilton Company <https://www.hamiltoncompany.com/automated-liquid-handling/platforms/microlab-star>`_
             * **Communication Protocol / Hardware:** USB / USB-A/B
             * **Communication Level:** Firmware
             * **VID:PID:** 0x08AF:0x8000
             * **Deck Size:** 54 tracks
           - .. figure:: img/star_old.png
                :width: 320px
                :align: center
                
                STAR with 54-track deck

   .. tab:: STARlet (30 tracks)

      .. list-table::
         :widths: 50 50
         :header-rows: 1

         * - Summary
           - Image
         * - * **OEM Link:** `Hamilton Company <https://www.hamiltoncompany.com/automated-liquid-handling/platforms/microlab-starlet>`_
             * **Communication Protocol / Hardware:** USB / USB-A/B
             * **Communication Level:** Firmware
             * **VID:PID:** 0x08AF:0x8000
             * **Deck Size:** 30 tracks
           - .. figure:: img/starlet_old.png
                :width: 320px
                :align: center
                
                STARlet with 30-track deck

About the Machine(s)
--------------------

The Hamilton Microlab STAR is a modular liquid handling workstation designed for high-throughput laboratory automation.
(The STARlet is a smaller version of the STAR that uses the exact same commands.)

It is particularly popular in the PyLabRobot community due to its flexibility, robustness, extensive sensor systems, and well-documented firmware.
Both the STAR and the STARlet share the same modular architecture and can be configured with:

* **Pipetting Tools:** 
   * 1000µL channels (4-16)
   * 5ml channels (1-4)
* **Optional Modules:**
   * "Multi-Probe Head": 96-Head / 384-Head
   * Autoload with barcode reading (1D / 2D reader)
   * iSWAP (for plate handling)
   * Imaging Channel
   * Tube Twister & Decapper
* **Key Technologies:**
   * CO-RE (Compression-induced O-Ring Expansion) tip attachment
   * dual liquid level detection (capacitive and pressure-based)
   * monitored air displacement (MAD)
   * anti-droplet control (ADC, for volatile solvents)
   * x/y/z motor resolution = 0.1 mm

----------------------

Table of Contents
-----------------

.. toctree::
   :maxdepth: 2

   core-features/intro
   modules/intro
   probing/intro
   foil
   debug
   hardware/index

