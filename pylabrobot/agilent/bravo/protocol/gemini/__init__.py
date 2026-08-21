"""Gemini wire protocol -- the framed TCP protocol spoken by Darwin-generation
Bravo firmware.

Submodules:

- ``enums`` -- command types, subcommand tables, NAK codes, motor states, and
  other wire-level constants.
- ``framing`` -- the 8-byte outer TCP frame header and its payload wrappers.
- ``packet`` -- the 8-byte Gemini packet codec and controller-tree addressing.
- ``instruction`` -- the 4-word motion/delay/tips instruction codec.
- ``errors`` -- protocol-level exceptions and NAK-to-error-type mapping.
- ``engine`` -- the synchronous request/response dispatcher that drives a
  connected transport.
"""

from __future__ import annotations
