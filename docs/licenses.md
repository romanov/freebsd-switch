# Source and license provenance

The firmware and operating system combine several upstream projects. Their
individual copyright notices and license texts remain authoritative. The
bundle includes primary license files in `switchbsd/licenses/`; the complete
pinned source archives are retained under this project's `cache/` directory.

| Component | Primary notice in sources |
| --- | --- |
| NintendoSwitchPkg | `sources/switch/LICENSE` (GPL v2; inspect individual files for their notices) |
| Hekate | `sources/hekate/LICENSE` (GPL v2) |
| Coreboot | `sources/coreboot/COPYING` (GPL v2; components may have additional notices) |
| Switch ARM Trusted Firmware | `sources/coreboot/3rdparty/arm-trusted-firmware/license.rst` |
| EDK2 | `sources/edk2/License.txt` (BSD-2-Clause-Patent and listed third-party components) |
| FreeBSD | `sources/freebsd/COPYRIGHT` and individual source notices |

The lock file records original download URLs, revisions and archive SHA-256
values, including nested build dependencies. `patches/generated/` records
changes to upstream files. Keep these patches, project templates and source
archives with any binary redistribution; the upstream license files describe
the applicable terms. Toolchain packages are listed in `build-info.json`.

The codex root also contains binaries from FreeBSD aarch64 packages, under
their own licenses. Among them are codex (Apache-2.0), bash (GPL-3.0),
git-lite (GPL-2.0), ripgrep (MIT or Unlicense) and libcurl (curl), plus the
libraries they link. The license files of every package that supplies a file
are copied to `usr/local/share/licenses/` in the root and to
`switchbsd/licenses/packages/` in the bundle. `build-info.json`
(`target_packages`) records each package's version, ports origin and SHA-256.
The corresponding sources are the FreeBSD ports tree at those origins and the
distfiles it names.

New project Python scripts, configuration, tests and documentation are offered
under the BSD-2-Clause license below. Firmware templates carry their own SPDX notices: the Hekate/Coreboot
templates are GPL-2.0-only, the SD driver replacements are GPL-2.0-or-later,
and the diagnostic boot-manager code and status headers are BSD-2-Clause. The
USB host and I2C templates (`switchbsd-usb-host.c`, `switchbsd-i2c.c`) port
Hekate code and are GPL-2.0-only. The FreeBSD driver `switchbsd-ehci-acpi.c` and
the network script `switchbsd-net` are BSD-2-Clause. Generated patches retain the
licensing of the upstream files they modify.

The Wi-Fi and SSH update redistributes binaries with their own notices, which
are copied to `switchbsd/licenses/` in the SD bundle and the network update ZIP:

| Component | Notice in sources |
| --- | --- |
| Realtek `rtwn` firmware, compiled into the kernel (binary redistribution only; no reverse engineering) | `sources/freebsd/sys/contrib/dev/rtwn/LICENSE` |
| wpa_supplicant (BSD) | `sources/freebsd/contrib/wpa/COPYING` |
| OpenSSH | `sources/freebsd/crypto/openssh/LICENCE` |

Copyright (c) 2026, SwitchBSD experiment contributors
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
