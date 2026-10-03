# Diagnostic image, revision 1

This RAM-root update adds USB, device and storage inspection, SHA-256 checksums,
file tools and a single report command. It uses the existing build 8 firmware
and kernel. Keyboard input on build 8 has been confirmed by the user; this
new diagnostic image requires its own hardware check.

## Install on the working build 8 SD card

1. Back up `boot/rootfs.ufs` from the card.
2. Extract `dist/freebsd-switch-15.1-diagnostics-update.zip` at the SD root.
   It replaces `boot/rootfs.ufs` and adds this guide and the image build time.
3. Boot the usual FreeBSD entry with the keyboard connected.

The firmware, kernel and loader configuration remain the working build 8
versions. Restore the backed-up `boot/rootfs.ufs` to undo the update. A fresh
installation can use the full SD ZIP, which also includes the diagnostic image.

## Capture a report

At the shell prompt:

```sh
switchbsd-report > /tmp/report.txt
less /tmp/report.txt
```

Use arrow keys or Space to navigate; press `q` to leave the pager. Running
`switchbsd-report` without redirection prints directly to the console.

The report includes image build time and packaged kernel/firmware hashes,
CPU/memory/console settings, USB identities and attached drivers, the device
tree, storage, mounted filesystems, network interfaces, processes, boot
settings and the kernel message buffer. The system clock may be unset; use the
image build time to identify the build. Packaged hashes do not verify the
contents of the physical SD card.

Collection does not mount, format, benchmark, reset or write to any device.
Each probe is run through `timeout` with a 10-second deadline and a further
2-second kill grace. A failed or unavailable probe is recorded and collection
continues. Processes stuck in an uninterruptible kernel wait may outlast that
deadline. `DIAGNOSTIC_REPORT_END` means collection finished, not that every
device passed a test. The summary counts unsuccessful probes; the command
returns success for a completed report even if some probes failed.

Reports in `/tmp` are lost on reboot. To keep one, copy it to an already mounted
writable filesystem (for example `cp /tmp/report.txt /mnt/report.txt`) and run
`sync`. No persistent filesystem is mounted automatically. Boot settings and
device descriptors may contain identifying details; review before sharing.

## Useful commands

```sh
usbconfig list                  # USB addresses and devices
usbconfig show_ifdrv            # attached USB interface drivers
devinfo -rv                    # devices and allocated resources
camcontrol devlist -v           # CAM buses and storage devices
geom disk list                 # disk geometry and identity
sysctl kern.disks              # detected disk names
diskinfo -v /dev/md0            # RAM-root disk details
sha256 /tmp/report.txt         # checksum a saved report
dmesg | less                   # kernel messages
```

`diskinfo -v /dev/da0` can inspect a USB disk if that device actually appears.
Avoid benchmark/write flags when collecting diagnostics. USB storage on the
Switch is a separate hardware test; keyboard success alone does not establish
that it works.

The image also provides `cp`, `mkdir`, `rm`, `mv`, `chmod`, `ln`, `sync`, `date`,
`head`, `tail`, `sed`, `tee`, `less`, `vi`, `gpart` and `mount_msdosfs`, alongside
the existing rescue commands. All binaries are static AArch64 executables or
rescue applets; no shared-library runtime is needed. The root image remains
128 MiB and the shell still runs as root.

## Acceptance

- `make test`: host tests, including report continuation after failed probes.
- `make validate`: static ARM64 tools, command aliases, build identity, archive
  contents, filesystem and distribution checksums.
- `make smoke`: QEMU shell executes the report and diagnostic tools, including
  a SHA-256 known-answer check. QEMU does not validate Switch USB hardware.
- On the Switch: capture a report, check that the keyboard and controller are
  listed, page through the report and return to the shell.

Build 8's OTG power behavior is unchanged: after testing, unplug the adapter
and boot Hekate once to restore charging, as described in `USB-UPDATE.md`.
