        // USB1 (ChipIdea EHCI) on the USB-C port, set up by SwitchBsdUsbHost
        // in the boot manager. Hidden unless the firmware wrote its marker.
        // Project ID without a PNP0D20 _CID: generic EHCI drivers ignore it.
        Device (USB0)
        {
            Name (_HID, "SWBS0001")
            Name (_UID, Zero)
            Name (_CCA, Zero)
            OperationRegion (SWUS, SystemMemory, 0x4003E020, 0x04)
            Field (SWUS, DWordAcc, NoLock, Preserve)
            {
                MAGC, 32
            }

            Method (_STA, 0, NotSerialized)
            {
                If (MAGC == 0x55534230)
                {
                    Return (0x0F)
                }

                Return (Zero)
            }

            Name (_CRS, ResourceTemplate ()
            {
                Memory32Fixed (ReadWrite, 0x7D000100, 0x00000100)
                Interrupt (ResourceConsumer, Level, ActiveHigh, Exclusive) { 0x34 }
            })
        }

