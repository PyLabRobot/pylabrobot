"""
Built-in commands, definitions of their parameters, and defaults.
"""

_channel_patt_16 = '1'*8 + '0'*8
_channel_patt_96 = '1'*96

_fan_port = 6
# on module load, scan COM ports to see if the usual fan COM number (6) has been reassigned by the OS
try:
    import serial.tools.list_ports
    for port in serial.tools.list_ports.comports():
        port_parse = str(port).split(' ')
        if 'Isolated' in port_parse and 'RS-485' in port_parse:
            _fan_port = int(port_parse[0][-1])
except Exception:
    pass

defaults_by_cmd = { # 'field':None indicates field is required when assembling command

    'initialize':('INITIALIZE', {
        'initializeAlways':0
    }),

    'channelTipPickUp':('PICKUP', {
        'tipSequence':'', # (string) leave empty if you are going to provide specific labwarePositions below
        'labwarePositions':'', # (string) leave empty if you are going to provide a sequence name above.'LabwareId1, positionId1; LabwareId2,positionId2; ....'
        'channelVariable':_channel_patt_16, # (string)  channel pattern e.g. '11110000'
        'sequenceCounting':0, # (integer) 0=don´t autoincrement,  1=Autoincrement
        'channelUse':1 # (integer) 1=use all sequence positions (no empty wells), 2=keep channel pattern
    }),

    'channelTipEject':('EJECT', {
        'wasteSequence':'', # (string) leave empty if you are going to provide specific labware-positions below or ejecting to default waste
        'labwarePositions':'', # (string) leave empty if you are going to provide a sequence name above.'LabwareId1, positionId1; LabwareId2,positionId2; ....'
        'channelVariable':_channel_patt_16, # (string) channel pattern e.g. "11110000"
        'sequenceCounting':0, # (integer) 0=don´t autoincrement,  1=Autoincrement.  Value omitted if ejecting to default waste
        'channelUse':1, # (integer) 1=use all sequence positions (no empty wells), 2=keep channel pattern
        'useDefaultWaste':0 # (integer) 0=eject to custom waste sequence,  1=Use default waste
    }),

    'channelAspirate':('ASPIRATE', {
        'aspirateSequence':'', # (string) leave empty if you are going to provide specific labware-positions below
        'labwarePositions':'', # (string) leave empty if you are going to provide a sequence name above. 'LabwareId1, positionId1; LabwareId2,positionId2; ....'
        'volumes':None, # (float or string) enter a single value used for all channels or enter an array of values for each channel like [10.0,15.5,11.2]
        'channelVariable':_channel_patt_16, # (string) channel pattern e.g. "11110000"
        'liquidClass':None, # (string)
        'sequenceCounting':0, # (integer) 0=don´t autoincrement,  1=Autoincrement
        'channelUse':1, # (integer) 1=use all sequence positions (no empty wells), 2=keep channel pattern
        'aspirateMode':0, # (integer) 0=Normal Aspiration, 1=Consecutive (don´t aspirate blowout), 2=Aspirate all 
        'capacitiveLLD':0, # (integer) 0=Off, 1=Max, 2=High, 3=Mid, 4=Low, 5=From labware definition
        'pressureLLD':0, # (integer) 0=Off, 1=Max, 2=High, 3=Mid, 4=Low, 5=From liquid class definition
        'liquidFollowing':0, # (integer) 0=Off , 1=On
        'submergeDepth':2.0, # (float) mm of immersion below liquid´s surface to start aspiration when using LLD
        'liquidHeight':1.0, # (float) mm above container´s bottom to start aspiration when not using LLD
        'maxLLdDifference':0.0, # (float) max mm height different between cLLD and pLLD detected liquid levels
        'mixCycles':0, # (integer) number of mixing cycles (1 cycle = 1 asp + 1 disp)
        'mixPosition':0.0, # (float) additional immersion mm below aspiration position to start mixing
        'mixVolume':0.0, # (float) mix volume
        'xDisplacement':0.0,
        'yDisplacement':0.0,
        'zDisplacement':0.0,
        'airTransportRetractDist':10.0, # (float) mm to move up in Z after finishing the aspiration at a fixed height before aspirating 'transport air'
        'touchOff':0, # (integer) 0=Off , 1=On
        'aspPosAboveTouch':0.0 # (float)  mm to move up in Z after touch off detects the bottom before aspirating liquid
    }),

    'channelDispense':('DISPENSE', {
        'dispenseSequence':'', # (string) leave empty if you are going to provide specific labware-positions below
        'labwarePositions':'', # (string) leave empty if you are going to provide a sequence name above. 'LabwareId1, positionId1; LabwareId2,positionId2; ....'
        'volumes':None, # (float or string) enter a single value used for all channels or enter an array of values for each channel like [10.0,15.5,11.2]
        'channelVariable':_channel_patt_16, # (string) channel pattern e.g. "11110000"
        'liquidClass':None, # (string)
        'sequenceCounting':0, # (integer) 0=don´t autoincrement,  1=Autoincrement
        'channelUse':1, # (integer) 1=use all sequence positions (no empty wells), 2=keep channel pattern
        'dispenseMode':8, # (integer) 0=Jet Part, 1=Jet Empty, 2=Surface Part, 3=Surface Empty, 4=Jet Drain tip, 8=From liquid class, 9=Blowout tip
        'capacitiveLLD':0, # (integer) 0=Off, 1=Max, 2=High, 3=Mid, 4=Low, 5=From labware definition
        'liquidFollowing':0, # (integer) 0=Off , 1=On
        'submergeDepth':2.0, # (float) mm of immersion below liquid´s surface to start dispense when using LLD
        'liquidHeight':1.0, # (float) mm above container´s bottom to start dispense when not using LLD
        'mixCycles':0, # (integer) number of mixing cycles (1 cycle = 1 asp + 1 disp)
        'mixPosition':0.0, # (float) additional immersion mm below dispense position to start mixing
        'mixVolume':0.0, # (float) mix volume
        'xDisplacement':0.0,
        'yDisplacement':0.0,
        'zDisplacement':0.0,
        'airTransportRetractDist':10.0, # (float) mm to move up in Z after finishing the dispense at a fixed height before aspirating 'transport air'
        'touchOff':0, # (integer) 0=Off , 1=On
        'dispPositionAboveTouch':0.0, # (float) mm to move up in Z after touch off detects the bottom, before dispense
        'zMoveAfterStep':0, # (integer) 0=normal, 1=Minimized (Attention!!! this depends on labware clearance height, can crash). 
        'sideTouch':0 # (integer) 0=Off , 1=On
    }),

    'mph96TipPickUp':('PICKUP96', {
        'tipSequence':'', # (string) leave empty if you are going to provide specific labware-positions below
        'labwarePositions':'', # (string) leave empty if you are going to provide a sequence name above. 'LabwareId1, positionId1; LabwareId2,positionId2; ....' Must contain 96 values
        'channelVariable':_channel_patt_96, # (string) channel Variable e.g. "11110000...." . Must contain 96 values
        'sequenceCounting':0, # (integer) 0=don´t autoincrement,  1=Autoincrement
        'reducedPatternMode':0 # (integer) 0=All (not reduced), 1=One channel, 2=One row  3=One column
    }),

    'mph96TipEject':('EJECT96', {
        'wasteSequence':'', # (string) leave empty if you are going to provide specific labware-positions below or ejecting to default waste
        'labwarePositions':'', # (string) leave empty if you are going to provide a sequence name above. 'LabwareId1, positionId1; LabwareId2,positionId2; ....'
        'channelVariable':_channel_patt_96, # (string) channel Variable e.g. "11110000...." . Must contain 96 values
        'sequenceCounting':0, # (integer)  0=don´t autoincrement,  1=Autoincrement.  Value omitted if ejecting to default waste
        'tipEjectToKnownPosition':0 # (integer) 0=Eject to specified sequence position,  1=Eject on tip pick up position, 2=Eject on default waste
    }),

    'mph96Aspirate':('ASPIRATE96', {
        'aspirateSequence':'', # (string) leave empty if you are going to provide specific labware-positions below
        'labwarePositions':'', # (string) leave empty if you are going to provide a sequence name above. LabwareId1, positionId1; LabwareId2,positionId2; ....
        'aspirateVolume':None, # (float)  single volume used for all channels in the head. There´s no individual control of each channel volume in multi-probe heads.
        'channelVariable':_channel_patt_96, # (string) channel Variable e.g. "11110000...." . Must contain 96 values
        'liquidClass':None, # (string)
        'sequenceCounting':0, # (integer)  0=don´t autoincrement,  1=Autoincrement
        'aspirateMode':0, # (integer) 0=Normal Aspiration, 1=Consecutive (don´t aspirate blowout), 2=Aspirate all 
        'capacitiveLLD':0, # (integer) 0=Off, 1=Max, 2=High, 3=Mid, 4=Low, 5=From labware definition
        'liquidFollowing':0, # (integer) 0=Off , 1=On
        'submergeDepth':2.0, # (float) mm of immersion below liquid´s surface to start aspiration when using LLD
        'liquidHeight':1.0, # (float) mm above container´s bottom to start aspiration when not using LLD
        'mixCycles':0, # (integer) number of mixing cycles (1 cycle = 1 asp + 1 disp)
        'mixPosition':0.0, # (float) additional immersion mm below aspiration position to start mixing
        'mixVolume':0.0, # (float) mix volume
        'airTransportRetractDist':10.0 # (float) mm to move up in Z after finishing the aspiration at a fixed height before aspirating 'transport air'
    }),

    'mph96Dispense':('DISPENSE96', {
        'dispenseSequence':'', # (string) leave empty if you are going to provide specific labware-positions below
        'labwarePositions':'', # (string) leave empty if you are going to provide a sequence name above. LabwareId1, positionId1; LabwareId2,positionId2; ....
        'dispenseVolume':None, # (float) single volume used for all channels in the head. There´s no individual control of each channel volume in multi-probe heads.
        'channelVariable':_channel_patt_96, # (string) channel Variable e.g. "11110000...." . Must contain 96 values
        'liquidClass':None, # (string) 
        'sequenceCounting':0, # (integer)  0=don´t autoincrement,  1=Autoincrement
        'dispenseMode':8, # (integer) 0=Jet Part, 1=Jet Empty, 2=Surface Part, 3=Surface Empty,4=Jet Drain tip, 8=From liquid class, 9=Blowout tip
        'capacitiveLLD':0, # (integer) 0=Off, 1=Max, 2=High, 3=Mid, 4=Low, 5=From labware definition
        'liquidFollowing':0, # (integer)  0=Off , 1=On
        'submergeDepth':2.0, # (float) mm of immersion below liquid´s surface to start dispense when using LLD
        'liquidHeight':1.0, # (float) mm above container´s bottom to start dispense when not using LLD
        'mixCycles':0, # (integer)  number of mixing cycles (1 cycle = 1 asp + 1 disp)
        'mixPosition':0.0, # (float)  additional immersion mm below dispense position to start mixing
        'mixVolume':0.0, # (float)  mix volume
        'airTransportRetractDist':10.0, # (float) mm to move up in Z after finishing the dispense at a fixed height before aspirating 'transport air'
        'zMoveAfterStep':0, # (integer) 0=normal, 1=Minimized (Attention!!! this depends on labware clearance height, can crash). 
        'sideTouch':0 # (integer) 0=Off , 1=On
    }),

    'iSwapGet':('ISWAP_GET', {
        'plateSequence':'', # leave empty if you are going to provide specific plate labware-position below
        'plateLabwarePositions':'', # leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1; 
        'lidSequence':'', # leave empty if you don´t use lid or if you are going to provide specific plate labware-positions below or ejecting to default waste
        'lidLabwarePositions':'', # leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1; 
        'toolSequence':'', # sequence name of the iSWAP. leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1;
        'sequenceCounting':0, # (integer) 0=don´t autoincrement plate sequence,  1=Autoincrement
        'movementType':0, # (integer) 0=To carrier, 1=Complex movement
        'transportMode':0, # (integer) 0=Plate only, 1=Lid only ,2=Plate with lid
        'gripForce':4, # (integer) 2 (minimum) ... 9 (maximum)
        'inverseGrip':0, # (integer) 0=Off, 1=On
        'collisionControl':0, # (integer) 0=Off, 1=On
        'gripMode':1, # (integer) 0=Small side, 1=Large side
        'retractDistance':0.0, # (float) retract distance [mm] (only used if 'movement type' is set to 'complex movement')
        'liftUpHeight':20.0, # (float) lift-up distance [mm] (only used if 'movement type' is set to 'complex movement')
        'gripWidth':123.7, # (float) grip width when closed [mm]
        'tolerance':2.0, # (float) tolerance [mm]
        'gripHeight':3.0, # (float) height to grip above bottom of labware [mm]
        'widthBefore':130.0, # (float) grip width when opened before grip [mm]
        'labwareOrientation':1
    }),

    'iSwapPlace':('ISWAP_PLACE', {
        'plateSequence':'', # leave empty if you are going to provide specific plate labware-position below
        'plateLabwarePositions':'', # leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1; 
        'lidSequence':'', # leave empty if you don´t use lid or if you are going to provide specific plate labware-positions below or ejecting to default waste
        'lidLabwarePositions':'', # leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1; 
        'toolSequence':'', # sequence name of the iSWAP. leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1;
        'sequenceCounting':0, # (integer) 0=don´t autoincrement plate sequence,  1=Autoincrement
        'movementType':0, # (integer) 0=To carrier, 1=Complex movement
        'transportMode':0, # (integer) 0=Plate only, 1=Lid only ,2=Plate with lid
        'collisionControl':0, # (integer) 0=Off, 1=On
        'retractDistance':0.0, # (float) retract distance [mm] (only used if 'movement type' is set to 'complex movement')
        'liftUpHeight':20.0 # (float) lift-up distance [mm] (only used if 'movement type' is set to 'complex movement')
    }),
    'iSwapMove':('ISWAP_MOVE',{
        'plateSequence':'',
        'plateLabwarePositions':'',
        'collisionControl':0,
        'gripMode':1
    }),

    'HxFanSet':('HEPA', {
        'deviceNumber':_fan_port, # (integer) COM port number of fan
        'persistant':1, # (integer) 0=don´t keep fan running after method exits, 1=keep settings after method exits
        'fanSpeed':None, # (float) set percent of maximum fan speed
        'simulate':0 #(integer) 0=normal mode, 1=use HxFan simulation mode
    }),

    'CORE96WashEmpty':('WASH96_EMPTY', {
        'refillAfterEmpty':0, # (integer) 0=Don't refill, 1=Refill both chambers, 2=Refill chamber 1 only, 3=Refill chamber 2 only
        'chamber1WashLiquid':0, # (integer) 0=Liquid 1 (red container), 1=liquid 2 (blue container)
        'chamber1LiquidChange':0, # (integer) 0=No, 1=Yes TODO: What does this mean?
        'chamber2WashLiquid':0, # (integer) 0=Liquid 1 (red container), 1=liquid 2 (blue container)
        'chamber2LiquidChange':0, # (integer) 0=No, 1=Yes TODO: What does this mean?
    }),

    'gripGet':('GRIP_GET', {
        'plateSequence':'', # leave empty if you are going to provide specific plate labware-position below
        'plateLabwarePositions':'', # leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1; 
        'lidSequence':'', # leave empty if you don´t use lid or if you are going to provide specific plate labware-positions below or ejecting to default waste
        'lidLabwarePositions':'', # leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1; 
        'toolSequence':'COREGripTool', # sequence name of the CO-RE Gripper
        'gripForce':3, # (integer) 0-9, from lowest to highest
        'gripperToolChannel':8, # specifies the higher of two consecutive integers representing the CO-RE gripper channels.
        'sequenceCounting':0, # (integer) 0=don´t autoincrement plate sequence,  1=Autoincrement
        'gripWidth':75, # (float) mm
        'gripHeight':3.0, # (float) mm
        'widthBefore':90, # (float) mm width before gripping
        'gripSpeed':5.0, # (float) mm/s. Must be supplied
        'zSpeed':50.0, # (float) mm/s. Must be supplied
        'transportMode':0, # (integer) 0=Plate only, 1=Lid only ,2=Plate with lid
        'checkPlate':0 # (integer) 
    }),

    'gripMove':('GRIP_MOVE', {
        'plateSequence':'', # leave empty if you are going to provide specific plate labware-position below
        'xAcceleration':4, # (integer) 1-5 from slowest to fastest, where 4 is default
        'plateLabwarePositions':'', # leave empty if you don´t use lid or if you are going to provide specific plate labware-positions below or ejecting to default waste

    }),

    'gripPlace':('GRIP_PLACE', {
        'plateSequence':'', # leave empty if you are going to provide specific plate labware-position below
        'plateLabwarePositions':'', # leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1; 
        'lidSequence':'', # leave empty if you don´t use lid or if you are going to provide specific plate labware-positions below or ejecting to default waste
        'lidLabwarePositions':'', # leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1; 
        'toolSequence':'COREGripTool', # sequence name of the iSWAP. leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1;
        'sequenceCounting':0, # (integer) 0=don´t autoincrement plate sequence,  1=Autoincrement
        'movementType':0, # (integer) 0=To carrier, 1=Complex movement
        'transportMode':0, # (integer) 0=Plate only, 1=Lid only ,2=Plate with lid
        'ejectToolWhenFinish':1, # (integer) 0=Off, 1=On
        'zSpeed':100.0, # (float) mm/s
        'platePressOnDistance':0.0, # (float) lift-up distance [mm] (only used if 'movement type' is set to 'complex movement'),
        'xAcceleration':4  # (integer) 1-5 from slowest to fastest, where 4 is default
    }),
    'moveSequence':('MOVE_SEQ',{

        'inputSequence':'',
        'xDisplacement':'',
        'yDisplacement':'',
        'zDisplacement':'',
    }),
    'TEC_Initialize':('TEC_INIT', {

        'ControllerID':'', # (integer)
        'SimulationMode':False, # 0=False, 1=True; 
    }),
    'TEC_StartTempControl':('TEC_START', {

        'ControllerID':'', # (integer)
        'DeviceID':'', # (integer); 
    }),

    'TEC_SetTarget':('TEC_SET_TARGET', {
        'ControllerID':'', # (integer)
        'DeviceID':'', # (integer); 
        'TargetTemperature':'', # (float); 
    }),
    'TEC_StopTemperatureControl':('TEC_STOP', {

        'ControllerID':'', # (integer)
        'DeviceID':'', # (integer); 
    }),
    'TEC_Terminate':('TEC_TERMINATE', {

        'StopAllDevices':1, # 0=False, 1=True
    }),
    'TiltModule_Initialize':('TILT_INIT', {

        'ModuleName':'', # (string)
        'Comport':'', # (integer)
        'TraceLevel':'', # (integer)
        'Simulate': '' # 0=False, 1=True
    }),
    'TiltModule_MoveToPosition':('TILT_MOVE', {

        'ModuleName':'', # (string)
        'Angle':'' # (integer)
    }),
    'FirmwareCommand':('FIRMWARECOMMAND', {

        'FirmwareCommandList':[], # list elements as {FirmwareCommand:'', FirmwareParameter:''} 
    }),
    'BarcodeReader_Initialize':('BC_INITIALIZE',{

        'ComPort':'' # (string)
    }),
    'BarcodeReader_Read':('BC_READ',{

    }),
}

"""All of the command names supported out of the box, mapped to their default params.

On module load, defaults_by_cmd is parsed into `HamiltonCmdTemplate`s, which are injected into the global package namespace under the first element of the values of this dict (strings in all caps). This is so that they can be imported directly from `pyhamilton` as module-level variables, while avoiding circular imports.

Example:

```
from pyhamilton import INITIALIZE
```


INITIALIZE

- initializeAlways (int)

    0=only initialize components not already initialized, 1=always reinitialize all robot components

    Default: 0 





PICKUP

- tipSequence (string)

    leave empty if you are going to provide specific labwarePositions below

    Default: ''

- labwarePositions (string)

    leave empty if you are going to provide a sequence name above.'LabwareId1, positionId1; LabwareId2,positionId2; ....'

    Default: ''

- channelVariable (string)

    channel pattern e.g. '11110000'

    Default: _channel_patt_16

- sequenceCounting (integer)

    0=don´t autoincrement,  1=Autoincrement

    Default: 0

- channelUse (integer)

    1=use all sequence positions (no empty wells), 2=keep channel pattern

    Default: 1



EJECT

- wasteSequence (string)

    leave empty if you are going to provide specific labware-positions below or ejecting to default waste

    Default: ''

- labwarePositions (string)

    leave empty if you are going to provide a sequence name above.'LabwareId1, positionId1; LabwareId2,positionId2; ....'

    Default: ''

- channelVariable (string)

    channel pattern e.g. "11110000"

    Default: _channel_patt_16

- sequenceCounting (integer)

    0=don´t autoincrement,  1=Autoincrement.  Value omitted if ejecting to default waste

    Default: 0

- channelUse (integer)

    1=use all sequence positions (no empty wells), 2=keep channel pattern

    Default: 1

- useDefaultWaste (integer)

    0=eject to custom waste sequence,  1=Use default waste

    Default: 0





ASPIRATE

- aspirateSequence (string)

    leave empty if you are going to provide specific labware-positions below

    Default: ''

- labwarePositions (string)

    leave empty if you are going to provide a sequence name above. 'LabwareId1, positionId1; LabwareId2,positionId2; ....'

    Default: ''

- volumes (float or string)

    enter a single value used for all channels or enter an array of values for each channel like [10.0,15.5,11.2]

    Default: None

- channelVariable (string)

    channel pattern e.g. "11110000"

    Default: _channel_patt_16

- liquidClass (string)

    Default: None

- sequenceCounting (integer)

    0=don´t autoincrement,  1=Autoincrement

    Default: 0

- channelUse (integer)

    1=use all sequence positions (no empty wells), 2=keep channel pattern

    Default: 1

- aspirateMode (integer)

    0=Normal Aspiration, 1=Consecutive (don´t aspirate blowout), 2=Aspirate all 

    Default: 0

- capacitiveLLD (integer)

    0=Off, 1=Max, 2=High, 3=Mid, 4=Low, 5=From labware definition

    Default: 0

- pressureLLD (integer)

    0=Off, 1=Max, 2=High, 3=Mid, 4=Low, 5=From liquid class definition

    Default: 0

- liquidFollowing (integer)

    0=Off , 1=On

    Default: 0

- submergeDepth (float)

    mm of immersion below liquid´s surface to start aspiration when using LLD

    Default: 2.0

- liquidHeight (float)

    mm above container´s bottom to start aspiration when not using LLD

    Default: 1.0

- maxLLdDifference (float)

    max mm height different between cLLD and pLLD detected liquid levels

    Default: 0.0

- mixCycles (integer)

    number of mixing cycles (1 cycle = 1 asp + 1 disp)

    Default: 0

- mixPosition (float)

    additional immersion mm below aspiration position to start mixing

    Default: 0.0

- mixVolume (float)

    mix volume

    Default: 0.0

- airTransportRetractDist (float)

    mm to move up in Z after finishing the aspiration at a fixed height before aspirating 'transport air'

    Default: 10.0

- touchOff (integer)

    0=Off , 1=On

    Default: 0

- aspPosAboveTouch (float)

    mm to move up in Z after touch off detects the bottom before aspirating liquid

    Default: 0.0





DISPENSE

- dispenseSequence (string)

    leave empty if you are going to provide specific labware-positions below

    Default: ''

- labwarePositions (string)

    leave empty if you are going to provide a sequence name above. 'LabwareId1, positionId1; LabwareId2,positionId2; ....'

    Default: ''

- volumes (float or string)

    enter a single value used for all channels or enter an array of values for each channel like [10.0,15.5,11.2]

    Default: None

- channelVariable (string)

    channel pattern e.g. "11110000"

    Default: _channel_patt_16

- liquidClass (string)

    Default: None

- sequenceCounting (integer)

    0=don´t autoincrement,  1=Autoincrement

    Default: 0

- channelUse (integer)

    1=use all sequence positions (no empty wells), 2=keep channel pattern

    Default: 1

- dispenseMode (integer)

    0=Jet Part, 1=Jet Empty, 2=Surface Part, 3=Surface Empty, 4=Jet Drain tip, 8=From liquid class, 9=Blowout tip

    Default: 8

- capacitiveLLD (integer)

    0=Off, 1=Max, 2=High, 3=Mid, 4=Low, 5=From labware definition

    Default: 0

- liquidFollowing (integer)

    0=Off , 1=On

    Default: 0

- submergeDepth (float)

    mm of immersion below liquid´s surface to start dispense when using LLD

    Default: 2.0



- liquidHeight (float)

    mm above container´s bottom to start dispense when not using LLD

    Default: 1.0

- mixCycles (integer)

    number of mixing cycles (1 cycle = 1 asp + 1 disp)

    Default: 0

- mixPosition (float)

    additional immersion mm below dispense position to start mixing

    Default: 0.0

- mixVolume (float)

    mix volume

    Default: 0.0

- airTransportRetractDist (float)

    mm to move up in Z after finishing the dispense at a fixed height before aspirating 'transport air'

    Default: 10.0

- touchOff (integer)

    0=Off , 1=On

    Default: 0

- dispPositionAboveTouch (float)

    mm to move up in Z after touch off detects the bottom, before dispense

    Default: 0.0

- zMoveAfterStep (integer)

    0=normal, 1=Minimized (Attention!!! this depends on labware clearance height, can crash). 

    Default: 0

- sideTouch (integer)

    0=Off , 1=On

    Default: 0





PICKUP96

- tipSequence (string)

    leave empty if you are going to provide specific labware-positions below

    Default: ''

- labwarePositions (string)

    leave empty if you are going to provide a sequence name above. 'LabwareId1, positionId1; LabwareId2,positionId2; ....' Must contain 96 values

    Default: ''

- channelVariable (string)

    channel Variable e.g. "11110000...." . Must contain 96 values

    Default: _channel_patt_96

- sequenceCounting (integer)

    0=don´t autoincrement,  1=Autoincrement

    Default: 0

- reducedPatternMode (integer)

    0=All (not reduced), 1=One channel, 2=One row  3=One column

    Default: 0





EJECT96

- wasteSequence (string)

    leave empty if you are going to provide specific labware-positions below or ejecting to default waste

    Default: ''

- labwarePositions (string)

    leave empty if you are going to provide a sequence name above. 'LabwareId1, positionId1; LabwareId2,positionId2; ....'

    Default: ''

- channelVariable (string)

    channel Variable e.g. "11110000...." . Must contain 96 values

    Default: _channel_patt_96

- sequenceCounting (integer)

    0=don´t autoincrement,  1=Autoincrement.  Value omitted if ejecting to default waste

    Default: 0

- tipEjectToKnownPosition (integer)

    0=Eject to specified sequence position,  1=Eject on tip pick up position, 2=Eject on default waste

    Default: 0





ASPIRATE96

- aspirateSequence (string)

    leave empty if you are going to provide specific labware-positions below

    Default: ''

- labwarePositions (string)

    leave empty if you are going to provide a sequence name above. LabwareId1, positionId1; LabwareId2,positionId2; ....

    Default: ''

- aspirateVolume (float)

    single volume used for all channels in the head. There´s no individual control of each channel volume in multi-probe heads.

    Default: None

- channelVariable (string)

    channel Variable e.g. "11110000...." . Must contain 96 values

    Default: _channel_patt_96

- liquidClass (string)

    Default: None

- sequenceCounting (integer)

    0=don´t autoincrement,  1=Autoincrement

    Default: 0

- aspirateMode (integer)

    0=Normal Aspiration, 1=Consecutive (don´t aspirate blowout), 2=Aspirate all 

    Default: 0

- capacitiveLLD (integer)

    0=Off, 1=Max, 2=High, 3=Mid, 4=Low, 5=From labware definition

    Default: 0

- liquidFollowing (integer)

    0=Off , 1=On

    Default: 0

- submergeDepth (float)

    mm of immersion below liquid´s surface to start aspiration when using LLD

    Default: 2.0

- liquidHeight (float)

    mm above container´s bottom to start aspiration when not using LLD

    Default: 1.0

- mixCycles (integer)

    number of mixing cycles (1 cycle = 1 asp + 1 disp)

    Default: 0

- mixPosition (float)

    additional immersion mm below aspiration position to start mixing

    Default: 0.0

- mixVolume (float)

    mix volume

    Default: 0.0

- airTransportRetractDist (float)

    mm to move up in Z after finishing the aspiration at a fixed height before aspirating 'transport air'

    Default: 10.0





DISPENSE96

- dispenseSequence (string)

    leave empty if you are going to provide specific labware-positions below

    Default: ''

- labwarePositions (string)

    leave empty if you are going to provide a sequence name above. LabwareId1, positionId1; LabwareId2,positionId2; ....

    Default: ''

- dispenseVolume (float)

    single volume used for all channels in the head. There´s no individual control of each channel volume in multi-probe heads.

    Default: None

- channelVariable (string)

    channel Variable e.g. "11110000...." . Must contain 96 values

    Default: _channel_patt_96

- liquidClass (string)

    Default: None

- sequenceCounting (integer)

    0=don´t autoincrement,  1=Autoincrement

    Default: 0

- dispenseMode (integer)

    0=Jet Part, 1=Jet Empty, 2=Surface Part, 3=Surface Empty,4=Jet Drain tip, 8=From liquid class, 9=Blowout tip

    Default: 8

- capacitiveLLD (integer)

    0=Off, 1=Max, 2=High, 3=Mid, 4=Low, 5=From labware definition

    Default: 0

- liquidFollowing (integer)

    0=Off , 1=On

    Default: 0

- submergeDepth (float)

    mm of immersion below liquid´s surface to start dispense when using LLD

    Default: 2.0

- liquidHeight (float)

    mm above container´s bottom to start dispense when not using LLD

    Default: 1.0

- mixCycles (integer)

    number of mixing cycles (1 cycle = 1 asp + 1 disp)

    Default: 0

- mixPosition (float)

    additional immersion mm below dispense position to start mixing

    Default: 0.0

- mixVolume (float)

    mix volume

    Default: 0.0

- airTransportRetractDist (float)

    mm to move up in Z after finishing the dispense at a fixed height before aspirating 'transport air'

    Default: 10.0

- zMoveAfterStep (integer)

    0=normal, 1=Minimized (Attention!!! this depends on labware clearance height, can crash). 

    Default: 0

- sideTouch (integer)

    0=Off , 1=On

    Default: 0





ISWAP_GET

plateSequence

    leave empty if you are going to provide specific plate labware-position below

    Default:''

- plateLabwarePositions (string)

    leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1; 

    Default: ''

- lidSequence (string)

    leave empty if you don´t use lid or if you are going to provide specific plate labware-positions below or ejecting to default waste

    Default: ''

- lidLabwarePositions (string)

    leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1; 

    Default: ''

- toolSequence (string)

    sequence name of the iSWAP. leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1;

    Default: ''

- sequenceCounting (integer)

    0=don´t autoincrement plate sequence,  1=Autoincrement

    Default: 0

- movementType (integer)

    0=To carrier, 1=Complex movement

    Default: 0

- transportMode (integer)

    0=Plate only, 1=Lid only ,2=Plate with lid

    Default: 0

- gripForce (integer)

    2 (minimum) ... 9 (maximum)

    Default: 4

- inverseGrip (integer)

    0=Off, 1=On

    Default: 0

- collisionControl (integer)

    0=Off, 1=On

    Default: 0

- gripMode (integer)

    0=Small side, 1=Large side

    Default: 1

- retractDistance (float)

    retract distance [mm] (only used if 'movement type' is set to 'complex movement')

    Default: 0.0

- liftUpHeight (float)

    lift-up distance [mm] (only used if 'movement type' is set to 'complex movement')

    Default: 20.0

- gripWidth (float)

    grip width when closed [mm]

    Default: 123.7

- tolerance (float)

    tolerance [mm]

    Default: 2.0

- gripHeight (float)

    height to grip above bottom of labware [mm]

    Default: 3.0

- widthBefore (float)

    grip width when opened before grip [mm]

    Default: 130.0





ISWAP_PLACE

- plateSequence (string)

    leave empty if you are going to provide specific plate labware-position below

    Default: ''

- plateLabwarePositions (string)

    leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1; 

    Default: ''

- lidSequence (string)

    leave empty if you don´t use lid or if you are going to provide specific plate labware-positions below or ejecting to default waste

    Default: ''

- lidLabwarePositions (string)

    leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1; 

    Default: ''

- toolSequence (string)

    sequence name of the iSWAP. leave empty if you are going to provide a plate sequence name above. LabwareId1, positionId1;

    Default: ''

- sequenceCounting (integer)

    0=don´t autoincrement plate sequence,  1=Autoincrement

    Default: 0

- movementType (integer)

    0=To carrier, 1=Complex movement

    Default: 0

- transportMode (integer)

    0=Plate only, 1=Lid only ,2=Plate with lid

    Default: 0

- collisionControl (integer)

    0=Off, 1=On

    Default: 0

- retractDistance (float)

    retract distance [mm] (only used if 'movement type' is set to 'complex movement')

    Default: 0.0

- liftUpHeight (float)

    lift-up distance [mm] (only used if 'movement type' is set to 'complex movement')

    Default: 20.0





HEPA

- deviceNumber (integer)

    COM port number of fan

    Default: _fan_port

- persistant (integer)

    0=don´t keep fan running after method exits, 1=keep settings after method exits

    Default: 1

- fanSpeed (float)

    set percent of maximum fan speed

    Default: None

- simulate (integer)

    0=normal mode, 1=use HxFan simulation mode

    Default: 0 





WASH96_EMPTY

- refillAfterEmpty (integer)

    0=Don't refill, 1=Refill both chambers, 2=Refill chamber 1 only, 3=Refill chamber 2 only

    Default: 0

- chamber1WashLiquid (integer)

    0=Liquid 1 (red container), 1=liquid 2 (blue container)

    Default: 0

- chamber1LiquidChange (integer)

    0=No, 1=Yes TODO: What does this mean?

    Default: 0

- chamber2WashLiquid (integer)

    0=Liquid 1 (red container), 1=liquid 2 (blue container)

    Default: 0

- chamber2LiquidChange (integer)

    0=No, 1=Yes TODO: What does this mean?

    Default: 0


"""

