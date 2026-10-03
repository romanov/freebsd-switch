# Project status — 2026-10-03

**FreeBSD 15.1 reached userland and a root shell prompt on the Switch.** The
user's `/home/tester/Pictures/switch2.jpg` shows kernel output, the UFS RAM root,
`SWITCHBSD: USERLAND_READY`, `uname`/`sysctl` output and the final `#` prompt.
This follows build 7 firmware and the screen console update. The user has since
confirmed physical USB keyboard input with build 8. UART input remains
unverified. See `docs/hardware-boot-2026-10-03.md`.

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

## USB keyboard build 8 — source only, 2026-10-03

Goal: a USB keyboard on a USB-C OTG adapter, at the FreeBSD shell only.

Source review of builds 4–6 found several plausible black-screen causes in
`EhciPciEmulationDxe`:
- UTMIPLL is never taken out of IDDQ;
- an `ASSERT_EFI_ERROR` on the PHY clock timeout hangs a DEBUG build before
  any console exists;
- `PLLU_BASE` is rewritten;
- GPIO CC4 is driven, which is the Joy-Con/fan 5 V rail on this console.

Nothing powered VBUS, and `UsbKbDxe` was never built. See `docs/firmware.md`.

Build 8 binds no UEFI USB driver. The boot manager does the setup late,
after the screen is up:
- it checks the BM92T36 for an OTG sink;
- it switches the BQ24193 to OTG boost after identity and input-power checks;
- it brings up the USB1 UTMI PHY and EHCI controller in host mode, following
  Hekate's sequence. Every wait is bounded.

On success it writes a ready marker in IRAM. The DSDT's `SWBS0001` device
reads that marker in `_STA`, so FreeBSD only sees a fully initialized
controller. A new kernel driver, `switchbsd_ehci_acpi`, adds the Tegra EHCI
quirks to the generic attachment. `/etc/rc` reports the controller and the
keyboard. `switchbsd/usb-host-disable` on the SD card skips everything.

Delivery is `dist/freebsd-switch-15.1-usb-update.zip`: firmware, kernel and
RAM root. See `USB-UPDATE.md`.

Validation so far, on a Windows checkout without the pinned build:
- 14 host tests pass, including two new ones. They compile the actual USB host
  and I2C templates against register and device models.
- Both templates compile cleanly with `-Wextra -Wconversion`.
- A dry run of `prepare_firmware.py` against the pinned upstream files applies
  every anchor, and a rerun changes nothing.

Not yet done:
- the FreeBSD-host steps: `make firmware freebsd image validate test smoke`,
  which also compile the ASL and the kernel driver;
- keeping the build 7 artifacts in `build/previous-build7/` before rebuilding;
- any hardware test.

Next hardware step: plug the keyboard in before power-on, photograph the build
8 USB lines, then type `echo USB_OK` at the `#` prompt. Afterwards unplug the
adapter and boot Hekate once; that restores charge mode.

## Codex root — source only, 2026-10-03

Goal: run the FreeBSD `codex` package from the Switch shell after boot.

Package research against `FreeBSD:15:aarch64` (index of 2026-09-29):
- `codex` 0.155.1 in `latest` (0.142.4 in `quarterly`). It installs `codex`,
  `codex-code-mode-host` (V8) and `codex-responses-api-proxy`, about 191 MiB.
- The package's declared dependencies add up to about 573 MiB, mostly python312
  and X11, pulled in by glib and dbus. The binaries themselves only link
  `libdbus-1`, `libonig`, `libzstd` and base libraries, so the build follows
  ELF `DT_NEEDED` entries instead of installing whole packages.
- git-lite's libcurl needs the base Kerberos libraries, so
  `WITHOUT_KERBEROS` was removed from `config/src.conf`.

Design:
- A second RAM root, `boot/rootfs-codex.ufs.gz`, selected by
  `boot/loader.conf.local`. Only one `mfs_root` is ever loaded, so the root
  stays `md0`.
- The loader adds `.gz` to the configured name and decompresses while
  loading, which cuts the SD read by about 3×.
- Deleting `boot/loader.conf.local` returns to the unchanged 128 MiB static root.
- Networking needs the build 8 USB host, a USB 2.0 hub, and a USB Ethernet
  adapter or Android USB tethering.

Validation so far, on the Windows checkout:
- 29 host tests pass, 15 of them new: the ELF parser and dependency closure on
  synthetic ELF files, soname links, the Kerberos error hint, hard-link
  preservation, the codex-root validation, the loader setting, and a `sh -n`
  check of the rc scripts.
- The real aarch64 packages, extracted in a scratch directory and run through
  `codex_root.py` (packages only, without the FreeBSD world):
  - the parser read the `DT_NEEDED` entries of all 210 ELF files;
  - the closure copied 14 package libraries (6 MiB): dbus, oniguruma, zstd,
    gettext, pcre2, curl, expat, brotli, idn2, unistring, psl, nghttp2 and
    ssh2. Python, glib and X11 were not needed;
  - the remaining sonames are base libraries, including
    `libgssapi_krb5.so.122` and `libkrb5.so.122`;
  - git-core's 175 entries stayed 26 inodes, and no symlinks were broken;
  - package content is 227 MiB, which gzips to 100 MiB. The estimate is a UFS
    image of about 380 MiB and a `.gz` of about 110 MiB.

Not yet done:
- keeping the current `dist/` in `build/previous-build8/` before rebuilding;
- the FreeBSD-host steps: `make fetch freebsd image validate test smoke`. The
  `freebsd` step is a full world rebuild because of the Kerberos change;
- recording the actual codex root and gzip sizes, and the package versions;
- any hardware test.

Open risks:
- A root of about 400 MiB is untested in the Switch loader and early kernel
  mapping; 128 MiB is proven.
- SD read time with the UEFI driver.
- Entropy before the first TLS handshake (no hardware RNG driver).
- The OTG power budget for the hub, keyboard and network adapter.
## Diagnostic image revision 1 — 2026-10-03

The user confirms that build 8's keyboard works. Before updating userland,
the local distribution, checksums, RAM root and loader configuration were
preserved in `build/hardware-keyboard-2026-10-03/`, with the user's report
recorded in `observation.json`. No SD-card read-back or new photo is claimed.

The diagnostic RAM root adds static ARM64 `usbconfig`, `devinfo`, `diskinfo`,
`sha256` and `timeout`, plus rescue file/paging and storage tools. The
`switchbsd-report` command collects build identity, USB/devices, storage,
settings and kernel logs. Probes have time limits and report failures without
discarding the remaining sections. Usage: `switchbsd-report > /tmp/report.txt`
then `less /tmp/report.txt`. Reports remain volatile unless explicitly copied
to mounted persistent storage. See `docs/diagnostics.md`.

Delivery: `dist/freebsd-switch-15.1-diagnostics-update.zip` (about 10 MiB),
containing only the RAM root, guide and build timestamp. The full SD ZIP/image
and USB update also include the new RAM root. Firmware, Hekate payload, kernel
and loader configuration were compared byte-for-byte with the preserved
working bundle and are unchanged. The root remains 128 MiB, with about
23 MiB of staged files.

Validation completed:
- `make image` and `make validate` passed, including static architecture checks,
  command aliases, build identity, ZIP contents and distribution checksums.
- `make test`: 18 tests passed, including partial reports after probe errors
  and timeouts. The Clang unused-helper fix from the previous investigation
  remains included.
- `make smoke`: QEMU executed the shell, USB and storage tools, pager, a real
  timeout, a known SHA-256 result and the report with zero failed probes.
  The harness now sends commands in paced chunks and checks each step.
- Logs: `logs/image-diagnostics.log`, `logs/verification-diagnostics.log`,
  `logs/tests-diagnostics.log`, `logs/smoke-diagnostics.log`,
  `logs/qemu-uart.log` and `logs/qemu-diagnostics-report.txt`.

The diagnostic update still needs a Switch hardware check. Next: back up the
card's `boot/rootfs.ufs`, extract the diagnostic update, boot and collect a
report. Confirm that it identifies the keyboard and Tegra controller, and
that the pager returns to the shell. Existing OTG power behavior is unchanged.

## Wi-Fi and SSH update (diagnostic image revision 2) — 2026-10-03

Goal: network access and SSH logins using the user's TP-Link TL-WN821N v5/v6
(RTL8192EU, USB ID 2357:0107) on the USB-C port. The Switch's built-in Wi-Fi
(Broadcom BCM4356 on Tegra PCIe) is out of scope. Its firmware never brings up
PCIe, and FreeBSD 15.1's `brcmfmac` is unfinished and not built by default.

What changed:
- Kernel: `wlan`, its WEP/CCMP/TKIP/AMRR modules, `rtwn`, `rtwn_usb` and
  `rtwnfw` are built into SWITCHDIAG. Modules stay disabled.
- RAM root:
  - `sshd`, `sshd-session`, `sshd-auth`, `sftp-server`, `wpa_supplicant`,
    `wpa_cli`, `pw`, `pwd_mkdb`, `arp` and `chown` are copied from the staged
    world, with `ld-elf.so.1` and their `DT_NEEDED` library closure. Recovery
    tools stay static.
  - The `route`, `ping`, `dhclient`, `pkill` and `pgrep` rescue applets are
    added, with `/sbin` aliases.
  - Password database for `root`, `sshd`, `_dhcp` and `nobody`.
  - dhclient hooks.
  - Every file is owned by root:wheel through an mtree spec passed to `makefs -F`.
- `switchbsd-net` (`config/switchbsd-net`):
  - reads `switchbsd.*` loader variables from `boot/loader.conf.d/network.conf`
    once per boot;
  - writes `wpa_supplicant.conf` and `authorized_keys`, and sets the root
    password with `pw`;
  - removes the Wi-Fi and SSH passwords from kenv;
  - joins Wi-Fi, runs DHCP on Wi-Fi and wired interfaces, starts sshd, and
    prints the address and host-key fingerprint.

  `/etc/rc` runs it under `timeout --foreground`, so the shell always starts.
- SSH host key: generated once in `build/ssh/` and reused across rebuilds. Its
  fingerprint is recorded in the build identity and printed by `make image`.
- `boot/entropy` (4096 random bytes per build) seeds random(4) through the
  loader.
- New `dist/freebsd-switch-15.1-network-update.zip`. No ZIP ever contains a
  real `boot/loader.conf.d/*.conf`; validation enforces this.
- License notices for the Realtek firmware, wpa_supplicant and OpenSSH are in
  `switchbsd/licenses/`.

Validation so far (Windows checkout, no FreeBSD build here):
- Host tests: the new `tests/test_network.py` (7 tests) and the extended
  `tests/test_artifacts.py` pass. They cover ELF dependency reading, library
  closure, the ownership spec and the SSH fingerprint against `ssh-keygen`. The
  shell tests ran under msys `dash`, standing in for FreeBSD `/bin/sh`.
- **Pending on the FreeBSD 15.1 build host:** `make freebsd image validate test
  smoke`. The kernel configuration changed, so `make freebsd` must run first.
  The smoke test now checks DHCP on a virtual network card, the CRLF settings
  file, secret removal, wlan/wpa tools, and SSH key login, password login and
  `scp` from the host.
- **Pending on hardware:** `rtwn0` attach, association, DHCP and an SSH login
  from a computer. See `NETWORK-UPDATE.md`.
