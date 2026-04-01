.. currentmodule:: pylabrobot.hamilton

pylabrobot.hamilton package
===========================

Heater Cooler
-------------

.. currentmodule:: pylabrobot.hamilton.heater_cooler

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    HamiltonHeaterCooler
    HamiltonHeaterCoolerDriver
    HamiltonHeaterCoolerTemperatureBackend


HEPA Fan
--------

.. currentmodule:: pylabrobot.hamilton.only_fans

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    HamiltonHepaFan
    HamiltonHepaFanDriver
    HamiltonHepaFanFanBackend
    HamiltonHepaFanChatterboxBackend


Heater Shaker
-------------

.. currentmodule:: pylabrobot.hamilton.heater_shaker

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    HamiltonHeaterShaker
    HamiltonHeaterShakerDriver
    HamiltonHeaterShakerShakerBackend
    HamiltonHeaterShakerTemperatureBackend
    HamiltonHeaterShakerBox
    HamiltonHeaterShakerInterface


STAR Liquid Handler
-------------------

.. currentmodule:: pylabrobot.hamilton.liquid_handlers.star

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    STAR

.. currentmodule:: pylabrobot.hamilton.liquid_handlers.star.pip_backend

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    STARPIPBackend

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.pip_backend.STARPIPBackend.PickUpTipsParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.pip_backend.STARPIPBackend.DropTipsParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.pip_backend.STARPIPBackend.AspirateParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.pip_backend.STARPIPBackend.DispenseParams
   :members:

.. currentmodule:: pylabrobot.hamilton.liquid_handlers.star.pip_backend

.. autosummary::
  :toctree: _autosummary
  :nosignatures:

    LLDMode

.. currentmodule:: pylabrobot.hamilton.liquid_handlers.star.head96_backend

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    STARHead96Backend

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.head96_backend.STARHead96Backend.PickUpTips96Params
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.head96_backend.STARHead96Backend.DropTips96Params
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.head96_backend.STARHead96Backend.Aspirate96Params
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.head96_backend.STARHead96Backend.Dispense96Params
   :members:

.. currentmodule:: pylabrobot.hamilton.liquid_handlers.star.iswap

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    iSWAPBackend

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.iswap.iSWAPBackend.ParkParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.iswap.iSWAPBackend.CloseGripperParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.iswap.iSWAPBackend.PickUpParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.iswap.iSWAPBackend.DropParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.star.iswap.iSWAPBackend.MoveToLocationParams
   :members:
