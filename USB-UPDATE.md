# USB keyboard update (build 8)

Build 8 adds a USB keyboard at the FreeBSD shell, using a USB-C OTG adapter
plugged into the console. **The user has confirmed keyboard input on hardware.**
The keyboard works only in FreeBSD, not on the firmware screen or in the
FreeBSD loader menu.

For additional inspection tools and a report command on this working build,
see [the diagnostic RAM-root update](docs/diagnostics.md).

## What changed

- **Firmware (build 8).** After the screen is up, the boot manager powers the
  USB-C port and starts the legacy USB1 (EHCI) controller in host mode:
  - It checks the USB-C controller (BM92T36) for an attached OTG device.
  - It switches the charger (BQ24193) to OTG boost mode for 5 V.
  - It brings up the UTMI PHY using Hekate's sequence, changed to host mode.
  Each step has a time limit, and a failure is shown on screen without
  stopping the boot. No UEFI USB driver is used: builds 4–6 used one and
  showed a black screen.
- **ACPI.** The firmware's DSDT describes the controller as `SWBS0001`. It is
  visible only when the firmware finished the bring-up, so FreeBSD never
  touches a controller that wasn't set up.
- **FreeBSD kernel.** A small EHCI driver for `SWBS0001` adds the Tegra
  details the generic driver lacks: host mode after a reset, the built-in
  transaction translator for low-speed keyboards, and the port-speed register.
  FreeBSD's own `hkbd`/`kbdmux` drivers then feed the keyboard to the screen
  console.
- **RAM root.** `/etc/rc` waits 3 s, then reports the USB controller and keyboard.

## What you need

- A USB-C OTG adapter (USB-C plug to USB-A socket) and a USB keyboard. A small
  unpowered USB 2.0 hub between them also works.
- No charger or computer connected to the Switch. The firmware refuses to
  supply 5 V while something else powers the port.

## Apply from your computer

1. Power off the Switch and connect its SD card to your computer.
2. Back up `switchbsd/coreboot.rom`, `bootloader/payloads/hekate-switchbsd.bin`,
   `boot/kernel/kernel` and `boot/rootfs.ufs`.
3. Extract `freebsd-switch-15.1-usb-update.zip` at the SD card's root,
   replacing files with matching paths. Keep `boot/loader.conf`.
4. Plug the keyboard into the adapter and the adapter into the Switch
   **before** powering on. The firmware only switches 5 V on at boot.
5. Boot **More Configs → FreeBSD 15.1 experiment**.

## Expected firmware screen

The banner reads **SwitchBSD SD diagnostic build 8 (USB host for FreeBSD)**.
After the SD lines, the USB lines stay on screen for 5 s; photograph them:

```
Starting USB host for FreeBSD...
USB power: on
USB-C PD 04b5/03b0 status .... .... alert ....
Charger part 2f control .. status c. fault 00
USB host: ready (Success)
Pad mux ........ -> ........; PHY ........; mode .......3
Port ........ (device connected, line state .); HOSTPC ........
Continuing in 5 seconds.
```

- **USB power: on** with a charger status starting with `c` means the charger
  is in OTG mode. The keyboard's lights may flash.
- **no OTG adapter or device detected**: the USB-C controller saw nothing.
  Check the adapter, or try a different keyboard or hub.
- **external power present**: a charger or computer is connected. Unplug it.
- **USB host** shows the last step reached. Anything other than `ready
  (Success)` names the step that timed out, and FreeBSD then boots without USB.
- **device connected** means the controller sees the keyboard. The line state
  tells low speed (1) and full speed (2) apart.

## Expected FreeBSD output

Kernel messages should include lines like these:

```
ehci0: <SwitchBSD Tegra210 EHCI controller> ... on acpi0
usbus0: EHCI version 1.0
usbus0: 480Mbps High Speed USB v2.0
uhub0: <Generic EHCI root HUB, ...> on usbus0
hkbd0: <...> on hidbus0
```

Before the shell starts, the RAM root prints the controller and keyboard it
found. At the `#` prompt, type `echo USB_OK` and press Enter. `USB_OK` on the
next line is the first proof of physical input. Please photograph it.

## If something goes wrong

- **The screen stops during the USB lines, or FreeBSD hangs or panics near
  `ehci0`:** on your computer, create an empty file named
  `switchbsd/usb-host-disable` on the SD card. The firmware then skips USB, the
  ACPI device stays hidden, and the boot is the same as build 7.
- **The firmware says ready, but FreeBSD finds no keyboard:** photograph the
  screen around the `ehci0`/`usbus0` lines. Try the keyboard behind a USB 2.0
  hub.
- **To undo the update:** restore the four backed-up files.

## Power notes

- While OTG mode is on, the console **does not charge**. The charger's
  watchdog is off, so it stays in OTG mode after FreeBSD stops, even with the
  console powered off.
- **After testing, unplug the adapter and boot Hekate once.** Every Hekate boot
  re-enables charging (`_check_low_battery()` in the pinned Hekate source).
  Booting Horizon also reprograms the charger.
- Don't leave the console powered off for long in OTG mode: the boost
  converter keeps running and slowly drains the battery.
