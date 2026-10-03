# SD power recovery firmware update (build 7)

The build 2 photo shows SD card negotiation failing at stage 5 with result
`-95`, before any block device or filesystem is available. Build 3 restores
the SDMMC1 I/O pad power disabled by Hekate during handoff. This is a likely
cause found in the source; the fix still needs a Switch hardware test. Build 7
returns to the last display-tested firmware path and leaves the experimental
USB host driver disabled. This is the recovery build for the black screen.

## Apply from your computer

1. Power off the Switch and connect its SD card to your computer.
2. Back up `switchbsd/coreboot.rom` and
   `bootloader/payloads/hekate-switchbsd.bin` from the card.
3. Extract `dist/freebsd-switch-15.1-firmware-update.zip` directly into the SD
   card's root, replacing files with matching paths. Keep the existing `boot`,
   `EFI` and normal Hekate configuration.
4. Boot **More Configs → FreeBSD 15.1 experiment** again.

No commands at `Shell>` are needed. Do not reformat your existing card for this
update. The photographed error occurs before filesystem access, so changing
the card's filesystem would not resolve this particular initialization failure.

## Expected screen

The firmware displays **SwitchBSD SD diagnostic build 7 (USB host disabled)**, with these lines:

- Pin control / Peripheral clocks / SD clocks / Power controller
- SD stage / SD status / Card result
- PMC NO_IOPOWER before and after recovery / PWR_DET
- Last command / raw response / command IRQ / present state / clock / power
- First command error, if one occurred
- Block devices / FAT filesystems

If a filesystem contains both `EFI/BOOT/BOOTAA64.EFI` and `boot/rootfs.ufs`, the
firmware attempts to launch the FreeBSD loader automatically. If discovery or
launch fails, it keeps the diagnostic screen visible instead of dropping into
an unusable shell. Record the complete screen, particularly SD stage, SD
status, Card result, PMC values, command details, Block devices and FAT filesystems.
The SDMMC1 bit (`0x00001000`) should be clear in the second NO_IOPOWER value
and set in PWR_DET. Other bits belong to other domains and are preserved.
The response register can contain an earlier response after a command error.

USB host input is intentionally disabled in this recovery build. Use the
keyboard-free boot path to confirm the display and FreeBSD launch screen return.
UART remains available as a fallback.

## What changed

- Restored the SDMMC1 pad voltage selection to 3.3 V and cleared its PMC
  NO_IOPOWER bit, which Hekate's `sd_end()` sets before the Coreboot handoff.
  The existing PMIC driver already restores the LDO2 regulator to 3.3 V.
- Added a 10 ms wait after enabling card power, before host/card initialization.
- Recorded PMC state, command transport errors and controller registers.
- Removed the filesystem-format hint when no block device exists.
- Kept `EhciPciEmulationDxe` out of the firmware-volume list. Earlier USB-host
  builds could stop before the display initialized on this Switch.

Build 2 changes retained:

- Fixed pin-control protocol registration to pass the interface address.
- Corrected SD Block IO's last-sector number (`sector count - 1`).
- Added zero-length, alignment, media and overflow checks to block reads.
- Replaced the SD driver's MBR scan and endless failure loop with one checked
  sector-zero read; partition interpretation remains with the partition driver.
- Saved SD initialization stages and errors for display after console startup.
- Added explicit loader discovery and a persistent error screen in the boot
  manager; this works without an SD-hosted startup script or keyboard.

The build and host-side tests exercise power sequencing, preservation of other
PMC bits, error reporting and block I/O boundary handling. They do not emulate
Tegra SD hardware. FreeBSD loader/kernel startup on the Switch remains unverified.

The full SD ZIP and disk image also contain this update. On the build host,
build 2 firmware and its update ZIP are retained in `build/previous-build2/`.
The original shell firmware remains in `build/previous-uefi-shell/`.
