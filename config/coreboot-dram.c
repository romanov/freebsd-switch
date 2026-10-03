/* SwitchBSD DRAM-backed CBFS transport. SPDX-License-Identifier: GPL-2.0-only */
#include <boot_device.h>
#include <console/console.h>
#include <halt.h>
#include <stdint.h>
#include <symbols.h>

#include "cbfs.h"

#define HANDOFF_ADDRESS 0x4003E000u
#define HANDOFF_MAGIC   0x53425344u
#define ROM_ADDRESS    0xCF600000u
#define ROM_LENGTH     0x00A00000u

static struct mem_region_device dram = MEM_REGION_DEV_RO_INIT((void *)ROM_ADDRESS, ROM_LENGTH);
static int checked;

void boot_device_init(void)
{
	const volatile uint32_t *d = (const volatile uint32_t *)HANDOFF_ADDRESS;
	if (checked) return;
	if (CONFIG_ROM_SIZE != ROM_LENGTH || d[0] != HANDOFF_MAGIC || d[1] != 1 ||
	    d[2] != ROM_ADDRESS || d[3] != ROM_LENGTH)
		die("SWITCHBSD: invalid Hekate handoff\n");
#if ENV_BOOTBLOCK
	uint32_t hash = 2166136261u;
	const uint8_t *p = (const uint8_t *)ROM_ADDRESS;
	for (uint32_t i = 0; i < ROM_LENGTH; i++) hash = (hash ^ p[i]) * 16777619u;
	if (hash != d[4]) die("SWITCHBSD: ROM checksum mismatch\n");
	printk(BIOS_INFO, "SWITCHBSD: COREBOOT_DRAM_VERIFIED\n");
#endif
	checked = 1;
}

const struct region_device *boot_device_ro(void)
{
	boot_device_init();
	return &dram.rdev;
}

void cbfs_switch_to_sdram(void)
{
	boot_device_init();
}
