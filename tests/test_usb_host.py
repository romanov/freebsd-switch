"""Compile the USB host firmware templates against MMIO and I2C device models."""
from pathlib import Path
import unittest

import test_sd_firmware

ROOT = Path(__file__).resolve().parents[1]

TYPES = r'''
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#define SWITCHBSD_HOST_TEST 1
#define STATIC static
#define VOID void
#define CONST const
#define TRUE 1
#define FALSE 0
typedef uint8_t UINT8;
typedef uint16_t UINT16;
typedef uint32_t UINT32;
typedef uintptr_t UINTN;
typedef intptr_t INTN;
typedef unsigned char BOOLEAN;
typedef UINTN EFI_STATUS;
#define EFI_SUCCESS 0
#define EFI_NOT_READY 6
#define EFI_TIMEOUT 18
'''

# Sparse register file with a write log; the template's own macros are
# defined later, so the models use raw addresses.
MMIO = r'''
#define MAX_REGS 256
#define MAX_LOG 8192
static UINTN reg_addr[MAX_REGS];
static UINT32 reg_val[MAX_REGS];
static int reg_count;
static UINTN log_addr[MAX_LOG];
static UINT32 log_val[MAX_LOG];
static int log_count;
static unsigned long delay_total;

static inline UINT32 *slot(UINTN address) {
    for (int i = 0; i < reg_count; i++)
        if (reg_addr[i] == address) return &reg_val[i];
    assert(reg_count < MAX_REGS);
    reg_addr[reg_count] = address;
    reg_val[reg_count] = 0;
    return &reg_val[reg_count++];
}
static inline UINT32 peek(UINTN address) { return *slot(address); }
static inline void poke(UINTN address, UINT32 value) { *slot(address) = value; }
static inline void mmio_reset(void) { reg_count = log_count = 0; delay_total = 0; }
static inline int first_write(UINTN address, UINT32 mask, UINT32 value) {
    for (int i = 0; i < log_count; i++)
        if (log_addr[i] == address && (log_val[i] & mask) == value) return i;
    return -1;
}
static UINTN MicroSecondDelay(UINTN us) { delay_total += us; return us; }
'''


class UsbHostTests(unittest.TestCase):
    def compile_and_run(self, name, prelude, tests):
        test_sd_firmware.SdFirmwareTests.compile_and_run(self, name, prelude, tests)

    def test_usb_host_bring_up_vbus_and_failures(self):
        prelude = TYPES + (ROOT / "config/switchbsd-usb-host.h").read_text() + MMIO + r'''
static int last_write(UINTN address) {
    for (int i = log_count - 1; i >= 0; i--)
        if (log_addr[i] == address) return i;
    return -1;
}
static int phy_mode;          /* 0 valid, 1 never valid, 2 lost after reset */
static int reset_sticks, reset_done, pllu_locks;
static UINT32 MmioRead32(UINTN address) {
    UINT32 value = peek(address);
    if (address == 0x7D000400) {
        value &= ~(1u << 7);
        if (!(value & (1u << 11)) && (phy_mode == 0 || (phy_mode == 2 && !reset_done)))
            value |= 1u << 7;
    }
    if (address == 0x600060C0 && (value & (1u << 30)) && pllu_locks) value |= 1u << 27;
    if (address == 0x6000652C) value |= 1u << 31;
    return value;
}
static UINT32 MmioWrite32(UINTN address, UINT32 value) {
    assert(log_count < MAX_LOG);
    log_addr[log_count] = address;
    log_val[log_count++] = value;
    if (address == 0x7D000130 && (value & 2)) {
        reset_done = 1;
        if (!reset_sticks) value &= ~2u;
    }
    poke(address, value);
    return value;
}

static int i2c_setup_result, pd_writes, bq_writes;
static UINT16 pd[256];
static UINT8 bq[256];
INTN SwitchBsdI2cSetup(VOID) { return i2c_setup_result; }
INTN SwitchBsdI2cRead(UINT32 device, UINT32 reg, UINT8 *buffer, UINT32 size) {
    if (device == 0x18) {
        assert(size == 2);
        buffer[0] = (UINT8)pd[reg];
        buffer[1] = (UINT8)(pd[reg] >> 8);
        return 0;
    }
    assert(device == 0x6B && size == 1);
    buffer[0] = bq[reg];
    return 0;
}
INTN SwitchBsdI2cWrite(UINT32 device, UINT32 reg, CONST UINT8 *buffer, UINT32 size) {
    if (device == 0x18) {
        assert(size == 2);
        pd[reg] = (UINT16)(buffer[0] | buffer[1] << 8);
        pd_writes++;
        return 0;
    }
    assert(device == 0x6B && size == 1);
    /* Register reset and watchdog kick bits must never be written. */
    assert(reg != 1 || !(buffer[0] & 0xC0));
    bq[reg] = buffer[0];
    if (reg == 1 && (buffer[0] & 0x30) == 0x20) bq[8] = (bq[8] & 0x3F) | 0xC0;
    bq_writes++;
    return 0;
}

static void setup(void) {
    mmio_reset();
    phy_mode = reset_sticks = reset_done = 0;
    pllu_locks = 1;
    i2c_setup_result = pd_writes = bq_writes = 0;
    for (int i = 0; i < 256; i++) { pd[i] = 0; bq[i] = 0; }
    pd[0x4D] = 0x04B5; pd[0x4E] = 0x03B0;
    pd[0x1A] = 0x0104;              /* over-current protection disabled */
    pd[0x06] = 0xC0A0;              /* SPDSRC 1/2 off */
    pd[0x02] = 0x0008; pd[0x03] = 0x1080; pd[0x04] = 0x2000;
    bq[0x0A] = 0x2F; bq[0x05] = 0x9A; bq[0x01] = 0x1B;
    poke(0x4003E020, 0x55534230);   /* stale marker from a previous boot */
    poke(0x7009F004, 0x12000001 | (1u << 18));
    poke(0x7000E4F0, 0xA5A5A5AF);
    poke(0x7D000404, 0x1805);
    poke(0x7D0001B4, 0xF0401234);
    poke(0x7D000174, 0x2B);
    poke(0x7D000400, 1u << 11);
}
'''
        self.compile_and_run("switchbsd-usb-host.c", prelude, r'''
static SWITCHBSD_USB_STATUS run(void) {
    SWITCHBSD_USB_STATUS diag = {0};
    EFI_STATUS status = SwitchBsdUsbHostInit(&diag);
    assert(status == diag.Status);
    return diag;
}

int main(void) {
    setup();
    SWITCHBSD_USB_STATUS diag = run();
    assert(diag.Status == EFI_SUCCESS && diag.Step == SwitchUsbStepReady);
    assert(diag.Vbus == SwitchUsbVbusOn && diag.Flags == 0);
    /* Stale marker cleared first, ready marker written last. */
    assert(log_addr[0] == SWITCHBSD_USB_STATUS_ADDRESS && log_val[0] == 0);
    assert(peek(SWITCHBSD_USB_STATUS_ADDRESS) == SWITCHBSD_USB_READY_MAGIC);
    assert(last_write(SWITCHBSD_USB_STATUS_ADDRESS) == log_count - 1);
    /* Bring-up order. */
    int hold = first_write(CAR_RST_DEV_L_SET, CAR_L_USBD, CAR_L_USBD);
    int pllu = first_write(CAR_PLLU_BASE, PLL_BASE_ENABLE, PLL_BASE_ENABLE);
    int clock = first_write(CAR_RST_DEV_L_CLR, CAR_L_USBD, CAR_L_USBD);
    int padctl = first_write(CAR_RST_DEV_W_CLR, CAR_W_XUSB_PADCTL, CAR_W_XUSB_PADCTL);
    int utmipll = first_write(CAR_UTMIP_PLL_CFG0, 0x00FFFF00u, (25u << 16) | (1u << 8));
    int phy = first_write(USB_SUSP_CTRL, SUSP_UTMIP_RESET | SUSP_UTMIP_PHY_ENB, SUSP_UTMIP_PHY_ENB);
    int reset = first_write(USB_USBCMD, USBCMD_RESET, USBCMD_RESET);
    int host = first_write(USB_USBMODE, USBMODE_CM_MASK, USBMODE_CM_HOST);
    int port = first_write(USB_PORTSC1, PORTSC_POWER, PORTSC_POWER);
    assert(hold >= 0 && hold < pllu && pllu < clock && clock < padctl && padctl < utmipll);
    assert(utmipll < phy && phy < reset && reset < host && host < port);
    /* Pad 0 and bias stay with SNPS; unrelated bits survive everywhere. */
    assert(diag.PadMuxBefore == (0x12000001 | (1u << 18)) && peek(PADCTL_USB2_PAD_MUX) == 0x12000000);
    assert(peek(PMC_USB_AO) == 0xA5A5A5A3);
    assert(peek(USB_VBUS_SENSORS) == 0x5);
    assert(peek(USB_HOSTPC1_DEVLC) == 0x1234);
    assert(log_val[port] == 0x1001);
    assert((peek(USB_USBMODE) & 3) == 3 && !(peek(USB_USBCMD) & USBCMD_RUN));
    /* PD init as L4T does it, then charger OTG with bounded settle times. */
    assert(pd[0x1A] == 0x0100 && pd[0x06] == 0x00A0);
    assert(bq[0x05] == 0x8A && bq[0x01] == 0x2B && diag.ChargerStatus == 0xC0);
    assert(diag.PdStatus1 == 0x1080 && diag.PdStatus2 == 0x2000 && diag.PdAlert == 0x0008);
    assert(delay_total < 600000);

    /* VBUS stays off without a recognized OTG sink; the PHY still comes up. */
    setup(); pd[0x04] = 0;
    diag = run();
    assert(diag.Vbus == SwitchUsbVbusNoOtgDevice && bq_writes == 0 && diag.Status == EFI_SUCCESS);
    setup(); pd[0x4D] = 0;
    diag = run();
    assert(diag.Vbus == SwitchUsbVbusPdUnknown && pd_writes == 0 && bq_writes == 0);
    setup(); bq[0x0A] = 0;
    diag = run();
    assert(diag.Vbus == SwitchUsbVbusChargerUnknown && bq_writes == 0);
    setup(); bq[0x08] = 0x44;      /* USB host input, power good */
    diag = run();
    assert(diag.Vbus == SwitchUsbVbusInputPresent && bq_writes == 0);
    setup(); bq[0x08] = 0x84;      /* adapter input */
    diag = run();
    assert(diag.Vbus == SwitchUsbVbusInputPresent && bq_writes == 0);
    setup(); bq[0x05] = 0x8A;      /* watchdog already off: only REG01 changes */
    diag = run();
    assert(diag.Vbus == SwitchUsbVbusOn && bq_writes == 1);
    setup(); bq[0x09] = 0x40;
    diag = run();
    assert(diag.Vbus == SwitchUsbVbusFault);
    setup(); i2c_setup_result = 1;
    diag = run();
    assert(diag.Vbus == SwitchUsbVbusI2cFailed && diag.Status == EFI_SUCCESS);

    /* PHY and controller timeouts end the bring-up and keep FreeBSD out. */
    setup(); phy_mode = 1;
    diag = run();
    assert(diag.Status == EFI_TIMEOUT && diag.Step == SwitchUsbStepPhyClock);
    assert(peek(SWITCHBSD_USB_STATUS_ADDRESS) == 0 && first_write(USB_USBCMD, USBCMD_RESET, USBCMD_RESET) < 0);
    assert(delay_total < 600000 + USB_POLL_LIMIT + 1000);
    setup(); reset_sticks = 1;
    diag = run();
    assert(diag.Status == EFI_TIMEOUT && diag.Step == SwitchUsbStepReset);
    assert(peek(SWITCHBSD_USB_STATUS_ADDRESS) == 0 && first_write(USB_USBMODE, 3, 3) < 0);
    setup(); phy_mode = 2;
    diag = run();
    assert(diag.Status == EFI_TIMEOUT && diag.Step == SwitchUsbStepPhyClockAfterReset);
    assert(peek(SWITCHBSD_USB_STATUS_ADDRESS) == 0);

    /* A missing PLLU lock bit is reported, not fatal. */
    setup(); pllu_locks = 0;
    diag = run();
    assert(diag.Status == EFI_SUCCESS && diag.Flags == SWITCHBSD_USB_FLAG_PLLU_UNLOCKED);

    setup();
    SwitchBsdUsbHostClearMarker();
    assert(peek(SWITCHBSD_USB_STATUS_ADDRESS) == 0);
    return 0;
}
''')

    def test_i2c_transfers_are_bounded(self):
        prelude = TYPES + (ROOT / "config/switchbsd-usb-host.h").read_text() + MMIO + r'''
static int busy, noack, packet_stuck, rx_words, rx_pending;
static UINT32 rx_word, tx_fifo[32];
static int tx_count;
static UINT32 MmioRead32(UINTN address) {
    switch (address) {
    case 0x7000C01C: return (busy ? 1u << 8 : 0) | (noack ? 1u : 0);
    case 0x7000C058: return packet_stuck ? 1u << 4 : 0;
    case 0x7000C060: return rx_pending ? 1 : 0;
    case 0x7000C054: rx_pending = --rx_words > 0; return rx_word;
    case 0x7000C068: return 1u << 11;
    case 0x7000C08C: return 0;
    default: return peek(address);
    }
}
static UINT32 MmioWrite32(UINTN address, UINT32 value) {
    assert(log_count < MAX_LOG);
    log_addr[log_count] = address;
    log_val[log_count++] = value;
    if (address == 0x7000C050) { assert(tx_count < 32); tx_fifo[tx_count++] = value; }
    poke(address, value);
    return value;
}
static void setup(void) {
    mmio_reset();
    busy = noack = packet_stuck = rx_pending = rx_words = tx_count = 0;
}
'''
        self.compile_and_run("switchbsd-i2c.c", prelude, r'''
int main(void) {
    UINT8 value = 0x8A, pair[2] = { 0xA0, 0x00 }, buffer[2] = { 0, 0 };

    /* Pinmux, then Hekate's I2C1 clock sequence. */
    setup();
    assert(SwitchBsdI2cSetup() == 0);
    assert(peek(0x700030BC) == 0x40 && peek(0x700030C0) == 0x40);
    int held = first_write(0x60006300, 1u << 12, 1u << 12);
    int gated = first_write(0x60006324, 1u << 12, 1u << 12);
    int source = first_write(0x60006124, ~0u, (6u << 29) | 3);
    int enabled = first_write(0x60006320, 1u << 12, 1u << 12);
    int released = first_write(0x60006304, 1u << 12, 1u << 12);
    assert(held >= 0 && held < gated && gated < source && source < enabled && enabled < released);

    /* Register byte first, data little-endian, length in CNFG. */
    setup();
    assert(SwitchBsdI2cWrite(0x6B, 0x05, &value, 1) == 0);
    assert(peek(0x7000C004) == 0xD6 && peek(0x7000C00C) == 0x8A05);
    assert(first_write(0x7000C000, ~0u, 0x2802) >= 0 && (peek(0x7000C000) & (1u << 9)));
    setup();
    assert(SwitchBsdI2cWrite(0x18, 0x06, pair, 2) == 0);
    assert(peek(0x7000C00C) == 0x00A006 && first_write(0x7000C000, ~0u, 0x2804) >= 0);
    setup(); noack = 1;
    assert(SwitchBsdI2cWrite(0x6B, 0x05, &value, 1) != 0);
    setup(); busy = 1;
    assert(SwitchBsdI2cWrite(0x6B, 0x05, &value, 1) != 0);
    assert(delay_total <= I2C_TRANSFER_POLLS + 100);
    assert(SwitchBsdI2cWrite(0x6B, 0x05, buffer, 8) != 0);

    /* Repeated-start read: address phase, then a read header. */
    setup(); rx_word = 0xBEEF1234; rx_words = 1; rx_pending = 1;
    assert(SwitchBsdI2cRead(0x18, 0x4D, buffer, 2) == 0);
    assert(buffer[0] == 0x34 && buffer[1] == 0x12);
    assert(tx_count == 7 && tx_fifo[0] == 0x10 && tx_fifo[1] == 0);
    assert(tx_fifo[2] == ((1u << 16) | 0x30) && tx_fifo[3] == 0x4D);
    assert(tx_fifo[4] == 0x10 && tx_fifo[5] == 1 && tx_fifo[6] == ((1u << 19) | 0x30));
    assert(!(peek(0x7000C000) & ((1u << 9) | (1u << 10))));
    setup();
    assert(SwitchBsdI2cRead(0x18, 0x4D, buffer, 2) != 0);
    assert(delay_total <= I2C_PACKET_POLLS + 100);
    setup(); packet_stuck = 1;
    assert(SwitchBsdI2cRead(0x18, 0x4D, buffer, 2) != 0 && tx_count == 4);
    setup(); rx_word = 0x12; rx_words = 1; rx_pending = 1; noack = 1;
    assert(SwitchBsdI2cRead(0x6B, 0x0A, buffer, 1) != 0);
    assert(SwitchBsdI2cRead(0x6B, 0x0A, buffer, 0) != 0);
    assert(SwitchBsdI2cRead(0x6B, 0x0A, buffer, 9) != 0);
    return 0;
}
''')


if __name__ == "__main__":
    unittest.main()
