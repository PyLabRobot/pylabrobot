_channel_patt_16 = '1'*8 + '0'*8
_channel_patt_96 = '1'*96

_fan_port = 6
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
        'widthBefore':130.0 # (float) grip width when opened before grip [mm]
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
    })
}
