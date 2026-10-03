# USB-stick root: a persistent FreeBSD that updates itself

The RAM roots lose every change at power-off, because FreeBSD cannot write the
Switch's SD card. This update puts `/` on a USB stick instead. Files, SSH keys,
`~/.codex` and installed packages survive reboots. The FreeBSD base system
updates with `pkg upgrade` from packages your build host makes from the same
source as the kernel.

The firmware still cannot read USB. The loader, the kernel and the small
diagnostic RAM root therefore stay on the SD card. When the stick is missing
or cannot be mounted, the Switch boots that RAM root as before.

```
SD card:   loader → kernel (+ diagnostic RAM root as fallback)
USB stick: /  (FreeBSD base packages, pkg, Codex, bash, ripgrep, git)
```

## What you need

- A USB stick of 8 GB or more. Writing the image erases the stick.
- A USB-C OTG hub for the Switch's single port, so the stick, keyboard and
  Wi-Fi adapter fit together. A powered hub is safest: the Switch may not
  supply enough current for all three.
- The build 8 firmware and an SD card that already boots the diagnostic root.

## Write the stick

On the build host, `make pkgbase image` produces
`dist/freebsd-switch-15.1-usbroot.img.gz`.

- **Windows:** write the `.img.gz` file with Rufus or balenaEtcher. Both
  decompress it themselves.
- **FreeBSD:** check the stick's device name with `geom disk list`, then
  `gunzip -c dist/freebsd-switch-15.1-usbroot.img.gz | dd of=/dev/daN bs=1m`.
  Make sure `daN` is the stick, not a disk you need.

The build never writes a device; you choose the stick.

## Switch the SD card to the stick

1. Power off the Switch and connect its SD card to your computer.
2. Extract `dist/freebsd-switch-15.1-usbroot-update.zip` into the card's root,
   replacing matching files. It contains:
   - `boot/loader.conf.local`, which selects the stick and replaces the Codex
     RAM-root selection;
   - the kernel from the same build as the stick's packages;
   - the diagnostic RAM root, used as the fallback.
   `boot/loader.conf.d/network.conf` keeps your Wi-Fi and SSH settings; the
   stick reads them from there at every boot.
3. Plug the hub and stick into the Switch and boot **More Configs → FreeBSD
   15.1 experiment**.

The first boot grows the file system to fill the stick. The screen then
shows `SWITCHBSD: USERLAND_READY`, the network lines and a root shell. Check
the root with:

```sh
mount -p | head -n 1
```

It should name `/dev/gpt/switchroot` mounted `rw`.

## Update

On the build host:

```sh
make pkgbase
make serve
```

`make serve` serves the package repository on port 8080 of every interface
until you press Ctrl-C. `PKGBASE_PORT` and `PKGBASE_BIND` change the port and
address.

On the Switch, the first time:

```sh
switchbsd-update --repo http://<build host address>:8080
```

After that, plain `switchbsd-update` is enough. `switchbsd-update -n` lists
the changes without installing anything. The base system comes only from your
build host, signed with the key in `build/pkgbase/signing.key`. Codex and the
other tools come from FreeBSD's official package repository. Setting
`SWITCHBSD_PKG_URL` when building writes the repository address into the
image, so the `--repo` step is not needed.

The pkgbase versions change only when the FreeBSD source in
`sources.lock.json` changes. Rebuilding the same source offers no upgrade.

### The kernel rule

pkg cannot reach the SD card, so it never updates the kernel. When a new
source revision reaches the stick, copy `boot/kernel/kernel` from that
build's `freebsd-switch-15.1-usbroot-update.zip` to the SD card.
`switchbsd-update` warns when the base system and the running kernel
versions differ. The kernel has no loadable modules, so there is nothing else
to keep in step.

## Going back

- **Unplug the stick:** the kernel waits 20 seconds for it, then boots the
  diagnostic RAM root.
- **Delete `boot/loader.conf.local`:** the SD card boots the diagnostic RAM
  root without waiting.
- **Codex RAM root:** extract `freebsd-switch-15.1-codex-update.zip` again.

## Cautions

- Do not unplug the stick or the hub while FreeBSD is running: it holds `/`.
  Run `shutdown -p now` before removing power.
- A flat battery or a forced power-off leaves the file system unclean. The
  next boot checks and repairs it before mounting it read-write.
- The Switch has no clock FreeBSD can read. The stick saves the time at
  shutdown and after the boot-time NTP sync, and the clock never starts
  earlier than that.
- SSH keys from `network.conf` go into `/root/.ssh/authorized_keys.switchbsd`
  at every boot. Keys you add by hand belong in `/root/.ssh/authorized_keys`,
  which nothing overwrites.
- Wi-Fi through a hub is untested on the Switch.

## What QEMU tests

`make smoke` boots the stick image in QEMU three times. The first boot checks
the stick root, growfs, networking, SSH, Codex, pkg and `switchbsd-update -n`
against a local repository server. The second boot, after a clean shutdown,
checks that a file written on the first boot is still there. The third boot,
without the stick, checks the RAM-root fallback. QEMU does not test the
Switch's USB host controller, hubs or power.
