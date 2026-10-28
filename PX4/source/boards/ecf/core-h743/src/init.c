/****************************************************************************
 *
 *   Copyright (c) 2021 PX4 Development Team. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in
 *    the documentation and/or other materials provided with the
 *    distribution.
 * 3. Neither the name PX4 nor the names of its contributors may be
 *    used to endorse or promote products derived from this software
 *    without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 * FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
 * INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
 * BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
 * OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
 * AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 * LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
 * ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 *
 ****************************************************************************/

#include "board_config.h"
#include <syslog.h>
#include <nuttx/config.h>
#include <nuttx/board.h>
#include <arch/board/board.h>
#include "arm_internal.h"
#include "hardware/stm32_syscfg.h"
#include <drivers/drv_board_led.h>
#include <systemlib/px4_macros.h>
#include <px4_platform_common/init.h>
#include <px4_platform/gpio.h>
#include <px4_platform/board_dma_alloc.h>

__EXPORT void board_peripheral_reset(int ms) { UNUSED(ms); }
__EXPORT void board_on_reset(int status)
{
	// PX4 reset requests: 1 = bootloader, 2 = ISP ROM.
	if (status == 1 || status == 2) {
		board_configure_reset(BOARD_RESET_MODE_BOOT_TO_BL, 0);
	}

	const uint32_t gpio[] = PX4_GPIO_INIT_LIST;
	px4_gpio_init(gpio, arraySize(gpio));

	if (status >= 0) { up_mdelay(100); }
}
__EXPORT void stm32_boardinitialize(void)
{
	ecf_rom_boot_stage(2);
	board_on_reset(-1);
// PC3_C must be connected to PC3 digital input/EXTI3 (switch closed).
	modifyreg32(STM32_RCC_APB4ENR, 0, RCC_APB4ENR_SYSCFGEN);
	modifyreg32(STM32_SYSCFG_PMC, SYSCFG_PMC_PC3SO, 0);
	stm32_spiinitialize();
	stm32_usbinitialize();
}
__EXPORT int board_app_initialize(uintptr_t arg)
{
	ecf_rom_boot_stage(3);
	px4_platform_init();

	if (board_dma_alloc_init() < 0) { syslog(LOG_ERR, "[boot] DMA alloc FAILED\n"); }

	drv_led_start();
	ecf_rgb_boot_test();
	board_hardfault_init(2, true);
#ifdef CONFIG_MMCSD
// Keep USB/NSH available even if the soldered storage fails to enumerate.
	int ret = stm32_sdio_initialize();

	if (ret != OK) { syslog(LOG_ERR, "[boot] SDMMC2 FAILED: %d\n", ret); }

#endif
	px4_platform_configure();
	ecf_rom_boot_stage(4);
	return OK;
}
