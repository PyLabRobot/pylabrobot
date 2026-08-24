# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- `StackerRetrieval` capability (`pylabrobot.capabilities.automated_retrieval.StackerRetrieval`) for sequential ("stacking access") plate storage: one or more single-ended LIFO `ResourceStack` stacks plus a loading tray, with `downstack`/`upstack` operations and a `StackerBackend` interface (plus `StackerChatterboxBackend`). Intended for devices like the Agilent BenchCel and HighRes MicroServe (#1113).
- `AutomatedRetrieval` base capability (`pylabrobot.capabilities.automated_retrieval.AutomatedRetrieval`) that owns the loading tray and the plate-movement plumbing shared by the random-access `RandomAccessRetrieval` and the sequential `StackerRetrieval`. The former random-access `AutomatedRetrieval` is now `RandomAccessRetrieval` and extends this base.
- HighRes Biosolutions MicroSpin centrifuge backend (`pylabrobot.centrifuge.highres.MicroSpinBackend`) speaking the device's ASCII command/response protocol over TCP/1000, plus a `MicroSpin(...)` factory.
- In-process `MicroSpinMockServer` (`pylabrobot.centrifuge.highres.mock_server`) that faithfully emulates the MicroSpin's wire protocol -- including the firmware's "`status` blocks until the spindle has stopped" semantics and the low-G spin-down-detection hang -- usable as a Python async context manager or runnable as a script (`python -m pylabrobot.centrifuge.highres.mock_server`) for `nc`/`telnet` debugging.
- `MicroSpinBackend.reset()` recovery helper that issues `abort` -> `clearbuttonabort` -> `status`, using the last as the gate that genuinely confirms the rotor has stopped.
- User guide notebook for the MicroSpin (`docs/user_guide/01_material-handling/centrifuge/highres_microspin.ipynb`).
- `Plate`: optional `stacking_z_height` parameter -- the per-plate vertical pitch when plates are stacked directly on top of each other (`size_z` minus the nesting overlap), mirroring `NestedTipRack.stacking_z_height`. Because it is a physical dimension, plates that differ in it no longer compare equal; `Plate` also now serializes `stacking_z_height` and the pre-existing `plate_type` so both round-trip through `deserialize`/`copy`. (#1110)
- `ResourceStack`: bare plates stacked in the z direction now nest into one another by their `stacking_z_height` (a stack of `N` identical plates is `size_z + (N - 1) * stacking_z_height` tall, for both `get_size_z()` and child placement). Plates without a `stacking_z_height`, and plates wearing a lid, do not nest, so existing behaviour is unchanged. (#1112)

- Background reader task on `pylabrobot.hamilton.transport.tcp.HamiltonTCPClient` that owns the socket for the session, so `on_event` subscribers receive events between commands and a response arriving with no command waiting is dropped and logged instead of being handed to the next command (#1195).
- Command serialization on `HamiltonTCPClient`: one command is in flight at a time. The lock spans write through terminal response and is released before the response is decoded, because error enrichment sends further commands through the same path (#1195).
- `ObjectRegistry.clear()` (`pylabrobot.hamilton.transport.tcp.introspection`), used to drop path and address mappings that are scoped to a single connected session (#1195).

### Fixed

- Imported `unittest.mock` in `pylabrobot/centrifuge/centrifuge_tests.py` (pre-existing bug that prevented the test class from running).
- `HamiltonTCPClient` no longer retransmits a command after a failed read. A read timeout on a slow motion command previously re-sent it, which could execute the motion twice (#1195).
- `HamiltonTCPClient.setup()` now resets all per-session state (client id, sequence numbers, instrument addresses, object registry) rather than carrying it into the new session, and refuses to run on an already-connected client instead of leaking the socket (#1195).
- `HamiltonTCPClient` no longer recurses without bound when a device fails the introspection queries that error enrichment itself issues. Enrichment is now non-re-entrant and falls back to the static HC_RESULT tables, so a degraded instrument yields a terse error instead of a `RecursionError` (#1195).
- `HamiltonTCPClient` no longer fails every command after the device sends a HARP control frame. MLPrep firmware sends one (options, no HOI body) shortly after registration; it was parsed as a command response and killed the reader. Frames with no routable message are skipped, as are unparseable frames, which is safe because frames are length-prefixed and consumed whole (#1195).

### Changed

- `HamiltonTCPClient` no longer reconnects automatically; `auto_reconnect` and `max_reconnect_attempts` are gone from its constructor. Recovery is `await client.stop()` followed by `await client.setup()`, matching every other transport in the library. `is_connected` remains for callers implementing their own policy (#1195).
- `TCPCommand` declares `Response` and `uses_physical_channels` as class attributes instead of the transport inferring them by attribute probing. Commands with per-channel firmware errors must set `uses_physical_channels = True` to raise `ChannelizedError` (#1195).

## 0.2.1

### Added

- Tecan Infinite 200 PRO plate reader backend (Infinite M Plex) (#797)
- Tecan Spark plate reader backend (#798)
- `height_volume_data` attribute on `Container` with piecewise-linear interpolation (#938)
- `eppendorf_96_wellplate_500ul_Vb` (#945)
- `thermo_TS_nalgene_1_troughplate_300mL_Fb` (#939)

### Fixed

- Visualizer: PlateAdapter with hole grid and magnetic rack styling (#946)
- Single persistent reader thread in Hamilton backend (#952)

### Changed

- Updated Corning 3603 with empirical cLLD `height_volume_data` (#948)
- Optional deps are now truly optional (#941)
- Unpinned dependency versions (#942)
- Renamed Hamilton trough 60mL/200mL to correct SI casing (#947)
- ARP fallback and streaming for SiLA discovery (#940)

### Removed

- GUI leftovers
