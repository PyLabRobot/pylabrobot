"""Gemini wire protocol — pure-Python replacement for Agilent's GeminiAPI.dll.

Subpackages:
    enums       — CommandTypes, subcommand IDs, NAK codes, motor states, ...
    framing     — TCP frame header pack/unpack (8-byte 0xAAAA-prefixed frames)
    packet      — 8-byte Gemini packet pack/unpack; InstructionAddress
    instruction — 4-word motion/delay/tips instruction encoder
    params      — pointer-cached parameter database access (not yet implemented)
    errors      — protocol-level exceptions + NAK→BravoError mapping (not yet implemented)
"""
