# UART capture and hardware test record

Hardware status: **FreeBSD userland and a root shell prompt are visible in the
Switch photo**, following build 3 and the screen console update. Physical input
remains unverified. See [the hardware boot record](hardware-boot-2026-10-03.md).
Record the complete log before a hang. A QEMU pass is only an OS-image test.

Use the UARTB connection exposed on the right Joy-Con interface, as described
by the [upstream EDK2 project](https://github.com/imbushuo/NintendoSwitchPkg).
Use a correctly wired, voltage-compatible serial adapter; do not connect an
RS-232-level port or the adapter's power output. Confirm the board pinout and
signal voltage for your hardware before connecting it. This project does not
provide a verified wiring diagram.

Configure the capture program for **115200 8N1, no flow control**. Start capture
before selecting the payload. On a FreeBSD capture host, `cu -l /dev/cuaU0 -s
115200` is an example; substitute the actual adapter device. Keep a transcript
with the distribution's SHA256SUMS, console revision and exact boot method.

| Stage | Expected evidence | If it stops here |
| --- | --- | --- |
| Dedicated Hekate | `hekate: Hello!` | Verify payload selection, power and UART capture. |
| SD/ROM handoff | `SWITCHBSD: HEKATE_HANDOFF` | Check FAT32 and the 10 MiB `switchbsd/coreboot.rom`. |
| Coreboot bootblock | `T210: Bootblock here` | Check relocation, CPU state and early clocks. |
| ROM verified | `SWITCHBSD: COREBOOT_DRAM_VERIFIED` | Check descriptor and DRAM preservation. |
| Coreboot romstage | `T210: romstage here` / `CPU prepare done` | Check carveouts, PMIC and CCPLEX startup. |
| Coreboot ramstage / ATF | Coreboot ramstage and BL31 diagnostics | Check payload addresses and EL3 handoff. |
| EDK2 | UEFI debug output / boot manager | Check firmware volume, SD access and EFI file discovery. |
| USB host (build 8) | `USB power: on`, `USB host: ready (Success)` | Record all USB lines; `switchbsd/usb-host-disable` skips this stage. |
| FreeBSD loader | ARM64 EFI loader, kernel and rootfs load | Check `/EFI/BOOT/BOOTAA64.EFI` and `/boot`. |
| FreeBSD kernel | FreeBSD banner, ACPI devices and UART console | Check MADT, GTDT, PSCI and UART parameters. |
| RAM root | `Trying to mount root from ufs:/dev/md0` | Check preloaded `mfs_root` and UFS support. |
| Userland | `SWITCHBSD: USERLAND_READY` | Check static init/rescue binaries and `/etc/rc`. |
| Interactive shell | `uname -a` produces output | Confirms serial input, scheduling and userland. |
| USB keyboard (build 8) | `ehci0` on acpi0, `hkbd0`; typed `echo USB_OK` prints `USB_OK` | Compare the firmware USB lines with the kernel's `ehci0`/`usbus0` messages. |

Build 3 restores SD pad power after Hekate handoff, automatically searches for
FreeBSD and displays SD initialization and command errors if it cannot launch
the loader; see `UPDATE.md`. Record the PMC register values and command details
alongside the stage/status lines. USB keyboards are not supported by the
firmware screens or the FreeBSD loader menu; build 8 enables one only in
FreeBSD (see `USB-UPDATE.md`). If the loader is waiting in its menu, the serial
terminal is the intended input.

The subsequent [screen console update](../CONSOLE-UPDATE.md) makes the screen
primary and mirrors kernel messages to UART. For the interactive serial-shell
test in this document, set `boot_serial="YES"` in `boot/loader.conf` on the SD
card first. Leaving it as `NO` sends userland `/dev/console` to the screen.
A display image does not prove physical UART or keyboard input. The shell has no
login prompt and runs as root in the RAM filesystem. To stop the experiment,
use the console's established power-off procedure if firmware reset does not
work; reset and power control are also unverified on this build.

## Capture template

- Date and hardware revision:
- Bundle SHA-256:
- Existing Hekate version / payload-loading method:
- UART adapter and configuration:
- Last confirmed stage:
- First error or last complete message:
- Interactive shell reached: yes / no
- Full transcript path:
