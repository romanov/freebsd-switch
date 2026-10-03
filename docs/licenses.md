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

New project Python scripts, configuration, tests and documentation are offered
under the BSD-2-Clause license below. Firmware templates carry their own SPDX notices: the Hekate/Coreboot
templates are GPL-2.0-only, the SD driver replacements are GPL-2.0-or-later,
and the diagnostic boot-manager code and status header are BSD-2-Clause. Generated
patches retain the licensing of the upstream files they modify.

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
