# Nimbus Development Tools

## Requirements

**DLL Dependencies**: This module requires Hamilton firmware DLL files to be copied to the target directory. The `firmware_assemblies.py` file depends on these DLLs:
- `Hamilton.Module.NimbusCORE.dll`
- `Hamilton.Components.TransportLayer.ComLink.dll`
- `Hamilton.Module.IOBoard.dll`

Ensure these DLL files are present in the `pylabrobot/liquid_handling/backends/hamilton/nimbus/firmware_dlls/` directory before running any tests.

## Test Notebook

The `dll_comlink_test.ipynb` notebook demonstrates basic Hamilton Nimbus instrument control via TCP communication. It performs the following operations:

1. **Connection Setup**: Establishes TCP connection to Nimbus instrument (default: 192.168.100.100:2000)
2. **Module Discovery**: Retrieves and displays available instrument modules (NimbusCORE, IOBoard, etc.)
3. **NimbusCORE Initialization**: Creates a NimbusCORE control instance for pipetting operations
4. **Door Management**:
   - Checks door lock status
   - Locks door for safe operation
   - Unlocks door when complete
5. **Pipettor Operations**:
   - Preinitializes the pipetting system
   - Parks all channels
6. **Cleanup**: Properly closes the TCP connection
