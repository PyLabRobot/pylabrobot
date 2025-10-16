import os, sys
from pathlib import Path

# Configure pythonnet to use the correct Mono runtime (MacOSx)
# os.environ['PYTHONNET_PYDLL'] = '/opt/homebrew/lib/libmonosgen-2.0.dylib'

import clr
clr.AddReference("System")
clr.AddReference("System.Reflection")
import System  # type: ignore
from System.Reflection import Assembly  # type: ignore
from System import Guid  # type: ignore

dll_path = Path(__file__).parent / "firmware_dlls"
sys.path.append(str(dll_path))

# Load core Nimbus firmware assemblies, pre-loading dependencies
NIMBUSCOREDLL = Assembly.LoadFrom(str(dll_path / "Hamilton.Module.NimbusCORE.dll"))
COMLINKDLL = Assembly.LoadFrom(str(dll_path / "Hamilton.Components.TransportLayer.ComLink.dll"))
IOBOARDDLL = Assembly.LoadFrom(str(dll_path / "Hamilton.Module.IOBoard.dll"))
PROTOCOLSDLL = Assembly.LoadFrom(str(dll_path / "Hamilton.Components.TransportLayer.Protocols.dll"))
