"""TCS modules the PreciseFlex driver requires.

Derived from the TCS source: every command this driver issues is defined in the base
``Cmd.gpl`` or in ``PARobot.gpl`` - none in ``Load_save.gpl``, ``StereoVision.gpl``
(IntelliGuide), or the Auto-Center plug-in, so those are not required by this driver.
The base command server is implicit (you cannot connect or run ``version`` without it).

``PARobot.gpl`` carries two modules and the driver uses commands from both: ``PARobot``
(plate handling, freedrive, park, rail, config) and ``SSGrip`` (``GraspPlate``,
``ReleasePlate``, ``IsFullyClosed``). Both ship in that one file, so installing
``Tcp_cmd_server_pa`` provides both - but the driver depends on each, so both are checked.

A missing required module is the usual cause of ``-2805 *Unknown command*``; checking
presence at setup turns that into a clear "install it from Brooks" message up front.
"""

# required module (name substring) -> (what it provides, project that ships it)
REQUIRED_MODULES = {
  "PARobot": ("plate, freedrive, park, rail", "Tcp_cmd_server_pa"),
  "SSGrip": ("force-limited grasp and release, closed-state sensor", "Tcp_cmd_server_pa"),
}


def missing_required_modules(modules: tuple) -> list:
  """Required modules absent from the loaded set, as (module, provides, project)."""
  loaded = " ".join(modules)
  return [
    (module, provides, project)
    for module, (provides, project) in REQUIRED_MODULES.items()
    if module not in loaded
  ]
