import os, sys
from pathlib import Path

# Configure pythonnet to use the correct Mono runtime (MacOSx)
# os.environ['PYTHONNET_PYDLL'] = '/opt/homebrew/lib/libmonosgen-2.0.dylib'

import clr
clr.AddReference("System")
clr.AddReference("System.Reflection")
import System
from System.Reflection import Assembly
from System import Guid

dll_path = Path(__file__).parent / "firmware_dlls"
sys.path.append(str(dll_path))

# Load core Nimbus firmware assemblies
NIMBUSCOREDLL = Assembly.LoadFrom(str(dll_path / "Hamilton.Module.NimbusCORE.dll"))
COMLINKDLL = Assembly.LoadFrom(str(dll_path / "Hamilton.Components.TransportLayer.ComLink.dll"))
