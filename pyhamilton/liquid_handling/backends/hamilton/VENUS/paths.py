import os
from pyhamilton import PACKAGE_PATH

LAY_BACKUP_DIR = os.path.join(PACKAGE_PATH, 'LAY-BACKUP')
if not os.path.exists(LAY_BACKUP_DIR):
    os.mkdir(LAY_BACKUP_DIR)
OEM_STAR_PATH = os.path.join(PACKAGE_PATH, 'star-oem')
if not (os.path.exists(OEM_STAR_PATH)
		and os.path.exists(os.path.join(OEM_STAR_PATH, 'RunHSLExecutor.dll'))
		and os.path.exists(os.path.join(OEM_STAR_PATH, 'HSLHttp.dll'))):
    raise FileNotFoundError('pyhamilton requires .../site-packages/pyhamilton/STAR-OEM, distributed separately.')
OEM_LAY_PATH = os.path.join(OEM_STAR_PATH, 'VENUS_Method', 'STAR_OEM_Test.lay')
OEM_HSL_PATH = os.path.join(OEM_STAR_PATH, 'VENUS_Method', 'STAR_OEM_noFan.hsl')
OEM_RUN_EXE_PATH = 'C:\\Program Files (x86)\\HAMILTON\\Bin\\HxRun.exe'
