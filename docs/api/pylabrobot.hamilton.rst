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


Vantage Liquid Handler
----------------------

.. currentmodule:: pylabrobot.hamilton.liquid_handlers.vantage

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    Vantage

.. currentmodule:: pylabrobot.hamilton.liquid_handlers.vantage.pip_backend

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    VantagePIPBackend

.. autoclass:: pylabrobot.hamilton.liquid_handlers.vantage.pip_backend.VantagePIPBackend.PickUpTipsParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.vantage.pip_backend.VantagePIPBackend.DropTipsParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.vantage.pip_backend.VantagePIPBackend.AspirateParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.vantage.pip_backend.VantagePIPBackend.DispenseParams
   :members:

.. currentmodule:: pylabrobot.hamilton.liquid_handlers.vantage.head96_backend

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    VantageHead96Backend

.. autoclass:: pylabrobot.hamilton.liquid_handlers.vantage.head96_backend.VantageHead96Backend.PickUpTipsParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.vantage.head96_backend.VantageHead96Backend.DropTipsParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.vantage.head96_backend.VantageHead96Backend.AspirateParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.vantage.head96_backend.VantageHead96Backend.DispenseParams
   :members:

.. currentmodule:: pylabrobot.hamilton.liquid_handlers.vantage.ipg

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    IPGBackend

.. autoclass:: pylabrobot.hamilton.liquid_handlers.vantage.ipg.IPGBackend.PickUpParams
   :members:

.. autoclass:: pylabrobot.hamilton.liquid_handlers.vantage.ipg.IPGBackend.DropParams
   :members:
