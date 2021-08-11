#pragma once
global resource Res_Cyt6000_6wp(1, 0xff0000, Translate("Cyt6000_6wp"));
global resource Res_CytC24_96wp(1, 0xffff, Translate("CytC24_96wp"));
global resource Res_Cytomat24(360, 0xf0caa6, Translate("Cytomat24"));
global resource Res_Cyto6002(3, 0xff0000, Translate("Cyto6002"));
global resource Res_Cytomat6002(1, 0xff0000, Translate("Cytomat6002"));
global resource Res_CeligoHandoff_96(1, 0xff, Translate("CeligoHandoff_96"));
global resource Res_ReaderHandoff2_96(1, 0xffff, Translate("ReaderHandoff2_96"));
global resource Res_ReaderHandoff_96(1, 0xff00ff, Translate("ReaderHandoff_96"));
global resource Res_ML_STAR(1, 0xff0000, Translate("ML_STAR"));
global resource Mediatrough(5, 0xff0000, Translate("PLT_CAR_2"));


function Res_Cyt6000_6wp_map(variable unit) variable { return(unit); }
function Res_Cyt6000_6wp_rmap(variable address) variable { return(address); }

function Res_CytC24_96wp_map(variable unit) variable { return(unit); }
function Res_CytC24_96wp_rmap(variable address) variable { return(address); }

function Res_Cytomat24_map(variable unit) variable { return(unit); }
function Res_Cytomat24_rmap(variable address) variable { return(address); }

function Res_Cyto6002_map(variable unit) variable { 
     variable ret;
     if ( unit == 1 ) ret = "Media1";
     if ( unit == 2 ) ret = "Media2";
     if ( unit == 3 ) ret = "Media3";
     return(ret);
}
function Res_Cyto6002_rmap(variable address) variable {
     variable ret;
     if ( address == "Media1" ) ret = 1;
     if ( address == "Media2" ) ret = 2;
     if ( address == "Media3" ) ret = 3;
     return(ret);
}

function Res_Cytomat6002_map(variable unit) variable { return(unit); }
function Res_Cytomat6002_rmap(variable address) variable { return(address); }

function Res_CeligoHandoff_96_map(variable unit) variable { return(unit); }
function Res_CeligoHandoff_96_rmap(variable address) variable { return(address); }

function Res_ReaderHandoff2_96_map(variable unit) variable { return(unit); }
function Res_ReaderHandoff2_96_rmap(variable address) variable { return(address); }

function Res_ReaderHandoff_96_map(variable unit) variable { return(unit); }
function Res_ReaderHandoff_96_rmap(variable address) variable { return(address); }

function Res_ML_STAR_map(variable unit) variable { return(unit); }
function Res_ML_STAR_rmap(variable address) variable { return(address); }

function Mediatrough_map(variable unit) sequence { 
     device dev("","", hslTrue);
     sequence ret;
     dev = GetDeviceRef("ML_STAR");
     if ( unit == 1 ) ret = dev.Ham_DW_Rgt_L_0001;
     if ( unit == 2 ) ret = dev.Ham_DW_Rgt_L_0002;
     if ( unit == 3 ) ret = dev.Ham_DW_Rgt_L_0003;
     if ( unit == 4 ) ret = dev.Ham_DW_Rgt_L_0004;
     if ( unit == 5 ) ret = dev.Ham_DW_Rgt_L_0005;
     return(ret);
}
function Mediatrough_rmap(sequence address) variable {
     device dev("","", hslTrue);
     variable ret;
     dev = GetDeviceRef("ML_STAR");
     if ( address.EqualsToSequence(dev.Ham_DW_Rgt_L_0001) ) ret = 1;
     if ( address.EqualsToSequence(dev.Ham_DW_Rgt_L_0002) ) ret = 2;
     if ( address.EqualsToSequence(dev.Ham_DW_Rgt_L_0003) ) ret = 3;
     if ( address.EqualsToSequence(dev.Ham_DW_Rgt_L_0004) ) ret = 4;
     if ( address.EqualsToSequence(dev.Ham_DW_Rgt_L_0005) ) ret = 5;
     return(ret);
}


namespace ResourceUnit {
     variable Res_Cyt6000_6wp;
     variable Res_CytC24_96wp;
     variable Res_Cytomat24;
     variable Res_Cyto6002;
     variable Res_Cytomat6002;
     variable Res_CeligoHandoff_96;
     variable Res_ReaderHandoff2_96;
     variable Res_ReaderHandoff_96;
     variable Res_ML_STAR;
     sequence Mediatrough;
}
// $$author=BenG$$valid=0$$time=2021-03-18 13:12$$checksum=9dfb7551$$length=081$$