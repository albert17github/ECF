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
#include <stdbool.h>
#include <nuttx/cache.h>
#include "arm_internal.h"
#include "nvic.h"
#include "stm32_rtc.h"
#include "stm32_pwr.h"
#include "stm32_rcc.h"
#include "stm32_wdg.h"

#ifdef ECF_FIRSTBOOT_ROM_GUARD
// The linker reserves .sram4 as NOLOAD and places its heap after this area.
// A full cache line prevents sharing cache state with the SRAM4 heap.
volatile uint32_t ecf_boot_trace[8] __attribute__((section(".sram4"), used, aligned(32)));
#endif

void ecf_rom_boot_stage(uint32_t stage)
{
#ifdef ECF_FIRSTBOOT_ROM_GUARD
	ecf_boot_trace[2] = stage;
	up_clean_dcache((uintptr_t)ecf_boot_trace, (uintptr_t)ecf_boot_trace + sizeof(ecf_boot_trace));
#else
	(void)stage;
#endif
}

// Force a host-visible USB disconnect before the ROM takes over the PHY.
// This runs without a USB kernel clock, so use RCC reset rather than GRSTCTL.
static void ecf_usb_detach_before_rom(void)
{
	ecf_rom_boot_stage(0xdf10);
	modifyreg32(STM32_RCC_AHB4ENR, 0, RCC_AHB4ENR_GPIOAEN);
	(void)getreg32(STM32_RCC_AHB4ENR);
	modifyreg32(STM32_RCC_AHB1RSTR, 0, RCC_AHB1RSTR_OTGFSRST | RCC_AHB1RSTR_OTGHSRST);
	(void)getreg32(STM32_RCC_AHB1RSTR);
	stm32_configgpio(GPIO_OUTPUT | GPIO_PUSHPULL | GPIO_SPEED_2MHz | GPIO_OUTPUT_CLEAR | GPIO_PORTA | GPIO_PIN11);
	stm32_configgpio(GPIO_OUTPUT | GPIO_PUSHPULL | GPIO_SPEED_2MHz | GPIO_OUTPUT_CLEAR | GPIO_PORTA | GPIO_PIN12);

// Calibrated for the fastest application clock; reset HSI only lengthens this.
	for (unsigned int i = 0; i < 10; ++i) {
		up_mdelay(5);
		putreg32(IWDG_KR_KEY_RELOAD, STM32_IWDG_KR);
	}

	ecf_rom_boot_stage(0xdf11);
	stm32_configgpio(GPIO_ANALOG | GPIO_FLOAT | GPIO_PORTA | GPIO_PIN11);
	stm32_configgpio(GPIO_ANALOG | GPIO_FLOAT | GPIO_PORTA | GPIO_PIN12);
	modifyreg32(STM32_RCC_AHB1RSTR, RCC_AHB1RSTR_OTGFSRST | RCC_AHB1RSTR_OTGHSRST, 0);
	modifyreg32(STM32_RCC_AHB1ENR, RCC_AHB1ENR_OTGFSEN | RCC_AHB1ENR_OTGHSEN | RCC_AHB1ENR_OTGHSULPIEN, 0);
	__asm__ volatile("dsb\n isb" ::: "memory");
}

// Called after C memory initialization, before RCC, USB, caches or IRQ setup.
void ecf_rom_boot_check(void)
{
	const uint32_t rom = 0x1ff09800u; // ST AN2606: STM32H74x ROM vectors.
	bool requested = false;
	bool watchdog_requested = false;
#ifdef ECF_FIRSTBOOT_ROM_GUARD
	const uint32_t reset_reason = getreg32(STM32_RCC_RSR);
// Do not read uninitialized recovery RAM on a cold power-on reset.
	watchdog_requested = (reset_reason & RCC_RSR_IWDG1RSTF) != 0
			     && ecf_boot_trace[0] == 0x45434652u && ecf_boot_trace[1] == 0x41524d44u;
	requested = watchdog_requested;
#endif

	if (!requested) {
		modifyreg32(STM32_RCC_APB4ENR, 0, RCC_APB4ENR_RTCAPBEN);
		(void)getreg32(STM32_RCC_APB4ENR);
		requested = getreg32(STM32_RTC_BK0R) == 0xb007b007u;
	}

	if (!requested) {
#ifdef ECF_FIRSTBOOT_ROM_GUARD
		ecf_boot_trace[0] = 0x45434652u;
		ecf_boot_trace[1] = 0x41524d44u;
		ecf_boot_trace[2] = 1;
		ecf_boot_trace[3] = reset_reason;

		for (unsigned int i = 4; i < 8; ++i) { ecf_boot_trace[i] = 0; }

		modifyreg32(STM32_RCC_RSR, 0, RCC_RSR_RMVF);
		// Independent LSI: nominal maximum is 4096 * 256 / 32000 = 32.768 s.
		putreg32(IWDG_KR_KEY_START, STM32_IWDG_KR);
		putreg32(IWDG_KR_KEY_ENABLE, STM32_IWDG_KR);
		putreg32(IWDG_PR_DIV256, STM32_IWDG_PR);
		putreg32(IWDG_RLR_MAX, STM32_IWDG_RLR);

		while ((getreg32(STM32_IWDG_SR) & (IWDG_SR_PVU | IWDG_SR_RVU)) != 0) {}

		putreg32(IWDG_KR_KEY_RELOAD, STM32_IWDG_KR);
#endif
		return;
	}

#ifdef ECF_FIRSTBOOT_ROM_GUARD
	ecf_boot_trace[1] = 0;
	ecf_boot_trace[2] = 0xdf00;
	ecf_boot_trace[3] = reset_reason;
	ecf_boot_trace[4] = getreg32(STM32_RCC_CR);
	ecf_boot_trace[5] = getreg32(STM32_RCC_CFGR);
	ecf_boot_trace[6] = getreg32(STM32_RCC_AHB1ENR);
	ecf_boot_trace[7] = getreg32(STM32_RCC_AHB1RSTR);
#endif
// Retain the reset cause for the ROM's watchdog handling, and feed first.
	putreg32(IWDG_KR_KEY_RELOAD, STM32_IWDG_KR);
	__asm__ volatile("cpsid i" ::: "memory");

	if (!watchdog_requested) {
		modifyreg32(STM32_PWR_CR1, 0, PWR_CR1_DBP);

		while ((getreg32(STM32_PWR_CR1) & PWR_CR1_DBP) == 0) {}

		putreg32(0, STM32_RTC_BK0R);
	}

	putreg32(0, NVIC_SYSTICK_CTRL);
	putreg32(0, NVIC_SYSTICK_RELOAD);
	putreg32(0, NVIC_SYSTICK_CURRENT);

	for (unsigned int irq = 0; irq < 240; irq += 32) {
		putreg32(0xffffffffu, NVIC_IRQ_CLEAR(irq));
		putreg32(0xffffffffu, NVIC_IRQ_CLRPEND(irq));
	}

	putreg32(NVIC_INTCTRL_PENDSTCLR | NVIC_INTCTRL_PENDSVCLR, NVIC_INTCTRL);
	up_disable_dcache();
	up_disable_icache();
	ecf_usb_detach_before_rom();
	ecf_rom_boot_stage(0xdf00);
	putreg32(IWDG_KR_KEY_RELOAD, STM32_IWDG_KR);
	putreg32(rom, NVIC_VECTAB);
	const uint32_t stack = getreg32(rom);
	const uint32_t entry = getreg32(rom + 4);
	__asm__ volatile(
		"movs r3, #0\n"
		"msr control, r3\n"
		"msr basepri, r3\n"
		"msr faultmask, r3\n"
		"msr msp, %0\n"
		"dsb\n"
		"isb\n"
		"cpsie i\n"
		"bx %1\n"
		: : "r"(stack), "r"(entry) : "r3", "memory");
	__builtin_unreachable();
}
