# Codex root update

This update adds the OpenAI Codex CLI (FreeBSD package `misc/codex`) to a
second, larger RAM root, plus what it needs to work: bash, ripgrep, git with
HTTPS, the shared libraries they use, CA certificates, Wi-Fi/DHCP networking,
SSH and NTP time. The user confirmed the previous Codex build works on the
Switch; the combined Wi-Fi build still needs a physical dongle test.

It builds on the [USB keyboard update](USB-UPDATE.md) (build 8). Codex needs a
network connection, and on the Switch the only one is the USB-C port.

## What changed

- **Codex RAM root.** `boot/rootfs-codex.ufs.gz` is the diagnostic root plus
  the codex packages, the FreeBSD dynamic linker and libraries, and common
  command-line tools. Both roots now include the Wi-Fi/SSH and diagnostic tools.
- **Loader choice.** `boot/loader.conf.local` tells the loader to boot the codex
  root. The loader finds the `.gz` file and decompresses it while loading.
- **Kernel.** Drivers for common USB network adapters are now built in:
  RTL8152/8153 (`ure`, already present), AX88179 (`axge`), AX88772 (`axe`),
  CDC Ethernet/NCM (`cdce`), Android USB tethering (`urndis`) and iPhone
  tethering (`ipheth`), plus `rtwn` USB Wi-Fi and its firmware for the
  TP-Link TL-WN821N v5/v6 (RTL8192EU).
- **Startup.** Before the shell starts, the codex root:
  - mounts RAM space on `/tmp` and `/root`;
  - joins the configured Wi-Fi network and enables SSH when credentials are set;
  - asks for a DHCP address on every network adapter it finds;
  - sets the clock with NTP;
  - prints the codex version.

## What you need

- The build 8 USB setup from [USB-UPDATE.md](USB-UPDATE.md): a USB-C OTG
  adapter and no charger connected.
- A small **unpowered USB 2.0 hub**, because the keyboard and the network
  adapter both need a port.
- One of:
  - a TP-Link TL-WN821N **v5/v6** USB Wi-Fi adapter and a 2.4 GHz network;
  - a USB Ethernet adapter (Realtek RTL8152/8153 and ASIX AX88179/AX88772 are
    the most common) and a cable to your router;
  - an Android phone with **USB tethering** turned on after it is connected.
- A ChatGPT account with Codex access, or an OpenAI API key.

## Apply from your computer

1. Power off the Switch and connect its SD card to your computer.
2. Back up `switchbsd/coreboot.rom`, `bootloader/payloads/hekate-switchbsd.bin`,
   `boot/kernel/kernel`, `boot/rootfs.ufs`, `boot/rootfs-codex.ufs.gz` and
   `boot/loader.conf.local`.
3. Extract `freebsd-switch-15.1-codex-update.zip` at the SD card's root,
   replacing files with matching paths. Keep `boot/loader.conf`. The ZIP
   includes the build 8 and Wi-Fi/SSH files, so no separate update ZIP is needed.
   Existing `boot/loader.conf.d/network.conf` is preserved.
4. For Wi-Fi, copy `boot/loader.conf.d/network.conf.sample` to `network.conf`
   in the same directory and enter your SSID and password. To enable SSH,
   add an SSH key or password. See [the settings guide](NETWORK-UPDATE.md#2-create-the-settings-file).
   If you already have this file configured, keep it.
5. Connect the hub to the OTG adapter, and the keyboard and network adapter
   to the hub. Plug the adapter into the Switch **before** powering on.
6. Boot **More Configs → FreeBSD 15.1 experiment**.

Loading the Codex root reads a compressed image of roughly 120 MiB from the
SD card and unpacks several hundred MiB, so the loader spends longer on `/boot/rootfs-codex.ufs`
than on the old root.

## Expected FreeBSD output

After the USB lines, the codex root prints something like:

```
Network: wlan0 address 192.168.1.23
Clock: Sat Oct  3 18:20:41 UTC 2026 (NTP)
Codex: codex-cli 0.155.1
Codex quick start: codex login --device-auth, then cd ~/work and run codex.
```

- **Wi-Fi: no USB adapter found** means FreeBSD did not see a supported dongle.
  Check `usbconfig list`, then run `switchbsd-net restart` after replugging.
- **Network: no address yet** means no interface has an address. Check
  `switchbsd-net status`, your Wi-Fi settings or cable, then retry with
  `switchbsd-net restart`. On a phone, turn USB tethering on first.
- **NTP failed** means the clock may be wrong, and HTTPS then rejects every
  certificate. Once networking works, run `timeout 30 ntpd -qg -c /etc/ntp.conf`.

## Using codex

```sh
codex login --device-auth      # shows a URL and a code; finish on your phone
cd ~/work
git clone https://github.com/you/project.git
cd project
codex
```

- To log in with an API key instead, pipe it in:
  `printenv OPENAI_API_KEY | codex login --with-api-key`.
- `codex exec "…"` runs one task without the full-screen interface.
- FreeBSD has no codex sandbox, so codex asks before running each command.
  `codex --dangerously-bypass-approvals-and-sandbox` skips the approvals; use
  it only for throwaway work.
- **Everything is lost at power-off**, including the login, `~/work` and any
  changes. Push your work with git before shutting down.
- The screen console has 16 colours, so the interface looks plainer than in a
  desktop terminal.

## If something goes wrong

- **The loader stops while loading `rootfs-codex.ufs`, or FreeBSD panics
  early:** delete `boot/loader.conf.local` on the SD card. The Switch then boots
  the small diagnostic root as before; the rest of the update can stay.
- **HTTPS errors in codex or git:** check the `Clock:` line, then
  `dmesg | grep random`. The first connection can wait until the kernel's
  random number generator has gathered enough entropy.
- **To undo the update:** restore the files backed up above. To return to the
  diagnostic root instead, delete `boot/loader.conf.local`.

## Power notes

The build 8 [power notes](USB-UPDATE.md#power-notes) apply. The hub, keyboard
and network adapter all draw from the console's battery through the OTG boost
converter, and the console does not charge meanwhile.
