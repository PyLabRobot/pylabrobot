"""
Pyhamilton
"""
import os
import shutil
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
from .utils import *
from autoconfig import pyhamiltonconfig

this_file_dir = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.abspath(os.path.join(this_file_dir))
LIBRARY_DIR = os.path.join(PACKAGE_DIR, 'library')
exe_http = os.path.join(PACKAGE_DIR, 'bin', 'Hamilton HSLHttp Library Installer Version 2.7.exe')
exe_json = os.path.join(PACKAGE_DIR, 'bin', 'HSLJson Library v1.6 Installer.exe')


def full_paths_list(directory_abs_path):
    list_files = os.listdir(directory_abs_path)
    list_file_paths = [directory_abs_path + '\\' + file for file in list_files]
    return list_file_paths


def autoconfig():
    print("Automatically configuring your PyHamilton installation")
    os.startfile(exe_http)
    os.startfile(exe_json)
    
    hamilton_lib_dir = os.path.abspath('C:/Program Files (x86)/HAMILTON/Library')

    
    def recursive_copy(source_dir, target_dir):
        source_list = full_paths_list(source_dir)
        for file in source_list:
            if os.path.isfile(file):
                shutil.copy(file, target_dir + '//' + os.path.basename(file))
            if os.path.isdir(file):
                target = target_dir + '//' + os.path.basename(file)
                if not os.path.exists(target):
                    os.mkdir(target)
                recursive_copy(source_dir + '//' + os.path.basename(file), target)            
    
    recursive_copy(LIBRARY_DIR, hamilton_lib_dir)        
    print("Configuration completed")
