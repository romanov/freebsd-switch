# Changelog

Builds are numbered by firmware revision. All dates are 2026-10-03; details
and validation records are in [STATUS.md](STATUS.md).

## Codex root update (unreleased, not yet built or tested on hardware)

The firmware is unchanged from build 8, so there is no new build number.

### Added
- A second RAM root, `boot/rootfs-codex.ufs.gz`, with the OpenAI Codex CLI
  (`misc/codex`), bash, ripgrep and git-lite from the FreeBSD 15 aarch64
  packages. `boot/loader.conf.local` selects it; deleting that file boots the
  diagnostic root again.
- `scripts/codex_root.py`:
  - fetches the packages with the host's `pkg`, using a private configuration;
  - stages only the files that the root's programs execute or link, by
    following their ELF `DT_NEEDED` entries. The packages' declared
    dependencies (python, X11) would add about 360 MiB without being used;
  - keeps hard links, so git-core does not grow the image.
- The FreeBSD dynamic linker, base shared libraries, common command-line
  tools (`config/codex-tools.txt`), CA certificates, password databases and
  `ntp.conf` in the codex root.
- `/etc/rc.codex` and `switchbsd-net`: tmpfs on `/tmp` and `/root`, DHCP on
  every Ethernet interface, an NTP clock with the build time as the lower
  bound, and the codex version on screen. Every wait is bounded.
- Kernel drivers for USB network adapters: `axge`, `axe`, `cdce`, `urndis` and
  `ipheth` (`ure`, `smsc` and `muge` come from GENERIC).
- `dist/freebsd-switch-15.1-codex-update.zip`, `CODEX.md`, and the package
  versions and SHA-256 values under `target_packages` in `build-info.json`.
- Host tests for the ELF dependency closure, link handling, the loader
  setting and the shell scripts (`tests/test_codex_root.py`).

### Changed
- `config/src.conf` no longer sets `WITHOUT_KERBEROS`, because git's HTTPS
  transport needs libcurl and libcurl links the Kerberos libraries.
- `make freebsd` also runs `make distribution` into `build/world`.
- `make fetch` also fetches the codex packages.
- The FAT disk image grows with the bundle; it is never smaller than 512 MiB.
- `make smoke` boots both RAM roots, the codex one with a QEMU network.
- `validate.py` checks that the codex root is self-contained (libraries,
  symlinks, AArch64 ELF), its gzip copy, the loader setting and the new ZIP.

## Build 8 — USB keyboard for the FreeBSD shell (unreleased, untested on hardware)

### Added
- Firmware USB-C host setup in the boot manager (`config/switchbsd-usb-host.c`).
  It runs after the screen is up:
  - checks the BM92T36 USB-C controller for an attached OTG device;
  - switches the BQ24193 charger to 5 V OTG boost, after identity and
    input-power checks;
  - brings up the USB1 UTMI PHY and EHCI controller in host mode, using
    Hekate's sequence adapted for host mode.

  Every wait is bounded, and failures are shown on screen without stopping the boot.
- A bounded I2C1 driver ported from Hekate (`config/switchbsd-i2c.c`), with
  repeated-start reads.
- DSDT device `USB0` (`SWBS0001`, `config/switchbsd-usb.asl`). It is visible to
  the OS only after the firmware writes its ready marker at `0x4003E020`.
- FreeBSD driver `switchbsd_ehci_acpi` (`config/switchbsd-ehci-acpi.c`). It
  reuses the generic EHCI attachment and adds the Tegra transaction-translator,
  port-speed and host-mode quirks.
- `/etc/rc` reports the USB controller and keyboard before the shell starts.
- `dist/freebsd-switch-15.1-usb-update.zip` (firmware, kernel, RAM root) and
  `USB-UPDATE.md`.
- Kill switch: an empty `switchbsd/usb-host-disable` on the SD card skips the
  USB setup and restores build 7 behaviour.
- Host tests for the USB bring-up order, VBUS refusal paths, timeouts, the
  marker contract and I2C framing (`tests/test_usb_host.py`).

### Changed
- Firmware banner: "SwitchBSD SD diagnostic build 8 (USB host for FreeBSD)".
- `prepare_usb_host()` now runs after `prepare_sd_diagnostics()`. It builds the
  DSDT from the pristine table, so reruns are no-ops.
- `validate.py` checks the new ZIP and the IRAM marker's place in the memory layout.

### Fixed
- Removed the stale "USB host enabled; keyboard should work" line from the
  firmware screen.

## Build 7 — USB host rollback

### Changed
- Restored the original EDK2 DSC, firmware-volume list and ACPI table, leaving
  UEFI USB host input disabled. SD power recovery is kept.

### Verified
- The user reports that build 7 reaches the FreeBSD userland shell on the
  Switch display again.

## Builds 4–6 — UEFI USB host attempt (withdrawn)

- Enabled the Tegra EHCI driver (`EhciPciEmulationDxe`) and an ACPI handoff.
  The latest hardware test showed a black screen before UEFI. The build 8
  source review lists the likely causes in `docs/firmware.md`.

## Hardware userland milestone

### Verified
- A photo shows FreeBSD 15.1-RELEASE SWITCHDIAG arm64 booting, the UFS RAM
  root, `SWITCHBSD: USERLAND_READY` and a `#` prompt on the Switch display.
  Physical input has not been demonstrated.

## Screen console update

### Fixed
- The FreeBSD loader picked serial-only kernel output because it does not
  recognize the Switch GOP vendor device path. `boot/loader.conf` now sets
  `boot_multicons="YES"` and `boot_serial="NO"`, so the screen is primary and
  UART mirrors kernel messages.

## Build 3 — SD power recovery

### Fixed
- Hekate's `sd_end()` gates the SDMMC1 I/O pads, and the UEFI driver never
  ungated them. Build 3 restores 3.3 V pad selection, clears `PMC_NO_IOPOWER`
  bit 12, and waits 10 ms after card power.

### Added
- PMC and command-level SD diagnostics on screen.

## Build 2 — SD diagnostics

### Added
- SD initialization stages, results and protocol dependencies are shown on
  screen.
- Automatic FreeBSD loader discovery on readable FAT filesystems, with a
  persistent error screen.

### Fixed
- Pin-control protocol registration.
- SD Block IO last-sector number, plus bounds and alignment checks.

## Initial build — QEMU userland

### Added
- Pinned Hekate, Coreboot, ARM Trusted Firmware, Switch EDK2 and FreeBSD 15.1
  sources with SHA-256 verification.
- A dedicated Hekate payload with a checked DRAM handoff to a patched Coreboot.
- SWITCHDIAG kernel, EFI loader and 128 MiB UFS RAM root; FAT32 SD ZIP and disk image.
- Validation, unit tests and a QEMU smoke test reaching an interactive shell.
