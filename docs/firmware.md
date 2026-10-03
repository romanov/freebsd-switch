# Firmware integration notes

All source revisions are in `sources.lock.json`. Upstream references:
[Switch EDK2](https://github.com/imbushuo/NintendoSwitchPkg),
[Switch Coreboot](https://github.com/imbushuo/Coreboot/tree/switch-display),
[Hekate](https://github.com/CTCaer/hekate),
[FreeBSD source](https://cgit.freebsd.org/src/).

## Handoff ABI

The launcher requires a 10 MiB Coreboot ROM, copies it to `0xcf600000`, computes
FNV-1a over the whole ROM, and relocates the first 28 KiB to `0x40010000` with
Hekate's relocator. It flushes/disables Hekate's cache through `hw_deinit` and
writes five 32-bit little-endian words at `0x4003e000`:

| Word | Value |
| --- | --- |
| Magic | `0x53425344` |
| ABI version | `1` |
| ROM address | `0xcf600000` |
| ROM length | `0x00a00000` |
| Checksum | 32-bit FNV-1a of all ROM bytes |

Coreboot replaces its USB BootROM transport with read-only DRAM. It checks
magic, version, fixed address and size before use and verifies the checksum in
the bootblock. This checksum detects transfer corruption; it is not a signature
or an authenticity check.

Hekate performs SDRAM initialization. Coreboot's MBIST rerun is skipped and
`BOOTROM_SDRAM_INIT` retains existing DRAM. The external memory-training blob
is disabled. The dedicated payload skips HOS autoboot and update paths and
keeps Hekate's initial memory rate. Compatibility of this state with Coreboot's
clock and carveout setup still needs physical hardware validation.

## Memory layout

| Use | Address | Size |
| --- | --- | --- |
| Coreboot bootblock | `0x40010000` | 28 KiB |
| Coreboot romstage | `0x40017000` | 60 KiB |
| Pre-RAM console | `0x40026000` | 4 KiB |
| Pre-RAM CBFS cache | `0x40027000` | 92 KiB |
| Handoff descriptor (old USB bounce area) | `0x4003e000` | 20 bytes |
| EDK2 firmware volume | `0x80110000` | 896 KiB |
| Coreboot ramstage | `0x80200000` | 256 KiB |
| Complete ROM | `0xcf600000` | 10 MiB |
| Post-RAM CBFS cache | `0xd0000000` | 8 MiB |
| Framebuffer | `0xdfb80000` | 4.5 MiB |
| TrustZone reservation | `0xfec00000` | 20 MiB |

The descriptor occupies space formerly reserved for USB traffic; the replacement
transport does not use USB. Static checks are not a proof that a running
firmware never writes another reserved region.

## ACPI and serial review

The pinned MADT describes four Cortex-A57 CPUs with MPIDRs 0–3 and GICv2 at
`0x50041000` (distributor) / `0x50042000` (CPU interface). GTDT uses physical
interrupt 30, virtual interrupt 27, hypervisor interrupt 26, secure interrupt
29 and the architectural timer. FADT advertises PSCI through SMC; Coreboot
includes the pinned Switch ARM Trusted Firmware BL31. Secondary CPU startup
is disabled through `kern.smp.disabled=1` for this milestone.

EDK2's DSDT names UARTB `NVDA0100` at `0x70006040`. Its serial library uses byte
accesses on registers four bytes apart and divisor 221. FreeBSD is patched to
recognize that ACPI ID as ns8250 with register shift 2, width 1 and 408 MHz
reference clock. `hw.uart.console` supplies the same values for early console
discovery, since this EDK2 port has DBG2 but no SPCR table. The loader uses the
EFI console before ExitBootServices.

## Build adaptations

The project supplies Python-based build orchestration in place of the upstream
PowerShell wrapper. It uses local host-tool aliases for EDK2, prioritizes ARM
newlib headers over FreeBSD headers in Hekate's cross compiler, enables Hekate
UART logging, adds EDK2's missing RegisterFilterLib mapping, updates obsolete
Python array calls, and adapts the old Coreboot make rules to current GNU make.
All upstream file changes are recorded in `patches/generated`.

## Build 2 SD diagnostics

The SD driver records its initialization stage and result in a boot-services
configuration table. A separately allocated table survives a failed driver
entry point, so the boot manager can show the actual failure once the display
console is ready. If the driver never dispatches, the screen instead lists
pin-control, clock and PMIC protocol availability.

The boot manager tries only filesystems containing both the FreeBSD EFI loader
and the project RAM-root path. Failure remains on screen until UART input. The
USB keyboard path is not enabled by this update. SD Block IO uses an inclusive
last-sector index, validates read ranges without overflowing, and delegates
partition recognition to the EDK2 partition driver. Host tests compile the actual
C templates against protocol mocks; physical SD initialization remains a
hardware test.

## Build 3 SD power recovery

The build 2 hardware photo (`/home/tester/Pictures/switch1.jpg`) shows stage 5
(card negotiation), EFI_DEVICE_ERROR and card result -95, with no block devices.
The return code alone does not identify which command failed: this driver can
return -EOPNOTSUPP after unsuccessful MMC fallback or SD readiness polling.

In the pinned Hekate source, `sdmmc1_disable_power()` sets bit 12 in
`PMC_NO_IOPOWER` during `sd_end()`. The dedicated launcher calls `sd_end()`
before jumping to Coreboot. The upstream UEFI SD driver raises GPIO E4 but
does not clear this pad-power gate. Its PMIC dependency already restores LDO2
to 3.3 V. Build 3 sets bit 12 in `PMC_PWR_DET_VAL`, clears bit 12 in
`PMC_NO_IOPOWER`, performs readbacks, then raises the card-power GPIO and waits
10 ms before initialization. Read-modify-write operations preserve all other
PMC domains. These definitions and the settling interval follow Hekate's
`bdk/soc/pmc_t210.h` and `bdk/storage/sdmmc_driver.c` at the pinned revision.

Diagnostic table version 2 saves the PMC values, last command, first command
transport error, command interrupt status before it is cleared, and controller
state. The raw response register can be stale after a transport error. The
boot manager no longer suggests a filesystem-format change when the driver
has not produced a block device. Native C tests check sequencing, preservation
of unrelated PMC bits and first/last-error recording. This source-level fix
has not yet been validated on physical hardware.

## Console selection after the SD fix

The user now reports seeing a FreeBSD boot screen before a white rectangle.
This suggests loader progress, but is not evidence of kernel or userland startup.
`SimpleFbDxe.c` publishes GOP with a hardware vendor device path. In the pinned
FreeBSD loader, `parse_uefi_con_out()` recognizes ACPI display and PCI paths,
plus serial paths; it does not recognize this vendor graphics path. The firmware
adds its serial path to ConOut as well, so the loader can select serial-only
kernel output despite showing its own UI on the framebuffer. `console="efi"`
alone does not reset the inherited `boot_serial` flag.

The console update explicitly sets `boot_multicons="YES"` and `boot_serial="NO"`.
`boot_env_to_howto()` treats `NO` as clearing the corresponding bit when
reconstructing the kernel flags. This makes video primary and enables both
kernel consoles. UART remains configured for kernel diagnostics; select
`boot_serial="YES"` for userland and an interactive shell over UART instead.
The firmware and kernel binaries are unchanged by this update. The blank screen
could still have another cause; the next hardware screen must establish progress.
