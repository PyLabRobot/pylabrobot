"""
Pyhamilton
"""
import os
from os.path import dirname, join, abspath
PACKAGE_PATH = abspath(dirname(__file__))
LAY_BACKUP_DIR = join(PACKAGE_PATH, 'LAY-BACKUP')
if not os.path.exists(LAY_BACKUP_DIR):
    os.mkdir(LAY_BACKUP_DIR)
OEM_STAR_PATH = join(PACKAGE_PATH, 'STAR-OEM')
if not (os.path.exists(OEM_STAR_PATH)
		and os.path.exists(os.path.join(OEM_STAR_PATH, 'RunHSLExecutor.dll'))
		and os.path.exists(os.path.join(OEM_STAR_PATH, 'HSLHttp.dll'))):
    raise FileNotFoundError('pyhamilton requires .../site-packages/pyhamilton/STAR-OEM, distributed separately.')
OEM_LAY_PATH = join(OEM_STAR_PATH, 'VENUS_Method', 'STAR_OEM_Test.lay')
OEM_HSL_PATH = join(OEM_STAR_PATH, 'VENUS_Method', 'STAR_OEM_noFan.hsl')
OEM_RUN_EXE_PATH = 'C:\\Program Files (x86)\\HAMILTON\\Bin\\HxRun.exe'
from .interface import *
from .deckresource import *
from .oemerr import *
