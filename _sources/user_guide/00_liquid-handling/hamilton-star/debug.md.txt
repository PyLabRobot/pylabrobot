# Debugging STAR issues

This page explains how to debug issues with PLR and a Hamilton STAR(let).

## Finding VENUS trace files

To get the firmware instructions sent by venus when doing specific operations, locate the following file:

`C:\Program Files (x86)\HAMILTON\LogFiles\HxUsbCommYYYMMDD.trc`

This information will be useful to see how the firmware commands sent by VENUS differ from the ones we send in PLR.

```{warning}
This file may contain firmware output of entire protocols you are running, so be careful with sharing it. You may want to look at the timestamps and filter the file to only include the relevant parts, or share the file privately.
```
