import os
from os.path import dirname, join, abspath
PACKAGE_PATH = abspath(dirname(dirname(__file__)))
LAY_BACKUP_DIR = join(PACKAGE_PATH, 'LAY-BACKUP')
if not os.path.exists(LAY_BACKUP_DIR):
    os.mkdir(LAY_BACKUP_DIR)
OEM_STAR_PATH = join(PACKAGE_PATH, 'STAR-OEM')
OEM_LAY_PATH = join(OEM_STAR_PATH, 'VENUS_Method', 'STAR_OEM_Test.lay')
OEM_HSL_PATH = join(OEM_STAR_PATH, 'VENUS_Method', 'STAR_OEM_Test.hsl')
OEM_RUN_EXE_PATH = 'C:\\Program Files (x86)\\HAMILTON\\Bin\\HxRun.exe'
from .interface import *
from .deckresource import *
from .oemerr import *
