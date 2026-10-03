#!/usr/bin/env python3
"""Explicit, idempotent adaptations of pinned upstream trees.

Every changed upstream file gets a reviewable diff in patches/generated/.
Do not silently accept source drift: each replacement has one expected anchor.
"""
from pathlib import Path
import difflib
from build import ROOT, SRC


def replace(relative, before, after):
    path = SRC / relative
    text = path.read_text()
    if after in text:
        return
    if text.count(before) != 1:
        raise RuntimeError(f"Source drift: {relative}: expected one patch anchor")
    write(relative, text.replace(before, after))


def write(relative, content):
    path = SRC / relative
    old = path.read_text() if path.exists() else ""
    if old == content:
        return
    original = ROOT / "cache/originals" / relative
    if not original.exists():
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_text(old)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    patch = ROOT / "patches/generated" / (relative.replace("/", "_") + ".patch")
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text("".join(difflib.unified_diff(original.read_text().splitlines(True),
                       content.splitlines(True), "a/" + relative, "b/" + relative)))


def main():
    (SRC / "switch/Include/FwReleaseInfo.h").write_text(
        '#define __IMPL_COMMIT_ID__ "09c3ade4-switchbsd"\n'
        '#define __RELEASE_DATE__ "10/03/2026"\n')
    replace("hekate/bdk/utils/types.h", "#include <assert.h>",
            "#include <assert.h>\n#include <stdint.h>")
    replace("hekate/Makefile", "\n#CUSTOMDEFINES += -DDEBUG_UART_BAUDRATE=115200 -DDEBUG_UART_INVERT=0 -DDEBUG_UART_PORT=1",
            "\nCUSTOMDEFINES += -DDEBUG_UART_BAUDRATE=115200 -DDEBUG_UART_INVERT=0 -DDEBUG_UART_PORT=1")
    replace("hekate/loader/Makefile", "$(OBJS): $(BUILDDIR)",
            "$(OBJS): $(BUILDDIR)\n$(BUILDDIR)/loader.o: payload_00.h payload_01.h")
    main_path = SRC / "hekate/bootloader/main.c"
    main_text = main_path.read_text()
    start = main_text.find("\t// Check if watchdog was fired previously.")
    end = main_text.find("skip_lp0_minerva_config:")
    if start >= 0 and end > start:
        write("hekate/bootloader/main.c", main_text[:start] +
              "\t/* Keep Hekate's initial DRAM rate; no periodic training or external modules. */\n"
              "\twatchdog_end();\n" + main_text[end + len("skip_lp0_minerva_config:"):])
    replace("switch/NintendoSwitch.dsc", "[LibraryClasses.common]",
            "[LibraryClasses.common]\n  RegisterFilterLib|MdePkg/Library/RegisterFilterLibNull/RegisterFilterLibNull.inf")
    prepare_sd_diagnostics()
    prepare_usb_host()
    # The payload wrapper is kept within Coreboot's 28 KiB bootblock allocation.
    hook = (ROOT / "config/hekate-handoff.c").read_text()
    # Replace the generated section as a unit so edited templates and reruns
    # cannot accumulate multiple copies of the launch function.
    text = main_path.read_text()
    anchor = "static void _launch_payload(char *path, bool update, bool clear_screen)"
    end = text.index(anchor)
    start = text.find("/* SwitchBSD experiment.")
    if start < 0:
        start = end
    write("hekate/bootloader/main.c", text[:start] + hook + "\n" + text[end:])
    replace("hekate/bootloader/main.c", "\t\t_check_for_updated_bootloader();\n\t\t_auto_launch();",
            "\t\t/* Dedicated experiment: never auto-update or enter a HOS boot path. */\n"
            "\t\t_switchbsd_launch();\n\t\twhile (1) msleep(1000);")
    replace("hekate/bootloader/main.c",
            "\t// Failed to launch Nyx, unmount SD Card.\n\tsd_end();\n\n"
            "\t// Set ram to a freq that doesn't need periodic training.\n\tminerva_change_freq(FREQ_800);\n\n"
            "\twhile (true)\n\t\ttui_do_menu(&menu_top);",
            "\tEPRINTF(\"SwitchBSD: SD mount failed.\");\n\tsd_end();")
    # SDRAM and its clock were initialized by Hekate. Retain both across handoff.
    replace("coreboot/src/soc/nvidia/tegra210/bootblock.c", "\tmbist_workaround();",
            "\t/* Hekate has already performed MBIST and initialized SDRAM. */")
    replace("coreboot/src/mainboard/nintendo/switch/cbfs_usb.c",
            (SRC / "coreboot/src/mainboard/nintendo/switch/cbfs_usb.c").read_text(),
            (ROOT / "config/coreboot-dram.c").read_text())
    # Clock and memory training are supplied by Hekate; no external MTC blob.
    replace("coreboot/src/soc/nvidia/tegra210/ramstage.c", "\tif (tegra210_run_mtc() != 0)",
            "\tif (IS_ENABLED(CONFIG_HAVE_MTC) && tegra210_run_mtc() != 0)")
    # GNU make 4.4 rejects the historical whitespace-named variable.
    replace("coreboot/Makefile.inc", "spc :=\nspc +=\n$(spc) :=\n$(spc) +=",
            "empty :=\nspc := $(empty) $(empty)")
    replace("coreboot/Makefile.inc", "$(subst $( ),/", "$(subst $(spc),/")
    replace("coreboot/Makefile.inc", "LDFLAGS_common := --gc-sections -nostdlib -nostartfiles -static --emit-relocs",
            "LDFLAGS_common := --gc-sections -nostdlib -static --emit-relocs")
    replace("coreboot/src/Kconfig", "config WARNINGS_ARE_ERRORS\n\tbool\n",
            'config WARNINGS_ARE_ERRORS\n\tbool "Treat compiler warnings as errors"\n')
    replace("coreboot/util/sconfig/sconfig.h", "\nstruct device *head;",
            "\nextern struct device *head;")
    replace("coreboot/util/sconfig/Makefile.inc", "SCONFIGFLAGS +=",
            "$(addprefix $(objutil)/sconfig/,$(sconfigobj)): util/sconfig/sconfig.h\n\nSCONFIGFLAGS +=")
    replace("coreboot/src/arch/arm64/Makefile.inc",
            "BL31_CFLAGS := -fno-pic", "BL31_CFLAGS := -Wno-error=array-bounds -fno-pic")
    replace("coreboot/src/arch/arm64/Makefile.inc",
            "BL31_LDFLAGS := --emit-relocs", "BL31_LDFLAGS := --emit-relocs --no-warn-rwx-segments")
    path = "coreboot/src/arch/arm64/Makefile.inc"
    lines = (SRC / path).read_text().splitlines(True)
    write(path, "".join('BL31_MAKEARGS += BUILD_MESSAGE_TIMESTAMP=\'"SwitchBSD pinned source build"\'\n'
                       if line.startswith("BL31_MAKEARGS += BUILD_MESSAGE_TIMESTAMP=") else line
                       for line in lines))
    # Modern Python removed tostring(), which the old EDK2 build scripts used.
    p = SRC / "edk2/BaseTools/Source/Python"
    for file in p.rglob("*.py"):
        old = file.read_text()
        new = old.replace(".tostring()", ".tobytes()")
        if old != new:
            write(str(file.relative_to(SRC)), new)


def prepare_sd_diagnostics():
    # Install a stable interface pointer, not a by-value varargs structure.
    replace("switch/Drivers/PinMuxDxe/PinMux.c", "\n        mPinMuxProtocol,",
            "\n        &mPinMuxProtocol,")
    write("switch/Include/Protocol/SwitchBsdSdStatus.h",
          (ROOT / "config/switchbsd-sd-status.h").read_text())
    write("switch/Library/PlatformBootManagerLib/SwitchBsdAutoBoot.c",
          (ROOT / "config/switchbsd-autoboot.c").read_text())
    replace("switch/Library/PlatformBootManagerLib/PlatformBootManagerLib.inf",
            "[Sources]\n  PlatformBm.c", "[Sources]\n  PlatformBm.c\n  SwitchBsdAutoBoot.c")
    replace("switch/Library/PlatformBootManagerLib/PlatformBootManagerLib.inf",
            "  ShellPkg/ShellPkg.dec\n", "  ShellPkg/ShellPkg.dec\n  NintendoSwitchPkg/NintendoSwitch.dec\n")
    replace("switch/Library/PlatformBootManagerLib/PlatformBootManagerLib.inf",
            "[Protocols]\n", "[Protocols]\n  gEfiBlockIoProtocolGuid\n")
    replace("switch/Library/PlatformBootManagerLib/PlatformBootManagerLib.inf",
            "  gEfiBlockIoProtocolGuid\n",
            "  gEfiBlockIoProtocolGuid\n  gTegraPinMuxProtocolGuid\n"
            "  gTegra210ClockManagementProtocolGuid\n  gTegraUBootClockManagementProtocolGuid\n"
            "  gPmicProtocolGuid\n")
    replace("switch/Library/PlatformBootManagerLib/PlatformBm.c", '#include "PlatformBm.h"',
            '#include "PlatformBm.h"\n\nVOID SwitchBsdAutoBoot(VOID);')
    replace("switch/Library/PlatformBootManagerLib/PlatformBm.c",
            "  EfiBootManagerConnectAll ();", "  EfiBootManagerConnectAll ();\n  SwitchBsdAutoBoot();")
    path = "switch/Drivers/SdMmcDxe/SdMmc.c"
    replace(path, '#include "Include/EfiProto.h"',
            '#include "Include/EfiProto.h"\n#include <Library/MemoryAllocationLib.h>\n'
            '#include <Protocol/SwitchBsdSdStatus.h>')
    write("switch/Drivers/SdMmcDxe/SwitchBsdSdPlatform.h",
          (ROOT / "config/switchbsd-sd-platform.h").read_text())
    replace(path, "struct blk_desc mBlkDesc;",
            'struct blk_desc mBlkDesc;\n\n#include "SwitchBsdSdPlatform.h"')
    replace(path,
            "    // power-gpios = <&gpio TEGRA_GPIO(E, 4) GPIO_ACTIVE_HIGH>;\n"
            "\tgpio_config(GPIO_PORT_E, GPIO_PIN_4, GPIO_MODE_GPIO);\n"
            "\tgpio_write(GPIO_PORT_E, GPIO_PIN_4, GPIO_HIGH);\n"
            "\tgpio_output_enable(GPIO_PORT_E, GPIO_PIN_4, GPIO_OUTPUT_ENABLE);",
            "    SwitchBsdSdPowerOn();")
    replace(path,
            "\tret = tegra_mmc_send_cmd_bounced(priv, cmd, data, &bbstate);",
            "\tif (mSwitchSdDiag != NULL) mSwitchSdDiag->CommandInterrupt = 0;\n"
            "\tret = tegra_mmc_send_cmd_bounced(priv, cmd, data, &bbstate);\n"
            "\tSwitchBsdSdCommandDone(priv, cmd, ret);")
    replace(path,
            "\t\tmask = readl(&priv->reg->norintsts);\n\t\t/* Command Complete */",
            "\t\tmask = readl(&priv->reg->norintsts);\n"
            "\t\tif (mSwitchSdDiag != NULL) mSwitchSdDiag->CommandInterrupt = mask;\n"
            "\t\t/* Command Complete */")
    text = (SRC / path).read_text()
    start = text.index("EFI_STATUS\nEFIAPI\nSdMmcDxeInitialize")
    # This is the last function in the pinned source file. Keep the prefix
    # stable when updating the generated replacement on subsequent builds.
    marker = text.find("/* SPDX-License-Identifier: GPL-2.0-or-later\n * Replacement entry point")
    if marker >= 0:
        start = marker
    write(path, text[:start] + (ROOT / "config/switchbsd-sd-init.c").read_text())
    replace("switch/Drivers/SdMmcDxe/SdMmcDxe.inf", "[LibraryClasses]\n",
            "[LibraryClasses]\n  MemoryAllocationLib\n")
    path = "switch/Drivers/SdMmcDxe/EfiBlkDeviceOp.c"
    text = (SRC / path).read_text()
    start = text.index("EFI_STATUS\nEFIAPI\nMMCHSReadBlocks")
    marker = text.rfind("/* SPDX-License-Identifier: GPL-2.0-or-later */", 0, start)
    if marker >= 0:
        start = marker
    end = text.index("EFI_STATUS\nEFIAPI\nMMCHSWriteBlocks", start)
    write(path, text[:start] + (ROOT / "config/switchbsd-readblocks.c").read_text() + "\n" + text[end:])


def prepare_usb_host():
    # The pinned EHCI port is experimental and can stop EDK2 before the
    # graphics console appears on this hardware. Restore the original source
    # lists for the known-good display recovery build: no UEFI USB driver
    # binds or resets the controller.
    for relative in ("switch/NintendoSwitch.dsc", "switch/NintendoSwitch.fdf"):
        original = ROOT / "cache/originals" / relative
        if original.exists():
            write(relative, original.read_text())
    # Keep the unrelated EDK2 library-class adaptation made by main().
    replace("switch/NintendoSwitch.dsc", "[LibraryClasses.common]",
            "[LibraryClasses.common]\n  RegisterFilterLib|MdePkg/Library/RegisterFilterLibNull/RegisterFilterLibNull.inf")
    # Instead, the boot manager powers the USB-C port and starts USB1 for
    # FreeBSD after the display is up. Runs after prepare_sd_diagnostics().
    library = "switch/Library/PlatformBootManagerLib/"
    for name, template in (("SwitchBsdUsbHost.h", "switchbsd-usb-host.h"),
                           ("SwitchBsdUsbHost.c", "switchbsd-usb-host.c"),
                           ("SwitchBsdI2c.c", "switchbsd-i2c.c")):
        write(library + name, (ROOT / "config" / template).read_text())
    replace(library + "PlatformBootManagerLib.inf", "  SwitchBsdAutoBoot.c\n",
            "  SwitchBsdAutoBoot.c\n  SwitchBsdUsbHost.h\n  SwitchBsdUsbHost.c\n  SwitchBsdI2c.c\n")
    replace(library + "PlatformBootManagerLib.inf", "[LibraryClasses]\n  BaseLib\n",
            "[LibraryClasses]\n  BaseLib\n  IoLib\n  TimerLib\n")
    # Build the DSDT from the pristine table so reruns leave it untouched.
    dsdt = "switch/AcpiTables/Dsdt/Dsdt.asl"
    original = ROOT / "cache/originals" / dsdt
    text = original.read_text() if original.exists() else (SRC / dsdt).read_text()
    anchor = "    } // Scope(_SB)"
    if text.count(anchor) != 1:
        raise RuntimeError(f"Source drift: {dsdt}: expected one patch anchor")
    write(dsdt, text.replace(anchor, (ROOT / "config/switchbsd-usb.asl").read_text() + anchor))


if __name__ == "__main__":
    main()
