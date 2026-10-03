# Project status — 2026-10-03

**FreeBSD 15.1 reached userland and a root shell prompt on the Switch.** The
user's `/home/tester/Pictures/switch2.jpg` shows kernel output, the UFS RAM root,
`SWITCHBSD: USERLAND_READY`, `uname`/`sysctl` output and the final `#` prompt.
This follows build 7 firmware and the screen console update. Physical keyboard
or UART input has not yet been demonstrated. See `docs/hardware-boot-2026-10-03.md`.

## Delivered

- Pinned Hekate, Coreboot, ARM Trusted Firmware and Switch EDK2 builds.
- FreeBSD 15.1 ARM64 SWITCHDIAG kernel, EFI loader and 128 MiB UFS RAM root.
- FAT32 SD ZIP and standalone 512 MiB-class MBR/FAT32 image in `dist/`.
- Source/toolchain/patch provenance and SHA-256 checksums.
- Build instructions, firmware ABI notes and UART test checklist.

## Validation completed

- `make firmware`: all firmware stages built; architecture, entry address,
  ROM/payload size, CBFS stage presence and memory-layout checks passed.
- `make image`: final SD bundle and disk image generated.
- `make validate`: UFS checked, AArch64 static root tools checked, EFI paths
  checked, FAT image artifacts extracted and compared, ZIP integrity and
  distribution checksums verified.
- `make test`: 12 tests passed, including corrupt downloads, wrong architectures,
  dynamic-linker dependencies, overlapping regions, oversized payloads,
  wrong ROM size and incomplete SD bundles.
- `make smoke`: QEMU reached USERLAND_READY and executed `uname -a` followed by
  the SHELL_INTERACTIVE marker. Transcript: `logs/qemu-uart.log`.
- Firmware patch preparation rerun: generated diffs unchanged; exactly one
  dedicated Hekate launch function remains.

QEMU reported:

```
FreeBSD freebsd-switch 15.1-RELEASE FreeBSD 15.1-RELEASE SWITCHDIAG arm64
SWITCHBSD: SHELL_INTERACTIVE
```

## Remaining hardware work

Follow `docs/boot-test.md`, retaining the bundle hashes and full UART transcript.
A QEMU pass does not test the Hekate/Coreboot handoff, Tegra clock/carveout state,
Switch ACPI interrupts or physical UART input. Record the first failing stage
before changing firmware or kernel settings. Hardware boot to the displayed
userland shell is now confirmed by the photo; interactive input, SMP, persistent
storage, networking and power control are not validated by that observation.

## Resuming development

The interrupted original build was recovered without replacing the source
pins. Build fixes live in `scripts/prepare_firmware.py` and `patches/generated/`.
The ARM newlib package is installed. The Hekate build explicitly selects its
headers, and its compressed loader now rebuilds when the embedded payload
changes. The RAM root builds a real static `uname`; FreeBSD's stock rescue
binary has no uname applet.

`make all` runs the pipeline sequentially. Individual make targets remain
available for incremental work. See `logs/verification.log` for the final
validation/test/smoke run.

The codebase-memory CLI was attempted but reported an active unverified
coordination generation. Source inspection was used as fallback; no graph
coverage or freshness claim is made.

## SD diagnostic build 2 — 2026-10-03

The user cannot type at the UEFI shell. Source inspection confirmed that the
pinned port disables the USB host controller registration and omits the USB
keyboard driver. The SD format (FAT32/exFAT) was asked but remains unknown.

The new firmware fixes pin-control interface registration and SD Block IO
boundaries, reports SD driver initialization results and protocol dependencies
on screen, and automatically tries FreeBSD on readable FAT filesystems. On
failure, the screen stays visible for capture; only UART input continues to the
ordinary boot manager. No USB keyboard fix is claimed.

Delivered `dist/freebsd-switch-15.1-firmware-update.zip` (about 784 KiB), also
included in the rebuilt full ZIP/image. The old working-to-shell firmware and
ZIP are preserved under `build/previous-uefi-shell/`.

Validation: firmware build, artifact/image checks and 11 tests passed, including
compiling the actual SD initialization/read C templates against host protocol
mocks. Patch preparation is idempotent. See `logs/verification-build2.log`.
The last QEMU OS-shell pass remains in `logs/qemu-uart.log`; QEMU does not test
this new Tegra firmware code.

The next hardware photo confirmed all four dependency protocols were present,
but card negotiation failed with -95. No filesystem access was possible.

## SD power recovery build 3 — 2026-10-03

Photo evidence: `/home/tester/Pictures/switch1.jpg`. Hekate's SD shutdown sets
the SDMMC1 NO_IOPOWER bit; the pinned UEFI SD probe never clears it. Build 3
restores the 3.3 V pad selection and clears the gate, then waits 10 ms after
raising card power. This is a likely cause of the hardware failure, pending
another physical test. Command and PMC register diagnostics are now displayed.
The no-block-device screen no longer suggests changing filesystem format.

Build 2 ROM, payload, update ZIP, build metadata and checksums are retained in
`build/previous-build2/`. The original backup remains unchanged.

Validation: `make firmware`, `make image`, `make validate` and all 12 tests
passed. Tests compile the real power-recovery/command-diagnostic template and
check preservation of unrelated PMC bits, the voltage/gate/GPIO/delay ordering,
and first/last command error recording. Patch preparation remains idempotent;
recorded build-input hashes match the files. See `logs/verification-build3.log`.
The firmware-only ZIP is in `dist/freebsd-switch-15.1-firmware-update.zip`;
the full ZIP/image also contain build 3. QEMU was not rerun for the firmware
change; the earlier OS-shell test does not cover this hardware fix.

Next hardware step: extract the build 3 firmware-update ZIP at the existing
SD root and boot the same Hekate entry. Capture the whole build 3 screen,
including the PMC and command lines. No keyboard or card reformat is required.

## Screen console update — 2026-10-03

User report after build 3: "something booted (freebsd boot login appeared) but
now i see only one white rectangle". It is not yet clear whether "login" means
the loader logo/menu or a literal login prompt. This RAM root does not run a
login prompt. No kernel/userland hardware boot claim is made.

Source review found that FreeBSD's ConOut parsing detects the UART but not the
Switch GOP vendor device path, enabling serial-only kernel output. The loader
configuration now overrides that with multiple consoles and video primary.
This is a plausible explanation of a blank screen after the loader; it does
not prove that the rectangle was a cursor or exclude a kernel failure.

The console-update ZIP replaces only `boot/loader.conf` and adds instructions.
The current firmware is build 7. Set `boot_serial="YES"` to use a UART userland shell;
the new default sends kernel messages to both and userland to the screen.
The previous configuration and distribution metadata are saved under
`build/previous-console-update/`.

Validation: rebuilt full ZIP/image plus the 1.7 KiB console-update ZIP;
`make validate test` passed (12 tests). All recorded build-input hashes match.
QEMU with a RAM framebuffer reached the on-screen USERLAND_READY marker and
interactive shell. `sysctl kern.console` showed ttyv0 first, `uname -a` succeeded,
and the injected shell command printed `display-shell-ready`. UART also carried
kernel messages. Evidence: `logs/qemu-framebuffer.png`,
`logs/qemu-framebuffer-uart.log`, and `logs/qemu-framebuffer-check.md`.
This test uses QEMU's framebuffer and USB keyboard; Switch display and input
remain unverified. Firmware/kernel binaries were not rebuilt for this update.

Next: extract `dist/freebsd-switch-15.1-console-update.zip` at the existing SD
root, boot the same Hekate entry, and obtain a photo of the last screen.

## Hardware userland confirmed — 2026-10-03

The next photo, `/home/tester/Pictures/switch2.jpg`, shows FreeBSD 15.1-RELEASE
SWITCHDIAG arm64 running on ARM Cortex-A57 r1p1, USERLAND_READY, RAM-root startup
messages and a `#` prompt. The white block at that prompt is a cursor. This
confirms the kernel and userland milestone on hardware, not just the EFI loader.
The displayed uname/sysctl output comes from `/etc/rc`; it is not evidence of
manually entered commands. Physical input remains the next unverified step.

Preserved the supplied local firmware, console configuration, update ZIPs, full
SD ZIP, metadata and checksums in `build/hardware-userland-2026-10-03/`. Its
`observation.json` records the photo hash. The actual SD card was not read back,
so the photo is not independent binary-hash verification. Existing distribution
files and their pre-test metadata were retained without rebuilding.

## USB host rollback build 7 — 2026-10-03

Builds 4–6 enabled the Tegra EHCI driver and an ACPI handoff, but the latest
hardware test stopped before UEFI and showed a black screen. Build 7 restores
the original EDK2 DSC, firmware-volume list and ACPI table, leaving USB host
input disabled while retaining the SD power recovery.

Build 4–6 artifacts are preserved under `build/previous-build4/`,
`build/previous-build5/` and `build/previous-build6/`. The new
firmware-only ZIP and rebuilt full SD image are in `dist/`. No physical USB
keyboard test has been completed yet; the next test should type `echo USB_OK`
at the final `#` prompt with the keyboard connected before boot.

## Hardware display and userland recovery confirmed — 2026-10-03

The user reports that build 7 again reaches the FreeBSD userland shell on the
Switch display. This confirms the USB-host rollback restores the earlier boot
milestone. Keyboard input and a manually entered command remain unverified.
