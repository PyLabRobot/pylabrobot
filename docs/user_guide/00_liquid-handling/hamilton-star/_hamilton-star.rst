Hamilton STAR
=============

.. tabs::

   .. tab:: STAR (54 tracks)

      .. list-table::
         :widths: 60 40
         :header-rows: 1

         * - Summary
           - Image
         * - * **OEM Link:** `Hamilton Company <https://www.hamiltoncompany.com/automated-liquid-handling/platforms/microlab-star>`_
             * **Communication Protocol / Hardware:** USB / USB-A/B
             * **Communication Level:** Firmware
             * **VID:PID:** 0x10C4:0xEA60
             * **Deck Size:** 54 tracks
             * **Generation:** Legacy (pre-2025) or "Fluid Motion" (2025+)
             * **Independent Channels:** 1000µL (4-16 channels) or 5000µL
             * **Multi-channel Heads:** 96-channel or 384-channel
             * **Optional Modules:** Autoload & Loading Tray, Barcode Reader (1D/2D), iSWAP, CO-RE Grippers
           - .. figure:: img/star_old.png
                :width: 320px
                :align: center
                
                Hamilton STAR with 54-track deck

   .. tab:: STARlet (30 tracks)

      .. list-table::
         :widths: 60 40
         :header-rows: 1

         * - Summary
           - Image
         * - * **OEM Link:** `Hamilton Company <https://www.hamiltoncompany.com/automated-liquid-handling/platforms/microlab-starlet>`_
             * **Communication Protocol / Hardware:** USB / USB-A/B
             * **Communication Level:** Firmware
             * **VID:PID:** 0x10C4:0xEA60
             * **Deck Size:** 30 tracks
             * **Generation:** Legacy (pre-2025) or "Fluid Motion" (2025+)
             * **Independent Channels:** 1000µL (4-16 channels) or 5000µL
             * **Multi-channel Heads:** 96-channel or 384-channel
             * **Optional Modules:** Autoload & Loading Tray, Barcode Reader (1D/2D), iSWAP, CO-RE Grippers
           - .. figure:: img/starlet_old.png
                :width: 320px
                :align: center
                
                Hamilton STARlet with 30-track deck

About the Machine(s)
--------------------

The Hamilton STAR is a modular liquid handling platform designed for high-throughput automation. It's available in two deck sizes (STAR with 54 tracks and STARlet with 30 tracks) and can be configured with various pipetting channels, multi-channel heads, and optional automation modules.

PyLabRobot provides comprehensive support for STAR systems through firmware-level communication over USB.

.. toctree::
   :maxdepth: 2

   core-features/intro
   modules/intro
   probing/intro
   foil
   debug
   hardware/index

