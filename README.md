# FreeBSD 15.1 on Switch: serial boot experiment

Target: original Nintendo Switch **Erista / Tegra210**. This is an experimental
boot bundle, not a supported FreeBSD hardware port. **FreeBSD 15.1 has reached
userland and a root shell prompt on the Switch display.** The hardware photo
shows `SWITCHBSD: USERLAND_READY`; the user has also confirmed USB keyboard
input with build 8. Physical UART input remains unverified.
See the [hardware boot record](docs/hardware-boot-2026-10-03.md).
The OS image can also be tested independently under ARM64 QEMU.

Boot chain:

```
Hekate → dedicated SwitchBSD Hekate payload → Coreboot → EDK2
        → EFI/BOOT/BOOTAA64.EFI → FreeBSD kernel → UFS RAM root → console shell
```

The dedicated payload reads `switchbsd/coreboot.rom` from FAT32. Its 10 MiB ROM
is too large for Hekate's ordinary payload loader. The launcher copies the ROM
to DRAM and passes a checked descriptor to a patched Coreboot bootblock. EDK2
then reads the FreeBSD loader, kernel and RAM root from SD. FreeBSD needs no
native SD driver to reach this first milestone.

For the reported shell with no mappings and no keyboard, see
[the build 3 SD power recovery update](UPDATE.md). The build 2 photo narrows the
failure to card negotiation; build 3 restores SD pad power after Hekate's
shutdown and keeps failures visible on screen. Build 7 is the display recovery
firmware; it leaves the experimental Tegra USB host disabled after the earlier
USB builds produced a black screen before UEFI.

Build 8 adds a USB keyboard for the FreeBSD shell through a USB-C OTG adapter;
see the [USB keyboard update](USB-UPDATE.md). The firmware powers the port and
starts the USB1 controller after the screen is up, without a UEFI USB driver,
and a small FreeBSD driver takes the controller over. Keyboard input is now
user-confirmed on hardware; `switchbsd/usb-host-disable` on the SD card returns
to build 7 behaviour.

The [diagnostic RAM-root update](docs/diagnostics.md) adds `usbconfig`, `devinfo`,
`diskinfo`, `sha256`, file/paging tools and `switchbsd-report`. At the shell,
run `switchbsd-report > /tmp/report.txt`, then `less /tmp/report.txt`. Reports
include build identity, USB/device/storage details and kernel logs. Files in
`/tmp` disappear on reboot.

For the subsequent FreeBSD screen followed by a white rectangle, apply the
[screen console update](CONSOLE-UPDATE.md). It enables kernel output on both
the framebuffer and UART, with the screen primary. Set `boot_serial="YES"` in
`boot/loader.conf` when an interactive UART shell is needed instead.

## Build on FreeBSD 15.1 amd64

Install the host tools (administrator access required for this step):

```sh
pkg install python312 git gmake bash gcc14 bison acpica-tools \
    aarch64-none-elf-gcc aarch64-none-elf-binutils \
    arm-none-eabi-gcc arm-none-eabi-binutils arm-none-eabi-newlib \
    mtools qemu-nox11
```

From this directory:

```sh
make doctor
make fetch
make firmware
make freebsd
make image
make validate test smoke
```

`JOBS=8` is the default parallelism within builds. Set `JOBS` in the environment
to change it. `make all` runs the complete sequence in order. Allow several GiB
for sources and object files. Builds and image creation operate on project
files; they never write a physical disk. `make freebsd` uses `arm64/aarch64`,
keeps the host configuration out of the cross-build, and stages the result in
`build/world`.

Source archives and SHA-256 digests are pinned in `sources.lock.json`. Fetching
verifies each archive before extraction. Local source adaptations are applied
by `scripts/prepare_firmware.py` and saved in `patches/generated/`; original
files remain in `cache/originals/`. A second build reuses existing objects.

## Output

- `dist/freebsd-switch-15.1-sd.zip`: files to extract onto a FAT32 SD card.
- `dist/freebsd-switch-15.1-firmware-update.zip`: small firmware-only update.
- `dist/freebsd-switch-15.1-console-update.zip`: small screen-console configuration update.
- `dist/freebsd-switch-15.1-usb-update.zip`: build 8 firmware, kernel and RAM root for the USB keyboard.
- `dist/freebsd-switch-15.1-diagnostics-update.zip`: diagnostic RAM root and guide for an existing build 8 installation.
- Every ZIP contains `switchbsd/BUILD-TIME.txt` and carries the same UTC build
  time in its archive comment.
- `dist/freebsd-switch-15.1.img`: standalone MBR/FAT32 disk image.
- `dist/build-info.json`: source, toolchain and patch provenance.
- `dist/SHA256SUMS`: hashes of distribution files.
- `logs/qemu-uart.log`: QEMU serial transcript.

Prefer extracting the ZIP onto an existing FAT32 card. Preserve the existing
`bootloader/hekate_ipl.ini`; this bundle adds a separate More Configs entry at
`bootloader/ini/switchbsd.ini`. It does not include or replace a normal Hekate
installation. Back up any SD files with matching names before copying.

In your existing Hekate menu, open **More Configs → FreeBSD 15.1 experiment**.
The supplied dedicated payload automatically attempts the Coreboot handoff.
You can also use your established Erista payload-loading setup to load
`bootloader/payloads/hekate-switchbsd.bin` directly with the bundle on SD.
The project does not set up RCM entry or hardware modifications.

The `.img` is an alternative for a separate card; writing a raw image replaces
that card's partition table and contents. The build does not perform this step.

## Serial output and acceptance

See [UART and boot stages](docs/boot-test.md). UARTB uses 115200 baud, 8 data
bits, no parity, 1 stop bit, and no flow control. The final markers are:

```
SWITCHBSD: USERLAND_READY
Diagnostic shell on /dev/console; type exit to restart it.
```

`make smoke` boots a copy using QEMU's own UEFI and PL011 serial device, supplies
entropy and a virtual USB keyboard, and checks the shell, diagnostic tools,
report collection and a known SHA-256 result.
Set `QEMU_EFI` to override `/usr/local/share/qemu/edk2-aarch64-code.fd`, and
`QEMU_TIMEOUT` to override the 300-second timeout. QEMU does not test the Switch
firmware, Tegra drivers, physical UART wiring or the Hekate/Coreboot handoff.

The root filesystem is volatile. There is no persistent storage setup,
network configuration, Joy-Con input, audio, graphics acceleration or suspend.
Secondary CPU startup is disabled. The diagnostic kernel retains GENERIC
hardware support; it is not a size-optimized Switch-only kernel.

See [firmware integration](docs/firmware.md) and [licenses](docs/licenses.md).
