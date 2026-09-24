# Opentrons labware fixtures

These are schema-2, version-1 definitions from [Opentrons commit
5b51a98ce736b2bb5aff780bf3fdf91941a038fa](https://github.com/Opentrons/opentrons/tree/5b51a98ce736b2bb5aff780bf3fdf91941a038fa/shared-data/labware/definitions/2),
the revision used by `pylabrobot/resources/opentrons/load.py`.
The upstream Apache-2.0 license and attribution are included in `LICENSE` and `NOTICE`.

The autouse fixture in `pylabrobot/conftest.py` reads these files instead of downloading
labware or using the machine's temporary cache. Each load gets a fresh dictionary.
Tests using another Opentrons definition must include its JSON here; missing fixtures
fail instead of falling back to a network request.
