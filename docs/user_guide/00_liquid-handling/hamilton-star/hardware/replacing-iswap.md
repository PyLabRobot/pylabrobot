# Replacing iSWAP arm on Hamilton STAR

This guide provides instructions for replacing the iSWAP arm on a Hamilton STAR liquid handling system.

Caution/Disclaimer: 
1. This procedure is a work in progress and the user assumes all responsibility for any damage that may be done as a result.
2. This procedure should not be performed on any system that are either under the OEM Warranty period or currently on a service contract with the OEM or any third party service organization. This will certainly void the OEM Warranty/Service contract and likely invalidate any service agreement with third parties.
3. This procedure DOES NOT encapselate the entire OEM adjustment procedure which includes specialized tooling and software to perform.
4. Once completing this procedure it is necesary to check, and if needed, reteach the locations that are to be accsesed by the Iswap.

Note: Due to the way the Iswap is taught, the calibration positions from machine to machine are typically very close. This means an Iswap can be SWAPPED (pun intended) and function largely the same with minor tweaks.

## Tools

- 2mm hex key
- 4mm hex key

## Removing the iSWAP Arm

1. Ensure that the Hamilton STAR system is powered off.

2. Undo the following two FFC cables:

![](./img/ffc.jpg)

3. Slightly loosen the two screws on the side using a 2mm hex key, enough to slide off the metal piece. It is easiest to keep the screws in place.

![](./img/side-screws.jpg)

4. Undo the two main screws on the back of the iSWAP arm using a 4mm hex key. Remove them. Start with the adjustment screw (1), then the main screw (2). Be careful, the arm might fall off if you don't hold it.

![](./img/main-screws.jpg)

5. The iSWAP arm can now be removed from the Hamilton STAR system. After removing the arm, you are left with this:

![](./img/after-remove.jpg)

## Attaching the New iSWAP Arm and rough leveling

6. Place the replacement arm on the system, insert and tighten the two fixing screws, reattach the comunication and Y-drive ribon cables.

Note: Once physically installed on the system it is recomended that you level the arm in relation to the deck.

7. Loosen the two fixing screws until the Iswap is fixed in position but can still be rotated about the X Axis (left to right) of the machine.
8. Manually position the X-Arm in the center of the machine and the Iswap in the middle of the X-Arm.
9. Remove any labware below the Iswap.
10. Power on the machine and verify there are no errors.
11. !!While supporting the ISwap in the Z axis!! send the firmware command "R0BA" to release the Z axis brake on the Iswap.
12. Lower the Iswap until the gripper fingers are just above the deck surface (2-5mm)
13. Send the firmware command "R0BO" (first is a the number zero (0), second is the capital letter (O)) to reingage the Z axis brake
14. Orient the Iswap with the main arm to the right with the gripper facing you and rotate the ISwap about the X axis until the gripper fingers are equidistant from the deck.
(Picture Coming Soon!)
15. Rotate the main Iswap are 180 degrees to the left and rotate the gripper hand around until it is facing. Repeat the X axis rotation the gripper fingers are equidistant from the deck.
(Picture Coming Soon!)
16. Repeat steps 14 and 15 until both sides are relatively the same.
17. Make sure there is no chance of Z axis colision and send the command "R0ZI" to initialize the Z axis of the Iswap.
18. Check/Reteach all Iswap locations in your method.

After attaching everything (in reverse order), use the alignment screw to make sure the iSWAP arm is level with the deck.
