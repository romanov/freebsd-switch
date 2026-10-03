# Wi-Fi and SSH update (diagnostic image revision 2)

This update connects the Switch to a Wi-Fi network through a **TP-Link
TL-WN821N v5/v6** USB adapter on the USB-C port, and starts an SSH server so
you can log in from a computer. It uses the build 8 firmware unchanged.
**Wi-Fi has not been tested on Switch hardware yet.** The QEMU test covers DHCP
and SSH on a virtual network card; QEMU cannot emulate a USB Wi-Fi adapter.

## What changed

- **Kernel.** The 802.11 stack, the `rtwn` Realtek USB Wi-Fi driver and its
  firmware are built in. The TL-WN821N v5/v6 uses the RTL8192EU chip. Other
  `rtwn` adapters (RTL8188CU/EU, RTL8192CU/EU, RTL8812AU, RTL8821AU) should also
  work, but are untested.
- **RAM root.**
  - `wpa_supplicant` joins the network, and `dhclient` gets an address.
  - OpenSSH `sshd` runs with `sftp`/`scp` support.
  - A new command, `switchbsd-net`, starts, stops and reports on these.
  - Files in the RAM root are now owned by root, as sshd requires.
- **Settings file.** Your network name, Wi-Fi password and SSH login go in
  `boot/loader.conf.d/network.conf` on the SD card. FreeBSD cannot read the SD
  card, so the FreeBSD loader passes these settings to the system at boot.
- **`boot/entropy`.** A random seed, new with every build, so the system
  doesn't stall waiting to gather randomness before it can start SSH.

## What you need

- Build 8 with a working USB keyboard ([USB keyboard update](USB-UPDATE.md)).
- A TL-WN821N **v5 or v6**. The version is on the label as "Ver: 5.x" or
  "Ver: 6.x"; on a computer its USB ID is 2357:0107. v4 (RTL8192CU) should also
  work. v1–v3 use Atheros chips that this build does not support.
- A **2.4 GHz** network using WPA2, WPA2/WPA3 mixed mode, or no password. This
  adapter can't use 5 GHz networks or WPA3-only networks.
- To keep the keyboard as well, a small USB 2.0 hub between the OTG adapter and
  the two devices. Without a keyboard, the Wi-Fi adapter can go straight into
  the OTG adapter.
- A computer on the same network with an SSH client. Windows 10 and 11 include
  `ssh` (use PowerShell); macOS and Linux have it in Terminal.

## 1. Apply the update

1. Power off the Switch and connect its SD card to your computer.
2. Back up `boot/kernel/kernel` and `boot/rootfs.ufs`.
3. Extract `freebsd-switch-15.1-network-update.zip` at the SD card's root,
   replacing files with matching paths. Keep `boot/loader.conf`.

## 2. Create the settings file

In `boot/loader.conf.d/` on the SD card, copy `network.conf.sample` to
`network.conf` and edit the copy. A complete example:

```
switchbsd.wifi.ssid="MyNetwork"
switchbsd.wifi.psk="my Wi-Fi password"
switchbsd.wifi.country="DE"
switchbsd.ssh.key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... you@pc"
switchbsd.ssh.password="a long password used nowhere else"
```

You need at least one of `switchbsd.ssh.key` and `switchbsd.ssh.password`, or
SSH won't start. Rules:

- The name must be exactly `network.conf`, in lower case. On Windows, turn on
  **View → File name extensions** first, so the file doesn't end up as
  `network.conf.txt`. In Notepad's Save dialog, choose **All files**. Windows
  line endings are fine.
- Put every value in double quotes. A value can't contain a double quote, and
  must be shorter than 128 characters.
- Put a backslash before any `$` or `\` in a value. For example, the password
  `pa$$word` is written `pa\$\$word`.
- `switchbsd.wifi.psk` must be 8 to 63 characters; leave the line out for an
  open network. `switchbsd.wifi.country` is optional: a two-letter code in
  capitals, needed for channels 12 and 13 outside North America.

**SSH key (recommended).** If you don't have one, run `ssh-keygen -t ed25519`
on your computer and press Enter at each question. Open the `.pub` file it
names (on Windows, `C:\Users\<you>\.ssh\id_ed25519.pub`) and copy its single
line into `switchbsd.ssh.key`. Only ed25519 keys are short enough. If the line
is 128 characters or longer, shorten the comment at its end. Add more keys as
`switchbsd.ssh.key2`, `switchbsd.ssh.key3` and so on.

**SSH password.** It's easier to start with, but it is stored in plain text
on the SD card, and anyone on the network can try to guess it. Choose a long
password you don't use anywhere else.

Updates never replace `network.conf`; they only ship the `.sample` file.

## 3. Boot

1. Plug the Wi-Fi adapter (and the hub and keyboard, if used) into the OTG
   adapter, and the OTG adapter into the Switch, **before** powering on. The
   firmware only switches 5 V on at boot.
2. Boot **More Configs → FreeBSD 15.1 experiment**.

Kernel messages should include a line like `rtwn0: <...> on usbus0`. Before the
shell starts, the screen should show something like:

```
USB Wi-Fi: <the adapter's USB name>
Wi-Fi: wlan0 on rtwn0
Wi-Fi: connecting.... associated.
DHCPREQUEST on wlan0 to 255.255.255.255 port 67
bound to 192.168.1.23 -- renewal in 43200 seconds.
SWITCHBSD: SSH_READY
Network: wlan0 address 192.168.1.23
SWITCHBSD: NETWORK_READY wlan0 192.168.1.23
SSH: ssh root@192.168.1.23
SSH host key: SHA256:... (ED25519)
```

It waits up to 15 s for the adapter, 30 s to join the network and 20 s for an
address. The shell starts afterwards either way. Please photograph this part of
the screen.

## 4. Connect

From your computer, run the command shown on screen:

```
ssh root@192.168.1.23
```

The first time, `ssh` shows the Switch's host key fingerprint. Check that it
matches the `SSH host key` line on the Switch screen, then type `yes`. The
fingerprint stays the same across reboots and rebuilds on the same build
machine. If it changes (for example after a build on another machine), remove
the old entry with `ssh-keygen -R 192.168.1.23` and connect again.

To copy a file from the Switch, for example a diagnostic report:

```
scp root@192.168.1.23:/tmp/report.txt .
```

## Commands on the Switch

```sh
switchbsd-net status    # adapter, Wi-Fi state, addresses, SSH server, key count
switchbsd-net scan      # list the Wi-Fi networks the adapter can see
switchbsd-net restart   # stop and start Wi-Fi, DHCP and SSH again
switchbsd-net stop      # stop Wi-Fi, DHCP and the SSH server
```

Settings are read once per boot. To change them, edit `network.conf` on the SD
card and reboot. For a one-off change, edit `/etc/wpa_supplicant.conf` with
`vi` and run `switchbsd-net restart`. Run `restart` and `stop` at the console:
over a Wi-Fi SSH session they disconnect you.

## If something goes wrong

- **`Wi-Fi: no USB adapter found`.** Check `usbconfig list` and
  `dmesg | less` for `rtwn0`. Plug everything in before powering on, and check
  the firmware screen's USB lines (see `USB-UPDATE.md`). An empty file
  `switchbsd/usb-host-disable` on the SD card turns USB off, so remove it.
- **`not associated`.** Run `switchbsd-net scan` and check that your network is
  listed. Check the network name and password, including upper and lower case.
  For channels 12 or 13, set `switchbsd.wifi.country`. 5 GHz-only and
  WPA3-only networks can't be used.
- **`Network: no address yet`.** The router didn't answer DHCP. Run
  `switchbsd-net restart` to retry.
- **`SSH: not started` or `Wi-Fi: not configured`.** The settings didn't
  arrive. Check the file name and the quotes. The loader prints `Malformed line`
  or `Failed to parse variable` for a bad line. A value that is too long is
  dropped; `dmesg` then shows `WARNING: too long kenv string`.
- **To undo the update,** restore the two backed-up files and delete
  `boot/loader.conf.d/network.conf`.

## Security notes

- The SSH server accepts root logins from any device that can reach the
  Switch. Use it only on networks you trust.
- The Wi-Fi password and the SSH password sit in plain text on the SD card.
  At boot, FreeBSD removes both from the kernel environment once they are
  applied. The Wi-Fi password stays in `/etc/wpa_supplicant.conf`, which only
  root can read, until the next reboot.
- The Switch's private SSH host key is inside the RAM root of every ZIP and
  image your build produces. Anyone with those files could pretend to be your
  Switch, so don't share them. Delete `build/ssh` on the build machine to get a
  new key with the next build.
- `boot/entropy` seeds the random number generator. The same file is used at
  every boot until the next build, mixed with randomness gathered during
  startup.

## Power notes

The power behaviour is the same as for the keyboard (see `USB-UPDATE.md`). The
console **does not charge** while it powers the USB-C port. A Wi-Fi adapter
draws more current than a keyboard, so the battery drains faster. After
testing, unplug the adapter and boot Hekate once to restore charging.
