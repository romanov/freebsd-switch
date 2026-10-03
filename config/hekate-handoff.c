/* SwitchBSD experiment. Copyright 2026. SPDX-License-Identifier: GPL-2.0-only
 * Shared ABI with coreboot-dram.c; no pointer from the SD image is trusted.
 */
#define SWITCHBSD_ROM_BASE 0xCF600000u
#define SWITCHBSD_ROM_SIZE 0x00A00000u
#define SWITCHBSD_DESC     0x4003E000u
#define SWITCHBSD_MAGIC    0x53425344u

static void _switchbsd_launch(void)
{
	u32 size = 0;
	u32 sum = 2166136261u;
	gfx_con.mute = false;
	if (h_cfg.t210b01) {
		EPRINTF("SwitchBSD requires Erista/T210.");
		return;
	}
	u8 *rom = sd_file_read("switchbsd/coreboot.rom", &size);
	if (!rom || size != SWITCHBSD_ROM_SIZE) {
		EPRINTF("SwitchBSD: missing or wrong-size coreboot.rom (10 MiB required).");
		free(rom);
		return;
	}
	/* ARM branch/entry validity is checked by the build validator. */
	for (u32 i = 0; i < size; i++) sum = (sum ^ rom[i]) * 16777619u;
	memcpy((void *)SWITCHBSD_ROM_BASE, rom, size);
	free(rom);
	memcpy((void *)RCM_PAYLOAD_ADDR, (void *)SWITCHBSD_ROM_BASE, 0x7000);
	_reloc_append(PATCHED_RELOC_ENTRY, EXT_PAYLOAD_ADDR, 0x7000);
	gfx_printf("SWITCHBSD: HEKATE_HANDOFF\n");
	uart_send(UART_B, (u8 *)"SWITCHBSD: HEKATE_HANDOFF\r\n", 27);
	uart_wait_xfer(UART_B, UART_TX_IDLE);
	sd_end();
	hw_deinit(false);
	/* Write descriptor last; Coreboot validates size, location and ROM checksum. */
	volatile u32 *desc = (volatile u32 *)SWITCHBSD_DESC;
	desc[0] = SWITCHBSD_MAGIC;
	desc[1] = 1;
	desc[2] = SWITCHBSD_ROM_BASE;
	desc[3] = SWITCHBSD_ROM_SIZE;
	desc[4] = sum;
	((void (*)(void))EXT_PAYLOAD_ADDR)();
}
