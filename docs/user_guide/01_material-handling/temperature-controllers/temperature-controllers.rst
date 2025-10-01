Temperature Controllers
=======================

Temperature controllers are defined as **machines with one or both** of these **features**:

- `heating` 
- `actively cooling`

a **material** or **enclosed volume** from a room-temperature baseline (≈20-25°C).

Based on this definition machines that include temperature control, but are not primarily temperature controllers, should build on top of the `TemperatureController` definition.

These multi-functional machines can be described as "composite machines".
Examples of such machines include:

- Heater shakers: `shake` + `heat` (e.g. Inheco Thermoshake)
- qPCR machines: `measure fluorescence` + `heat` + `cool` (e.g. Thermo Fisher Scientific QuantStudio 5)
- Smart storage machines: `store` + `heat` + (`cool`) (e.g. Thermo Fisher Scientific Cytomats)

Temperature Controller machines can be implemented with a variety of heating/cooling technologies suited to different workflows. 

------------------------------------------

Actuation technologies
----------------------

Multiple technologies can be used to implement temperature control, each with its own advantages and limitations. 
The two most common types are:

1. **Thermoelectric (Peltier) modules**  
   are solid-state devices that pump heat via the Peltier effect, enabling both heating and cooling by reversing current flow.
   Compact and modular, they mount directly on robotic decks.  
   
   *Examples:* 

   - Inheco Cold Plate Air Cooled (CPAC) Heater/Cooler (not yet supported in PLR)
   - Hamilton Heater Cooler (HHC)
   - Opentrons Temperature Module GEN2

- **Pros:** Bidirectional control; fast response; minimal footprint   
- **Cons:** Limited ΔT from ambient (±65°C max); efficiency drops near extremes; requires heatsinking/ventilation

2. **Liquid-circulation systems**  
   use external chillers or heaters to pump fluid (water or glycol) through channels around a sample block, delivering uniform, stable temperatures well below and above ambient.  
   
   *Examples:*

   - Inheco Cold Plate Liquid Cooled (CPLC) Heater/Cooler (not yet supported in PLR)

- **Pros:** Broad temperature range; excellent uniformity; precise PID control  
- **Cons:** Bulky; requires plumbing and space; higher cost

------------------------------------------

Implementation
--------------

Backend
^^^^^^^

PyLabRobot programmatically defines Temperature Controller machines based on the :class:`~pylabrobot.temperature_controlling.temperature_controller.TemperatureController` base class.

e.g.:

.. code-block:: python

   from pylabrobot.temperature_controlling.temperature_controller import (
     TemperatureControllerBackend
   )

   hhc_backend = HamiltonHeaterCoolerBackend(device_address="/dev/ttyUSB0")

Resource Model
^^^^^^^^^^^^^^

Physically speaking, PLR models most standalone temperature controllers as either `ResourceHolder` or `PlateHolder` objects.
I.e. these machines have physical dimensions and can hold plates or other resources in a specified `child_location`.

e.g.:

.. code-block:: python

    def Hamilton_heater_cooler_resource(name: str) -> PlateHolder:
        """
        Hamilton cat. no.: 6601900-01
        """
        return PlateHolder(
            name=name,
            size_x=145.5,
            size_y=104.0,
            size_z=67.8,
            child_location=Coordinate(11.5, 8.0, 67.8),
            model="hamilton_heater_cooler",
            pedestal_size_z=0,
        )

    hhc_resource_model = Hamilton_heater_cooler_resource(
        name="Hamilton Heater Cooler no1"
    )

Frontend
^^^^^^^^

The frontend then enables fast and user-friendly access to the temperature controller's functionality and storing of complete machine interfaces using familiar names.

e.g.:

.. code-block:: python

   Hamilton_heater_cooler = TemperatureController(
      backend=hhc_backend,
      resource_model=hhc_resource_model
      )


   # Action command:
   Hamilton_heater_cooler.set_temperature(37)

   # Read command:
   current = Hamilton_heater_cooler.get_temperature()


.. toctree::
   :maxdepth: 1
   :hidden:

   ot-temperature-controller
   hamilton-heater-cooler
   inheco
