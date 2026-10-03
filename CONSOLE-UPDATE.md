# Screen console update (for build 7 firmware)

After the SD power fix, the user reports a FreeBSD boot screen followed by a
white rectangle. Whether that screen was the loader menu, kernel messages or
an actual login prompt has not yet been confirmed. This RAM root normally
prints `SWITCHBSD: USERLAND_READY` and starts a shell without a login prompt.

The subsequent `switch2.jpg` photo confirms that build 3 plus this configuration
reaches USERLAND_READY and a `#` prompt on the Switch display. Physical input
remains unverified. See [the hardware boot record](docs/hardware-boot-2026-10-03.md).

## Apply

1. Power off the Switch and connect the SD card to your computer.
2. Back up the card's `boot/loader.conf`.
3. Extract `freebsd-switch-15.1-console-update.zip` into the SD card's root,
   replacing `boot/loader.conf`.
4. Boot **More Configs → FreeBSD 15.1 experiment** again.

The firmware identifies itself as **SD diagnostic build 7 (USB host disabled)**. This update
changes the loader configuration, so no firmware or kernel replacement is needed.

## Expected result

Kernel messages should appear on the screen and UART. The screen becomes the
primary console, so the RAM-root startup messages and shell should appear there
if userland is reached. The screen may be sideways because the firmware exports
the panel's native portrait framebuffer. USB keyboard input is disabled in build
7 so the display recovery can be tested independently.

If it stops, capture the last visible text or the rectangle's position and size.
The change addresses a console-selection mismatch; it does not establish that
the kernel was already running or rule out a separate kernel/display failure.

## Cause found in source

The Switch firmware publishes its graphics device with a hardware vendor path.
FreeBSD's EFI loader recognizes ACPI display or PCI device paths when choosing
kernel consoles, but not this vendor graphics path. It does recognize the UART
path. That can set `boot_serial=YES` without enabling multiple kernel consoles,
even though the loader itself displays correctly through EFI graphics.

This update sets `boot_multicons=YES` and explicitly overrides the inherited
serial-primary flag with `boot_serial=NO`. `console=efi` and the Switch UART
address remain. With the screen primary, UART carries kernel diagnostics;
userland `/dev/console` uses the screen. For an interactive UART session, change
`boot_serial` to `YES` on the computer; kernel messages will still use both.

Restore the backed-up `boot/loader.conf` to undo this update. Do not reformat
the SD card.
