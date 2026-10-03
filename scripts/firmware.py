#!/usr/bin/env python3
"""Build the three pinned firmware stages; local changes live in prepare_firmware.py."""
import os
from pathlib import Path
import shutil
import sys
from build import ROOT, SRC, BUILD, JOBS, run, require, copy


def main():
    require(["gmake", "gcc14", "aarch64-none-elf-gcc", "arm-none-eabi-gcc", "iasl"])
    run([sys.executable, ROOT / "scripts/prepare_firmware.py"])
    hostbin = BUILD / "hostbin"
    hostbin.mkdir(exist_ok=True)
    for name, target in {"gcc": "gcc14", "g++": "g++14", "python": "python3.12",
                         "python3": "python3.12", "make": "gmake"}.items():
        link = hostbin / name
        link.unlink(missing_ok=True)
        link.symlink_to(shutil.which(target))
    env = dict(os.environ, PATH=str(hostbin) + ":" + os.environ["PATH"],
               PYTHON_COMMAND=sys.executable, GCC5_AARCH64_PREFIX="aarch64-none-elf-")
    edk = SRC / "edk2"
    link = edk / "NintendoSwitchPkg"
    if not link.exists():
        link.symlink_to("../switch")
    run(["gmake", "-C", edk / "BaseTools", f"-j{JOBS}",
         "EXTRA_OPTFLAGS=-Wno-error=vla-parameter -Wno-error=dangling-pointer"],
        env=env, log="edk2-basetools.log")
    run(["bash", "-c", ". ./edksetup.sh && build -a AARCH64 -b DEBUG -t GCC5 "
         "-p NintendoSwitchPkg/NintendoSwitch.dsc -n " + JOBS], cwd=edk, env=env, log="edk2.log")
    fw = BUILD / "firmware"
    fw.mkdir(exist_ok=True)
    fd = edk / "Build/NintendoSwitch-AARCH64/DEBUG_GCC5/FV/NINTENDOSWITCH.fd"
    if not fd.exists():
        candidates = list(fd.parent.glob("*.fd"))
        if len(candidates) != 1:
            raise RuntimeError("Expected one EDK2 firmware volume")
        fd = candidates[0]
    run(["aarch64-none-elf-objcopy", "-I", "binary", "-O", "elf64-littleaarch64",
         "--binary-architecture", "aarch64", fd, fw / "FD.o"])
    run(["aarch64-none-elf-ld", "-m", "aarch64elf", fw / "FD.o", "-T",
         SRC / "switch/Tools/FvWrapper.ld", "-o", fw / "UEFI.elf"])
    # Do not build Nyx or unrelated Hekate modules for the dedicated launcher.
    devkit = BUILD / "devkit"
    devkit.mkdir(exist_ok=True)
    # FreeBSD's cross GCC carries fixed host headers; prioritize target newlib.
    newlib = Path(shutil.which("arm-none-eabi-gcc")).resolve().parents[1] / "arm-none-eabi/include"
    if not (newlib / "stdlib.h").is_file():
        raise RuntimeError("Install arm-none-eabi-newlib before building Hekate")
    (devkit / "base_rules").write_text(f"CC := arm-none-eabi-gcc -isystem {newlib}\nCXX := arm-none-eabi-g++ -isystem {newlib}\n"
                                     "OBJCOPY := arm-none-eabi-objcopy\n")
    run(["gmake", f"-j{JOBS}", f"DEVKITARM={devkit}", "NYXDIR=", "MODULEDIRS=", "IPLECHO=true"],
        cwd=SRC / "hekate", env=env, log="hekate.log")
    copy(SRC / "hekate/output/hekate.bin", fw / "hekate-switchbsd.bin")
    core = SRC / "coreboot"
    env.update(CROSS_COMPILE_arm="arm-none-eabi-", CROSS_COMPILE_arm64="aarch64-none-elf-",
               CROSS_COMPILE_aarch64="aarch64-none-elf-")
    config = (ROOT / "config/coreboot.config").read_text().replace("@UEFI_ELF@", str(fw / "UEFI.elf"))
    (core / ".config").write_text(config)
    run(["gmake", "olddefconfig"], cwd=core, env=env, log="coreboot-config.log")
    run(["gmake", f"-j{JOBS}"], cwd=core, env=env, log="coreboot.log")
    copy(core / "build/coreboot.rom", fw / "coreboot.rom")
    run([sys.executable, ROOT / "scripts/validate.py", "--firmware"])


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        sys.exit(str(exc))
