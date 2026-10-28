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
#include <arch/board/board.h>
#include <errno.h>
#include <stm32_adc.h>
#include <px4_platform_common/micro_hal.h>
#include "hardware/stm32_memorymap.h"

static unsigned selected_mode = UINT32_MAX;
static bool selected_analog_power;

extern "C" unsigned ecf_shared_mode(void) { return selected_mode; }
extern "C" bool ecf_shared_uart_allowed(uint32_t base)
{
	if (base == STM32_USART3_BASE) {
		return selected_mode <= 3 && (selected_mode & 1);
	}
	if (base == STM32_UART8_BASE) {
		return selected_mode <= 3 && (selected_mode & 2);
	}
	return true;
}
extern "C" bool ecf_shared_i2c_allowed(int port)
{
	return port != 4 || (selected_mode <= 3 && !(selected_mode & 1));
}
extern "C" bool ecf_shared_adc_allowed(uint32_t base)
{
	return base != STM32_ADC1_BASE ||
	       (selected_mode <= 3 && !(selected_mode & 2) && selected_analog_power);
}
extern "C" void ecf_adc_configuration(uint32_t *base, uint32_t *channels)
{
	const bool analog = ecf_shared_adc_allowed(STM32_ADC1_BASE);
	*base = analog ? STM32_ADC1_BASE : STM32_ADC3_BASE;
	*channels = analog ? ADC_CHANNELS : 0;
}
extern "C" int ecf_shared_apply(unsigned mode, bool analog_power)
{
	if (mode > 3) { return -EINVAL; }
	if (selected_mode != UINT32_MAX) {
		return selected_mode == mode && selected_analog_power == analog_power ? 0 : -EBUSY;
	}
	const irqstate_t state = px4_enter_critical_section();
	const uint32_t inactive[] = {
		ECF_ANALOG(C, 4), ECF_ANALOG(C, 5), ECF_ANALOG(E, 0), ECF_ANALOG(E, 1),
		ECF_ANALOG(D, 8), ECF_ANALOG(D, 9), ECF_ANALOG(D, 12), ECF_ANALOG(D, 13)
	};
	for (uint32_t gpio : inactive) { stm32_configgpio(gpio); }
	selected_analog_power = analog_power;
	selected_mode = mode;
	if (mode & 1) {
		stm32_configgpio(GPIO_USART3_TX); stm32_configgpio(GPIO_USART3_RX);
	} else {
		stm32_configgpio(GPIO_I2C4_SCL); stm32_configgpio(GPIO_I2C4_SDA);
	}
	if (mode & 2) {
		stm32_configgpio(GPIO_UART8_TX); stm32_configgpio(GPIO_UART8_RX);
	}
	px4_leave_critical_section(state);
	return 0;
}
