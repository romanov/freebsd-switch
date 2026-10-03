# Codex root update

This update adds the OpenAI Codex CLI (FreeBSD package `misc/codex`) to a
second, larger RAM root, plus what it needs to work: bash, ripgrep, git with
HTTPS, the shared libraries they use, CA certificates, DHCP networking and
NTP time. **It has not been tested on Switch hardware yet.**

It builds on the [USB keyboard update](USB-UPDATE.md) (build 8). Codex needs a
network connection, and on the Switch the only one is the USB-C port.

## What changed

- **Codex RAM root.** `boot/rootfs-codex.ufs.gz` is the diagnostic root plus
  the codex packages, the FreeBSD dynamic linker and libraries, and common
  command-line tools. The original `boot/rootfs.ufs` is unchanged.
- **Loader choice.** `boot/loader.conf.local` tells the loader to boot the codex
  root. The loader finds the `.gz` file and decompresses it while loading.
- **Kernel.** Drivers for common USB network adapters are now built in:
  RTL8152/8153 (`ure`, already present), AX88179 (`axge`), AX88772 (`axe`),
  CDC Ethernet/NCM (`cdce`), Android USB tethering (`urndis`) and iPhone
  tethering (`ipheth`).
- **Startup.** Before the shell starts, the codex root:
  - mounts RAM space on `/tmp` and `/root`;
  - asks for a DHCP address on every network adapter it finds;
  - sets the clock with NTP;
  - prints the codex version.

## What you need

- The build 8 USB setup from [USB-UPDATE.md](USB-UPDATE.md): a USB-C OTG
  adapter and no charger connected.
- A small **unpowered USB 2.0 hub**, because the keyboard and the network
  adapter both need a port.
- One of:
  - a USB Ethernet adapter (Realtek RTL8152/8153 and ASIX AX88179/AX88772 are
    the most common) and a cable to your router;
  - an Android phone with **USB tethering** turned on after it is connected.
- A ChatGPT account with Codex access, or an OpenAI API key.

## Apply from your computer

1. Power off the Switch and connect its SD card to your computer.
2. Back up `switchbsd/coreboot.rom`, `bootloader/payloads/hekate-switchbsd.bin`,
   `boot/kernel/kernel` and `boot/rootfs.ufs`.
3. Extract `freebsd-switch-15.1-codex-update.zip` at the SD card's root,
   replacing files with matching paths. Keep `boot/loader.conf`. The ZIP
   includes the build 8 files, so you don't need the USB update ZIP as well.
4. Connect the hub to the OTG adapter, and the keyboard and network adapter
   to the hub. Plug the adapter into the Switch **before** powering on.
5. Boot **More Configs → FreeBSD 15.1 experiment**.

Loading the codex root reads about 110 MiB from the SD card and unpacks it to
about 380 MiB of RAM, so the loader spends longer on `/boot/rootfs-codex.ufs`
than on the old root.

## Expected FreeBSD output

After the USB lines, the codex root prints something like:

```
Network: ue0 192.168.1.23
Clock: Sat Oct  3 18:20:41 UTC 2026 (NTP)
Codex: codex-cli 0.155.1
Codex quick start: codex login --device-auth, then cd ~/work and run codex.
```

- **Network: no USB Ethernet adapter or tethered phone found** means FreeBSD
  saw no network adapter. Check the hub, then run `switchbsd-net` again after
  replugging. On a phone, turn USB tethering on and run `switchbsd-net`.
- **Network: no DHCP lease** means the adapter is there but nothing answered
  within 15 s. Check the cable, then run `switchbsd-net`.
- **NTP failed** means the clock may be wrong, and HTTPS then rejects every
  certificate. Run `switchbsd-net` again once the network works.

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
- **To undo the update:** restore the four backed-up files and delete
  `boot/loader.conf.local` and `boot/rootfs-codex.ufs.gz`.

## Power notes

The build 8 [power notes](USB-UPDATE.md#power-notes) apply. The hub, keyboard
and network adapter all draw from the console's battery through the OTG boost
converter, and the console does not charge meanwhile.
