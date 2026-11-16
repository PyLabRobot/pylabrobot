"""Sphinx directives that render the device registry (``docs/_static/devices.json``).

``conf.py`` loads ``plr_devices.directive``, the submodule holding ``setup``, so that
``plr_devices.data`` and ``plr_devices.html`` stay importable without Sphinx.
"""
