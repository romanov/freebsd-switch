/* SPDX-License-Identifier: GPL-2.0-only
 * Erista USB-C host bring-up for FreeBSD: OTG VBUS, then the legacy USB1
 * (ChipIdea EHCI) controller and UTMI PHY in host mode.
 *
 * The PHY sequence is ported from the pinned Hekate bdk/usb/usbd.c
 * (_usb_init_phy, _usbd_reset_usb_otg_phy_device_mode) and bdk/soc/clock.c
 * (clock_enable_pllu, clock_enable_utmipll), switched to host mode as in
 * U-Boot's ehci-tegra.c. VBUS follows the L4T bm92txx.c and bq2419x drivers.
 * GPIO CC4 is the Joy-Con/fan 5 V rail on this console and is left alone.
 */
#ifndef SWITCHBSD_HOST_TEST
#include <Uefi.h>
#include <Library/IoLib.h>
#include <Library/TimerLib.h>
#include "SwitchBsdUsbHost.h"
#endif

#define CAR_BASE              0x60006000u
#define CAR_MISC_CLK_ENB      (CAR_BASE + 0x048)
#define CAR_PLLU_BASE         (CAR_BASE + 0x0C0)
#define CAR_PLLU_MISC         (CAR_BASE + 0x0CC)
#define CAR_CLK_ENB_Y_SET     (CAR_BASE + 0x29C)
#define CAR_CLK_ENB_Y_CLR     (CAR_BASE + 0x2A0)
#define CAR_RST_DEV_L_SET     (CAR_BASE + 0x300)
#define CAR_RST_DEV_L_CLR     (CAR_BASE + 0x304)
#define CAR_CLK_ENB_L_SET     (CAR_BASE + 0x320)
#define CAR_CLK_ENB_H_SET     (CAR_BASE + 0x328)
#define CAR_RST_DEV_W_CLR     (CAR_BASE + 0x43C)
#define CAR_UTMIP_PLL_CFG0    (CAR_BASE + 0x480)
#define CAR_UTMIP_PLL_CFG1    (CAR_BASE + 0x484)
#define CAR_UTMIP_PLL_CFG2    (CAR_BASE + 0x488)
#define CAR_UTMIPLL_PWRDN     (CAR_BASE + 0x52C)
#define CAR_USB2_HSIC_TRK     (CAR_BASE + 0x6CC)
#define  CAR_L_USBD           (1u << 22)
#define  CAR_H_FUSE           (1u << 7)
#define  CAR_W_XUSB_PADCTL    (1u << 14)
#define  CAR_Y_USB2_TRK       (1u << 18)
#define  PLL_BASE_LOCK        (1u << 27)
#define  PLL_BASE_ENABLE      (1u << 30)
#define  UTMIPLL_LOCK         (1u << 31)

#define PMC_USB_AO            0x7000E4F0u
#define FUSE_USB_CALIB        0x7000F9F0u
#define FUSE_USB_CALIB_EXT    0x7000FB50u
#define PADCTL_USB2_PAD_MUX   0x7009F004u
#define  PAD_MUX_PORT0_MASK   (3u << 0)
#define  PAD_MUX_BIAS_MASK    (3u << 18)

#define USB_BASE              0x7D000000u
#define USB_USBCMD            (USB_BASE + 0x130)
#define  USBCMD_RUN           (1u << 0)
#define  USBCMD_RESET         (1u << 1)
#define USB_USBSTS            (USB_BASE + 0x134)
#define USB_USBINTR           (USB_BASE + 0x138)
#define USB_TXFILLTUNING      (USB_BASE + 0x154)
#define USB_ICUSB_CTRL        (USB_BASE + 0x15C)
#define USB_PORTSC1           (USB_BASE + 0x174)
#define  PORTSC_W1C           ((1u << 1) | (1u << 3) | (1u << 5))
#define  PORTSC_POWER         (1u << 12)
#define USB_HOSTPC1_DEVLC     (USB_BASE + 0x1B4)
#define  HOSTPC_PTS_STS       (0xFu << 28)
#define  HOSTPC_PHCD          (1u << 22)
#define USB_USBMODE           (USB_BASE + 0x1F8)
#define  USBMODE_CM_MASK      3u
#define  USBMODE_CM_HOST      3u
#define USB_SUSP_CTRL         (USB_BASE + 0x400)
#define  SUSP_PHY_CLK_VALID   (1u << 7)
#define  SUSP_UTMIP_RESET     (1u << 11)
#define  SUSP_UTMIP_PHY_ENB   (1u << 12)
#define USB_VBUS_SENSORS      (USB_BASE + 0x404)
#define  VBUS_B_SESS_VLD_SW   ((1u << 12) | (1u << 11))
#define UTMIP_XCVR_CFG0       (USB_BASE + 0x808)
#define UTMIP_BIAS_CFG0       (USB_BASE + 0x80C)
#define UTMIP_HSRX_CFG0       (USB_BASE + 0x810)
#define UTMIP_HSRX_CFG1       (USB_BASE + 0x814)
#define UTMIP_TX_CFG0         (USB_BASE + 0x820)
#define UTMIP_MISC_CFG1       (USB_BASE + 0x828)
#define UTMIP_DEBOUNCE_CFG0   (USB_BASE + 0x82C)
#define UTMIP_BAT_CHRG_CFG0   (USB_BASE + 0x830)
#define UTMIP_SPARE_CFG0      (USB_BASE + 0x834)
#define UTMIP_XCVR_CFG1       (USB_BASE + 0x838)
#define UTMIP_BIAS_CFG1       (USB_BASE + 0x83C)
#define UTMIP_BIAS_CFG2       (USB_BASE + 0x850)
#define UTMIP_XCVR_CFG2       (USB_BASE + 0x854)
#define UTMIP_XCVR_CFG3       (USB_BASE + 0x858)

#define BM92T36_ADDRESS       0x18
#define  BM92T_ALERT          0x02
#define  BM92T_STATUS1        0x03
#define  BM92T_STATUS2        0x04
#define  BM92T_CONFIG1        0x06
#define  BM92T_VENDOR_CONFIG  0x1A
#define  BM92T_MAN_ID         0x4D
#define  BM92T_DEV_ID         0x4E
#define  BM92T_ROHM           0x04B5
#define  BM92T_DEVICE         0x03B0
#define  BM92T_SRC_MODE       (1u << 12)
#define  BM92T_OTG_INSERT     (1u << 13)
#define  BM92T_SPDSRC_MASK    (3u << 14)
#define  BM92T_OCP_DISABLE    (1u << 2)

#define BQ24193_ADDRESS       0x6B
#define  BQ24193_POWER_ON     0x01
#define  BQ24193_TIMER        0x05
#define  BQ24193_STATUS       0x08
#define  BQ24193_FAULT        0x09
#define  BQ24193_PART         0x0A
#define  BQ24193_PART_MASK    0x38
#define  BQ24193_PART_ID      0x28
#define  BQ24193_WATCHDOG     0x30
#define  BQ24193_OTG_CONFIG   0x21
#define  BQ24193_POWER_KEEP   0x0E
#define  BQ24193_BOOST_FAULT  0x40

#define USB_POLL_LIMIT        100000u

STATIC UINT32
SwitchBsdUsbRead(UINTN Address)
{
    return MmioRead32(Address);
}

STATIC VOID
SwitchBsdUsbWrite(UINTN Address, UINT32 Value)
{
    MmioWrite32(Address, Value);
}

STATIC VOID
SwitchBsdUsbUpdate(UINTN Address, UINT32 Clear, UINT32 Set)
{
    SwitchBsdUsbWrite(Address, (SwitchBsdUsbRead(Address) & ~Clear) | Set);
}

STATIC BOOLEAN
SwitchBsdUsbPoll(UINTN Address, UINT32 Mask, UINT32 Expected, UINT32 Limit)
{
    UINT32 Index;

    for (Index = 0; Index < Limit; Index++) {
        if ((SwitchBsdUsbRead(Address) & Mask) == Expected)
            return TRUE;
        MicroSecondDelay(1);
    }
    return (SwitchBsdUsbRead(Address) & Mask) == Expected;
}

STATIC BOOLEAN
SwitchBsdPdRead(UINT32 Register, UINT16 *Value)
{
    UINT8 Buffer[2];

    if (SwitchBsdI2cRead(BM92T36_ADDRESS, Register, Buffer, sizeof(Buffer)) != 0)
        return FALSE;
    *Value = (UINT16)(Buffer[0] | (Buffer[1] << 8));
    return TRUE;
}

STATIC BOOLEAN
SwitchBsdPdWrite(UINT32 Register, UINT16 Value)
{
    UINT8 Buffer[2] = { (UINT8)Value, (UINT8)(Value >> 8) };

    return SwitchBsdI2cWrite(BM92T36_ADDRESS, Register, Buffer, sizeof(Buffer)) == 0;
}

STATIC BOOLEAN
SwitchBsdChargerRead(UINT32 Register, UINT8 *Value)
{
    return SwitchBsdI2cRead(BQ24193_ADDRESS, Register, Value, 1) == 0;
}

STATIC BOOLEAN
SwitchBsdChargerWrite(UINT32 Register, UINT8 Value)
{
    return SwitchBsdI2cWrite(BQ24193_ADDRESS, Register, &Value, 1) == 0;
}

/*
 * Source 5 V on the USB-C port only for a recognized OTG sink, and only when
 * no charger or host is feeding VBUS. This never prevents the PHY bring-up.
 */
STATIC UINT32
SwitchBsdUsbEnableVbus(SWITCHBSD_USB_STATUS *Diag)
{
    UINT16 Value;
    UINT8 Part;
    UINT8 Timer;

    if (SwitchBsdI2cSetup() != 0)
        return SwitchUsbVbusI2cFailed;
    if (!SwitchBsdPdRead(BM92T_MAN_ID, &Diag->PdManufacturer) ||
        !SwitchBsdPdRead(BM92T_DEV_ID, &Diag->PdDevice))
        return SwitchUsbVbusI2cFailed;
    if (Diag->PdManufacturer != BM92T_ROHM || Diag->PdDevice != BM92T_DEVICE)
        return SwitchUsbVbusPdUnknown;

    // L4T bm92t_extcon_cable_set_init_state(): over-current protection on,
    // both source power switches enabled for OTG.
    if (!SwitchBsdPdRead(BM92T_VENDOR_CONFIG, &Value))
        return SwitchUsbVbusI2cFailed;
    if ((Value & BM92T_OCP_DISABLE) &&
        !SwitchBsdPdWrite(BM92T_VENDOR_CONFIG, (UINT16)(Value & ~BM92T_OCP_DISABLE)))
        return SwitchUsbVbusWriteFailed;
    if (!SwitchBsdPdRead(BM92T_CONFIG1, &Value))
        return SwitchUsbVbusI2cFailed;
    if ((Value & BM92T_SPDSRC_MASK) &&
        !SwitchBsdPdWrite(BM92T_CONFIG1, (UINT16)(Value & ~BM92T_SPDSRC_MASK)))
        return SwitchUsbVbusWriteFailed;
    MicroSecondDelay(100000);

    // Reading ALERT clears it.
    if (!SwitchBsdPdRead(BM92T_ALERT, &Diag->PdAlert) ||
        !SwitchBsdPdRead(BM92T_STATUS1, &Diag->PdStatus1) ||
        !SwitchBsdPdRead(BM92T_STATUS2, &Diag->PdStatus2))
        return SwitchUsbVbusI2cFailed;
    if (!(Diag->PdStatus1 & BM92T_SRC_MODE) || !(Diag->PdStatus2 & BM92T_OTG_INSERT))
        return SwitchUsbVbusNoOtgDevice;

    if (!SwitchBsdChargerRead(BQ24193_PART, &Part))
        return SwitchUsbVbusChargerUnknown;
    Diag->ChargerPart = Part;
    if ((Part & BQ24193_PART_MASK) != BQ24193_PART_ID)
        return SwitchUsbVbusChargerUnknown;
    if (!SwitchBsdChargerRead(BQ24193_STATUS, &Diag->ChargerStatus))
        return SwitchUsbVbusI2cFailed;
    // VBUS_STAT 1 (USB host) or 2 (adapter): something else powers the port.
    if (((Diag->ChargerStatus >> 6) & 3) == 1 || ((Diag->ChargerStatus >> 6) & 3) == 2)
        return SwitchUsbVbusInputPresent;

    // The I2C watchdog would drop OTG mode after 40 s; FreeBSD cannot kick it.
    if (!SwitchBsdChargerRead(BQ24193_TIMER, &Timer))
        return SwitchUsbVbusI2cFailed;
    if ((Timer & BQ24193_WATCHDOG) &&
        !SwitchBsdChargerWrite(BQ24193_TIMER, (UINT8)(Timer & ~BQ24193_WATCHDOG)))
        return SwitchUsbVbusWriteFailed;
    // CHG_CONFIG = OTG with the 1.3 A boost limit; never set reset or kick bits.
    if (!SwitchBsdChargerRead(BQ24193_POWER_ON, &Diag->ChargerPowerOn))
        return SwitchUsbVbusI2cFailed;
    if (!SwitchBsdChargerWrite(BQ24193_POWER_ON,
                               (UINT8)((Diag->ChargerPowerOn & BQ24193_POWER_KEEP) | BQ24193_OTG_CONFIG)))
        return SwitchUsbVbusWriteFailed;

    // Boost starts about 220 ms after enable.
    MicroSecondDelay(250000);
    if (!SwitchBsdChargerRead(BQ24193_POWER_ON, &Diag->ChargerPowerOn) ||
        !SwitchBsdChargerRead(BQ24193_STATUS, &Diag->ChargerStatus) ||
        !SwitchBsdChargerRead(BQ24193_FAULT, &Diag->ChargerFault))
        return SwitchUsbVbusI2cFailed;
    return (Diag->ChargerFault & BQ24193_BOOST_FAULT) ? SwitchUsbVbusFault : SwitchUsbVbusOn;
}

STATIC VOID
SwitchBsdUsbStartPllu(SWITCHBSD_USB_STATUS *Diag)
{
    UINT32 Config;

    // 38.4 MHz / 2 * 25 = 480 MHz, software override, then the USB outputs.
    SwitchBsdUsbUpdate(CAR_PLLU_MISC, 0, 1u << 29);
    Config = (SwitchBsdUsbRead(CAR_PLLU_BASE) & 0xFFE00000u) | (1u << 24) | (1u << 16) | (0x19u << 8) | 2;
    SwitchBsdUsbWrite(CAR_PLLU_BASE, Config);
    SwitchBsdUsbWrite(CAR_PLLU_BASE, Config | PLL_BASE_ENABLE);
    if (!SwitchBsdUsbPoll(CAR_PLLU_BASE, PLL_BASE_LOCK, PLL_BASE_LOCK, 1000))
        Diag->Flags |= SWITCHBSD_USB_FLAG_PLLU_UNLOCKED;
    MicroSecondDelay(10);
    SwitchBsdUsbUpdate(CAR_PLLU_BASE, 0, 0x2E00000u);
}

STATIC VOID
SwitchBsdUsbStartUtmiPll(SWITCHBSD_USB_STATUS *Diag)
{
    // 38.4 MHz * 25 = 960 MHz with the matching delay and crystal counts.
    SwitchBsdUsbUpdate(CAR_UTMIP_PLL_CFG0, 0x00FFFF00u, (25u << 16) | (1u << 8));
    SwitchBsdUsbUpdate(CAR_UTMIP_PLL_CFG2, 0x00FFFFC0u, 24u << 18);
    SwitchBsdUsbUpdate(CAR_UTMIP_PLL_CFG1, 0xF8005FFFu, (1u << 15) | 375);
    if (!SwitchBsdUsbPoll(CAR_UTMIPLL_PWRDN, UTMIPLL_LOCK, UTMIPLL_LOCK, 1000))
        Diag->Flags |= SWITCHBSD_USB_FLAG_UTMIPLL_UNLOCKED;
}

STATIC VOID
SwitchBsdUsbCalibratePhy(VOID)
{
    UINT32 Calib;

    // Fuse clock and register visibility, as Hekate leaves them.
    SwitchBsdUsbWrite(CAR_CLK_ENB_H_SET, CAR_H_FUSE);
    SwitchBsdUsbUpdate(CAR_MISC_CLK_ENB, 0, 1u << 28);
    Calib = SwitchBsdUsbRead(FUSE_USB_CALIB);
    SwitchBsdUsbUpdate(UTMIP_XCVR_CFG0, 0x01C0000Fu,
                       (Calib & 0xF) | ((((Calib & 0x3F) << 25) >> 29) << 22));
    SwitchBsdUsbUpdate(UTMIP_XCVR_CFG1, 0x003C0000u, ((Calib << 21) >> 28) << 18);
    SwitchBsdUsbUpdate(UTMIP_XCVR_CFG3, 0x00003E00u, (SwitchBsdUsbRead(FUSE_USB_CALIB_EXT) & 0x1F) << 9);
    SwitchBsdUsbUpdate(UTMIP_XCVR_CFG0, 1u << 21, 0);
    SwitchBsdUsbUpdate(UTMIP_XCVR_CFG2, 0x00000E00u, 0x400);
    MicroSecondDelay(1);

    SwitchBsdUsbUpdate(UTMIP_DEBOUNCE_CFG0, 0x0000FFFFu, 0xBB80);
    SwitchBsdUsbUpdate(UTMIP_BIAS_CFG1, 0x00003F00u, 0x100);
    SwitchBsdUsbUpdate(UTMIP_BIAS_CFG2, 0, 2);
    SwitchBsdUsbUpdate(UTMIP_SPARE_CFG0, 0x00000198u, 0);
    SwitchBsdUsbUpdate(UTMIP_TX_CFG0, 0, 0x80000);
    SwitchBsdUsbUpdate(UTMIP_HSRX_CFG0, 0x0F0FFC00u, 0x88000 | 0x4000);
    SwitchBsdUsbUpdate(UTMIP_BIAS_CFG2, 7, 0);
    SwitchBsdUsbUpdate(UTMIP_HSRX_CFG1, 0x3E, 0x12);
    SwitchBsdUsbUpdate(UTMIP_MISC_CFG1, 0, 1u << 30);
    SwitchBsdUsbUpdate(CAR_UTMIP_PLL_CFG2, 0, 1u << 30);

    // Two bias-pad tracking cycles on the USB2 tracking clock.
    SwitchBsdUsbWrite(CAR_CLK_ENB_Y_SET, CAR_Y_USB2_TRK);
    SwitchBsdUsbUpdate(CAR_USB2_HSIC_TRK, 0xFF, 6);
    SwitchBsdUsbUpdate(UTMIP_BIAS_CFG1, 0x003FC0F8u, 0x78000 | 0x50);
    SwitchBsdUsbUpdate(UTMIP_BIAS_CFG0, 1u << 10, 0);
    MicroSecondDelay(1);
    SwitchBsdUsbUpdate(UTMIP_BIAS_CFG1, 1, 2);
    MicroSecondDelay(100);
    SwitchBsdUsbUpdate(UTMIP_BIAS_CFG1, 1u << 23, 1);
    MicroSecondDelay(3);
    SwitchBsdUsbUpdate(UTMIP_BIAS_CFG1, 1, 0);
    MicroSecondDelay(100);
    SwitchBsdUsbUpdate(UTMIP_BIAS_CFG1, 1u << 23, 1);
    SwitchBsdUsbWrite(CAR_CLK_ENB_Y_CLR, CAR_Y_USB2_TRK);
    SwitchBsdUsbUpdate(CAR_UTMIP_PLL_CFG2, 0x01000015u, 0x2000000 | 0x28 | 2);
    MicroSecondDelay(1);
    SwitchBsdUsbUpdate(UTMIP_BIAS_CFG0, 0x00C00800u, 0);
    MicroSecondDelay(1);

    // Wake detectors and transceiver power downs. Host mode also needs the
    // disconnect detector, and the charger detector stays powered down.
    SwitchBsdUsbUpdate(PMC_USB_AO, 0xC, 0);
    SwitchBsdUsbUpdate(UTMIP_XCVR_CFG0, (1u << 14) | (1u << 16) | (1u << 18), 0);
    SwitchBsdUsbUpdate(UTMIP_XCVR_CFG1, (1u << 0) | (1u << 2) | (1u << 4), 0);
    SwitchBsdUsbUpdate(UTMIP_BAT_CHRG_CFG0, 0, 1);
    MicroSecondDelay(1);
}

VOID
SwitchBsdUsbHostClearMarker(VOID)
{
    SwitchBsdUsbWrite(SWITCHBSD_USB_STATUS_ADDRESS, 0);
}

EFI_STATUS
SwitchBsdUsbHostInit(SWITCHBSD_USB_STATUS *Diag)
{
    UINT32 Suspend;
    UINT32 Port;

    SwitchBsdUsbHostClearMarker();
    Diag->Step = SwitchUsbStepNone;
    Diag->Status = EFI_NOT_READY;
    Diag->Flags = 0;
    Diag->Vbus = SwitchBsdUsbEnableVbus(Diag);

    // Hold the controller in reset while its clocks change under it.
    Diag->Step = SwitchUsbStepPllu;
    SwitchBsdUsbWrite(CAR_RST_DEV_L_SET, CAR_L_USBD);
    SwitchBsdUsbStartPllu(Diag);

    Diag->Step = SwitchUsbStepClock;
    SwitchBsdUsbWrite(CAR_CLK_ENB_L_SET, CAR_L_USBD);
    MicroSecondDelay(2);
    SwitchBsdUsbWrite(CAR_RST_DEV_L_SET, CAR_L_USBD);
    MicroSecondDelay(2);
    SwitchBsdUsbWrite(CAR_RST_DEV_L_CLR, CAR_L_USBD);
    MicroSecondDelay(2);
    SwitchBsdUsbWrite(CAR_RST_DEV_W_CLR, CAR_W_XUSB_PADCTL);
    MicroSecondDelay(2);

    // Pad 0 and the bias pad belong to the legacy (SNPS) controller.
    Diag->Step = SwitchUsbStepPadMux;
    Diag->PadMuxBefore = SwitchBsdUsbRead(PADCTL_USB2_PAD_MUX);
    if (Diag->PadMuxBefore & (PAD_MUX_PORT0_MASK | PAD_MUX_BIAS_MASK))
        SwitchBsdUsbWrite(PADCTL_USB2_PAD_MUX,
                          Diag->PadMuxBefore & ~(PAD_MUX_PORT0_MASK | PAD_MUX_BIAS_MASK));
    Diag->PadMuxAfter = SwitchBsdUsbRead(PADCTL_USB2_PAD_MUX);

    Diag->Step = SwitchUsbStepUtmiPll;
    Suspend = SwitchBsdUsbRead(USB_SUSP_CTRL);
    SwitchBsdUsbWrite(USB_SUSP_CTRL, Suspend | SUSP_UTMIP_RESET);
    SwitchBsdUsbWrite(USB_SUSP_CTRL, Suspend | SUSP_UTMIP_PHY_ENB | SUSP_UTMIP_RESET);
    // Software IDDQ control with IDDQ off; Coreboot never clears it.
    SwitchBsdUsbUpdate(CAR_UTMIPLL_PWRDN, 3, 1);
    MicroSecondDelay(10);
    SwitchBsdUsbUpdate(UTMIP_MISC_CFG1, 1u << 30, 0);
    SwitchBsdUsbUpdate(CAR_UTMIP_PLL_CFG2, 1u << 30, 0);
    // Host mode: no forced B-session (device-mode) override.
    SwitchBsdUsbUpdate(USB_VBUS_SENSORS, VBUS_B_SESS_VLD_SW, 0);
    SwitchBsdUsbStartUtmiPll(Diag);

    Diag->Step = SwitchUsbStepCalibration;
    SwitchBsdUsbCalibratePhy();

    Diag->Step = SwitchUsbStepPhyClock;
    SwitchBsdUsbUpdate(USB_SUSP_CTRL, SUSP_UTMIP_RESET, 0);
    if (!SwitchBsdUsbPoll(USB_SUSP_CTRL, SUSP_PHY_CLK_VALID, SUSP_PHY_CLK_VALID, USB_POLL_LIMIT))
        goto timeout;

    Diag->Step = SwitchUsbStepReset;
    SwitchBsdUsbUpdate(USB_USBCMD, USBCMD_RUN, 0);
    SwitchBsdUsbUpdate(USB_USBMODE, USBMODE_CM_MASK, 0);
    SwitchBsdUsbUpdate(USB_USBCMD, 0, USBCMD_RESET);
    if (!SwitchBsdUsbPoll(USB_USBCMD, USBCMD_RESET, 0, USB_POLL_LIMIT))
        goto timeout;

    // A controller reset can drop the PHY clock; Hekate waits again.
    Diag->Step = SwitchUsbStepPhyClockAfterReset;
    if (!SwitchBsdUsbPoll(USB_SUSP_CTRL, SUSP_PHY_CLK_VALID, SUSP_PHY_CLK_VALID, USB_POLL_LIMIT))
        goto timeout;

    Diag->Step = SwitchUsbStepHostMode;
    SwitchBsdUsbUpdate(USB_USBMODE, USBMODE_CM_MASK, USBMODE_CM_HOST);
    if (!SwitchBsdUsbPoll(USB_USBMODE, USBMODE_CM_MASK, USBMODE_CM_HOST, USB_POLL_LIMIT))
        goto timeout;
    // UTMI parallel interface, PHY clock running; Tegra Tx FIFO threshold.
    SwitchBsdUsbUpdate(USB_HOSTPC1_DEVLC, HOSTPC_PTS_STS | HOSTPC_PHCD, 0);
    SwitchBsdUsbWrite(USB_TXFILLTUNING, 0x10u << 16);
    SwitchBsdUsbUpdate(USB_ICUSB_CTRL, 1u << 3, 0);
    SwitchBsdUsbWrite(USB_USBINTR, 0);
    SwitchBsdUsbWrite(USB_USBSTS, SwitchBsdUsbRead(USB_USBSTS));

    // Port power; leave the controller halted for FreeBSD.
    Diag->Step = SwitchUsbStepPort;
    Port = SwitchBsdUsbRead(USB_PORTSC1);
    SwitchBsdUsbWrite(USB_PORTSC1, (Port & ~PORTSC_W1C) | PORTSC_POWER);
    MicroSecondDelay(100000);

    Diag->Step = SwitchUsbStepReady;
    Diag->Status = EFI_SUCCESS;
    SwitchBsdUsbWrite(SWITCHBSD_USB_STATUS_ADDRESS, SWITCHBSD_USB_READY_MAGIC);
    goto snapshot;

timeout:
    Diag->Status = EFI_TIMEOUT;
snapshot:
    Diag->SuspendControl = SwitchBsdUsbRead(USB_SUSP_CTRL);
    Diag->UsbMode = SwitchBsdUsbRead(USB_USBMODE);
    Diag->PortStatus = SwitchBsdUsbRead(USB_PORTSC1);
    Diag->HostPortControl = SwitchBsdUsbRead(USB_HOSTPC1_DEVLC);
    return Diag->Status;
}
