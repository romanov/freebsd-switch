"""Compile the real firmware C templates against small host-side protocol mocks."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SdFirmwareTests(unittest.TestCase):
    def compile_and_run(self, name, prelude, tests):
        compiler = shutil.which("cc")
        self.assertIsNotNone(compiler, "Host C compiler required for firmware regression tests")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "test.c"
            binary = Path(directory) / "test"
            source.write_text(prelude + '\n' + (ROOT / "config" / name).read_text() + '\n' + tests)
            result = subprocess.run([compiler, "-std=c11", "-Wall", "-Werror",
                                     "-Wno-unused-parameter", str(source), "-o", str(binary)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_sd_pad_power_recovery_and_command_diagnostics(self):
        prelude = r'''
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#define STATIC static
#define VOID void
typedef uint64_t EFI_STATUS, UINT64;
typedef uint32_t UINT32;
typedef int32_t INT32;
#define PMC_BASE 0
#define APBDEV_PMC_NO_IOPOWER 0x44
#define APBDEV_PMC_PWR_DET_VAL 0xe4
#define GPIO_PORT_E 4
#define GPIO_PIN_4 16
#define GPIO_MODE_GPIO 1
#define GPIO_HIGH 1
#define GPIO_OUTPUT_ENABLE 1
static UINT32 registers[64];
static unsigned power_step, delay;
static UINT32 MmioRead32(unsigned address) { return registers[address / 4]; }
static UINT32 MmioOr32(unsigned address, UINT32 mask) {
    assert(address == APBDEV_PMC_PWR_DET_VAL && power_step++ == 0);
    return registers[address / 4] |= mask;
}
static UINT32 MmioAnd32(unsigned address, UINT32 mask) {
    assert(address == APBDEV_PMC_NO_IOPOWER && power_step++ == 1);
    assert(registers[APBDEV_PMC_PWR_DET_VAL / 4] & (1u << 12));
    return registers[address / 4] &= mask;
}
static void gpio_config(unsigned port, unsigned pin, unsigned mode) {
    assert(port == GPIO_PORT_E && pin == GPIO_PIN_4 && mode == GPIO_MODE_GPIO);
    assert(power_step++ == 2);
}
static void gpio_write(unsigned port, unsigned pin, unsigned level) {
    assert(port == GPIO_PORT_E && pin == GPIO_PIN_4 && level == GPIO_HIGH);
    assert(power_step++ == 3);
}
static void gpio_output_enable(unsigned port, unsigned pin, unsigned enable) {
    assert(port == GPIO_PORT_E && pin == GPIO_PIN_4 && enable == GPIO_OUTPUT_ENABLE);
    assert(power_step++ == 4);
}
static void udelay(unsigned us) { assert(power_step++ == 5); delay = us; }
struct registers { UINT32 rspreg0, prnsts; uint16_t clkcon; uint8_t pwrcon; };
struct tegra_mmc_priv { struct registers *reg; };
struct mmc_cmd { UINT32 cmdidx, cmdarg; };
#define readl(p) (*(p))
#define readw(p) (*(p))
#define readb(p) (*(p))
'''
        prelude += (ROOT / "config/switchbsd-sd-status.h").read_text()
        self.compile_and_run("switchbsd-sd-platform.h", prelude, r'''
int main(void) {
    SWITCHBSD_SD_STATUS diag = {0};
    mSwitchSdDiag = &diag;
    for (unsigned gated = 0; gated <= 1; ++gated) {
        UINT32 before = 0xa5000080 | (gated << 12);
        registers[APBDEV_PMC_NO_IOPOWER / 4] = before;
        registers[APBDEV_PMC_PWR_DET_VAL / 4] = 0x12348080;
        power_step = delay = 0;
        SwitchBsdSdPowerOn();
        assert(power_step == 6 && delay >= 10000);
        assert(registers[APBDEV_PMC_NO_IOPOWER / 4] == (before & ~(1u << 12)));
        assert(registers[APBDEV_PMC_PWR_DET_VAL / 4] == 0x12349080);
        assert(diag.NoIoPowerBefore == before && diag.NoIoPowerAfter == 0xa5000080);
        assert(diag.PowerDetect == 0x12349080);
    }
    struct registers hardware = {0x1aa, 0xff8000, 0x107, 0xf};
    struct tegra_mmc_priv priv = {&hardware};
    struct mmc_cmd cmd = {8, 0x1aa};
    SwitchBsdSdCommandDone(&priv, &cmd, 0);
    assert(diag.CommandCount == 1 && diag.FirstErrorResult == 0);
    cmd.cmdidx = 55; cmd.cmdarg = 0;
    SwitchBsdSdCommandDone(&priv, &cmd, -110);
    cmd.cmdidx = 1;
    SwitchBsdSdCommandDone(&priv, &cmd, -1);
    assert(diag.FirstErrorCommand == 55 && diag.FirstErrorResult == -110);
    assert(diag.CommandCount == 3 && diag.LastCommand == 1);
    assert(diag.LastArgument == 0 && diag.LastCommandResult == -1);
    assert(diag.LastResponse == 0x1aa && diag.PresentState == 0xff8000);
    assert(diag.ClockControl == 0x107 && diag.PowerControl == 0xf);
    mSwitchSdDiag = NULL;
    SwitchBsdSdCommandDone(&priv, &cmd, -1);
    assert(diag.CommandCount == 3);
    return 0;
}
''')

    def test_block_reads_boundaries_alignment_and_errors(self):
        self.compile_and_run("switchbsd-readblocks.c", r'''
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#define IN
#define OUT
#define EFIAPI
#define VOID void
typedef uint64_t EFI_STATUS, EFI_LBA, UINT64;
typedef size_t UINTN;
typedef uint32_t UINT32;
#define MAX_UINT32 UINT32_MAX
#define MAX_UINT64 UINT64_MAX
enum { EFI_SUCCESS, EFI_NO_MEDIA, EFI_MEDIA_CHANGED, EFI_DEVICE_ERROR,
       EFI_BAD_BUFFER_SIZE, EFI_INVALID_PARAMETER };
typedef struct { int unused; } EFI_BLOCK_IO_PROTOCOL;
typedef struct { int MediaPresent; UINT32 MediaId, BlockSize, IoAlign;
                 EFI_LBA LastBlock; } EFI_BLOCK_IO_MEDIA;
typedef struct { EFI_BLOCK_IO_MEDIA BlockMedia; } BIO_INSTANCE;
static BIO_INSTANCE instance;
#define BIO_INSTANCE_FROM_BLOCKIO_THIS(This) (&instance)
static unsigned calls;
static UINT64 address;
static UINT32 length;
static int read_result = 1;
static int MmcReadInternal(BIO_INSTANCE *i, UINT64 a, void *b, UINT32 n) {
    ++calls; address = a; length = n; return read_result;
}
''', r'''
int main(void) {
    _Alignas(16) unsigned char buffer[1024];
    instance.BlockMedia = (EFI_BLOCK_IO_MEDIA){1, 42, 512, 4, 99};
    assert(MMCHSReadBlocks(NULL, 42, 99, 512, buffer) == EFI_SUCCESS);
    assert(calls == 1 && address == 99 * 512 && length == 512);
    assert(MMCHSReadBlocks(NULL, 42, 99, 1024, buffer) == EFI_INVALID_PARAMETER);
    assert(MMCHSReadBlocks(NULL, 42, 100, 512, buffer) == EFI_INVALID_PARAMETER);
    assert(MMCHSReadBlocks(NULL, 42, 0, 513, buffer) == EFI_BAD_BUFFER_SIZE);
    assert(MMCHSReadBlocks(NULL, 42, 0, 512, buffer + 1) == EFI_INVALID_PARAMETER);
    assert(MMCHSReadBlocks(NULL, 42, 0, 512, NULL) == EFI_INVALID_PARAMETER);
    assert(MMCHSReadBlocks(NULL, 42, UINT64_MAX, 0, NULL) == EFI_SUCCESS);
    assert(MMCHSReadBlocks(NULL, 41, 0, 512, buffer) == EFI_MEDIA_CHANGED);
    instance.BlockMedia.MediaPresent = 0;
    assert(MMCHSReadBlocks(NULL, 42, 0, 512, buffer) == EFI_NO_MEDIA);
    instance.BlockMedia.MediaPresent = 1;
    instance.BlockMedia.LastBlock = UINT64_MAX;
    assert(MMCHSReadBlocks(NULL, 42, UINT64_MAX, 1024, buffer) == EFI_INVALID_PARAMETER);
    assert(MMCHSReadBlocks(NULL, 42, UINT64_MAX / 512 + 1, 512, buffer) == EFI_BAD_BUFFER_SIZE);
    assert(MMCHSReadBlocks(NULL, 42, 0, (UINTN)UINT32_MAX + 1, buffer) == EFI_BAD_BUFFER_SIZE);
    assert(calls == 1);
    read_result = 0;
    assert(MMCHSReadBlocks(NULL, 42, 0, 512, buffer) == EFI_DEVICE_ERROR);
    assert(calls == 2);
    return 0;
}
''')

    def test_sd_diagnostics_success_and_failure_stages(self):
        prelude = r'''
#include <stdint.h>
#include <stdlib.h>
#include <stdarg.h>
#include <assert.h>
#define IN
#define EFIAPI
#define VOID void
#define NULL ((void *)0)
typedef uint64_t EFI_STATUS, UINT64;
typedef uint32_t UINT32;
typedef int32_t INT32;
typedef uint8_t UINT8;
typedef void *EFI_HANDLE;
typedef struct { int unused; } EFI_SYSTEM_TABLE;
typedef struct { uint32_t a; uint16_t b,c; uint8_t d[8]; } EFI_GUID;
enum { EFI_SUCCESS, EFI_OUT_OF_RESOURCES, EFI_NOT_READY, EFI_DEVICE_ERROR,
       EFI_UNSUPPORTED, EFI_NOT_FOUND };
#define EFI_ERROR(s) ((s) != EFI_SUCCESS)
#define DEBUG(x) do {} while (0)
#define DEBUG_INFO 0
'''
        prelude += (ROOT / "config/switchbsd-sd-status.h").read_text()
        prelude += r'''
static SWITCHBSD_SD_STATUS *published;
static SWITCHBSD_SD_STATUS *mSwitchSdDiag;
static int failure_stage;
static unsigned reads;
static UINT64 published_last;
static void *AllocateZeroPool(size_t n) { return calloc(1, n); }
static void FreePool(void *p) { free(p); }
static EFI_STATUS Table(EFI_GUID *g, void *p) { published = p; return EFI_SUCCESS; }
static EFI_STATUS Locate(EFI_GUID *g, void *unused, void **p) {
    return published->Stage == (UINT32)failure_stage ? EFI_NOT_FOUND : EFI_SUCCESS;
}
static EFI_GUID gTegraUBootClockManagementProtocolGuid, gPmicProtocolGuid;
static EFI_GUID gEfiBlockIoProtocolGuid, gEfiDevicePathProtocolGuid;
static void *mClkProtocol, *mPmicProtocol;
static struct { UINT64 lba; UINT32 blksz; } mBlkDesc;
static struct { int has_init; } mMmcInstance;
typedef struct { EFI_HANDLE Handle; struct {UINT32 BlockSize; UINT64 LastBlock;} BlockMedia;
                 int BlockIo; int DevicePath; } BIO_INSTANCE;
static BIO_INSTANCE *active;
static EFI_STATUS Install(EFI_HANDLE *h, ...) {
    published_last = active->BlockMedia.LastBlock;
    return failure_stage == SwitchSdPublish ? EFI_DEVICE_ERROR : EFI_SUCCESS;
}
static struct { EFI_STATUS (*InstallConfigurationTable)(EFI_GUID*, void*);
                EFI_STATUS (*LocateProtocol)(EFI_GUID*, void*, void**);
                EFI_STATUS (*InstallMultipleProtocolInterfaces)(EFI_HANDLE*, ...); }
    services = {Table, Locate, Install}, *gBS = &services;
static EFI_STATUS SdControllerProbe(void) {
    return failure_stage == SwitchSdProbe ? EFI_NOT_FOUND : EFI_SUCCESS;
}
static EFI_STATUS TegraMmcInit(void) {
    return failure_stage == SwitchSdHost ? EFI_DEVICE_ERROR : EFI_SUCCESS;
}
static int SdFxInit(void) { return failure_stage == SwitchSdCardInit ? -110 : 0; }
static int SdFxInitFinalize(void) { return failure_stage == SwitchSdCardFinalize ? -5 : 0; }
static int mmc_bread(UINT64 lba, unsigned count, void *b) {
    ++reads; assert(lba == 0 && count == 1);
    return failure_stage == SwitchSdReadSector ? 0 : 1;
}
static EFI_STATUS BioInstanceContructor(BIO_INSTANCE **p) {
    active = calloc(1, sizeof(*active)); *p = active; return EFI_SUCCESS;
}
'''
        self.compile_and_run("switchbsd-sd-init.c", prelude, r'''
int main(void) {
    for (int stage = SwitchSdClock; stage <= SwitchSdPublish; ++stage) {
        failure_stage = stage; reads = 0; active = NULL;
        mBlkDesc.lba = stage == SwitchSdCapacity ? 0 : 100;
        mBlkDesc.blksz = 512; mMmcInstance.has_init = 1;
        EFI_STATUS status = SdMmcDxeInitialize(NULL, NULL);
        assert(EFI_ERROR(status));
        assert(published->Stage == (UINT32)stage && published->Status == status);
        assert(reads == (stage >= SwitchSdReadSector ? 1u : 0u));
        free(published);
    }
    failure_stage = 0; mBlkDesc.lba = 100; reads = 0;
    assert(SdMmcDxeInitialize(NULL, NULL) == EFI_SUCCESS);
    assert(published->Stage == SwitchSdReady && published->Status == EFI_SUCCESS);
    assert(published_last == 99 && reads == 1);
    assert(published->Blocks == 100 && published->BlockSize == 512);
    free(published); free(active);
    return 0;
}
''')


if __name__ == "__main__":
    unittest.main()
