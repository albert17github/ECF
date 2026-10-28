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
#include <nuttx/arch.h>
#include <pthread.h>
#include <drivers/drv_board_led.h>
#include <drivers/drv_neopixel.h>
#include <px4_platform_common/micro_hal.h>

extern "C" {
	void led_init(void);
	void led_on(int led);
	void led_off(int led);
	void led_toggle(int led);
}

// One WS2812B-2020-V6, low brightness. Bench fallback uses the existing
// platform GPIO driver; TIM17_CH1N/DMA is deliberately not enabled here.
static uint8_t red, green, blue;
static pthread_mutex_t rgb_lock = PTHREAD_MUTEX_INITIALIZER;
static void send_rgb(uint8_t r, uint8_t g, uint8_t b)
{
// Calls are from tasks, not NuttX interrupt LEDs (CONFIG_ARCH_LEDS is off).
	pthread_mutex_lock(&rgb_lock);
	up_udelay(300);
	neopixel_write_no_dma(r, g, b, 1);
	up_udelay(300);
	pthread_mutex_unlock(&rgb_lock);
}
static void write_rgb() { send_rgb(red, green, blue); }
extern "C" int neopixel_init(neopixel::NeoLEDData *data, int count)
{
	if (count != 1) { return -1; }

	stm32_configgpio(GPIO_ECF_RGB);
	send_rgb(0, 0, 0);
	return 0;
}
extern "C" int neopixel_write(neopixel::NeoLEDData *data, int count)
{
	if (count != 1 || data == nullptr) { return -1; }

	send_rgb(data[0].R(), data[0].G(), data[0].B());
	return 0;
}
extern "C" int neopixel_deinit(void) { send_rgb(0, 0, 0); return 0; }
extern "C" __EXPORT void led_init(void)
{
	stm32_configgpio(GPIO_ECF_RGB);
	red = green = blue = 0;
	write_rgb();
}
extern "C" __EXPORT void led_on(int led)
{
	if (led == LED_RED) { red = 8; }

	if (led == LED_GREEN) { green = 8; }

	if (led == LED_BLUE) { blue = 8; }

	write_rgb();
}
extern "C" __EXPORT void led_off(int led)
{
	if (led == LED_RED) { red = 0; }

	if (led == LED_GREEN) { green = 0; }

	if (led == LED_BLUE) { blue = 0; }

	write_rgb();
}
extern "C" __EXPORT void led_toggle(int led)
{
	if (led == LED_RED) { red ^= 8; }

	if (led == LED_GREEN) { green ^= 8; }

	if (led == LED_BLUE) { blue ^= 8; }

	write_rgb();
}
extern "C" void ecf_rgb_boot_test(void)
{
	// Run before the scheduler: three white identification flashes.
	for (unsigned pulse = 0; pulse < 3; pulse++) {
		red = green = blue = 8; write_rgb(); up_mdelay(150);
		red = green = blue = 0; write_rgb(); up_mdelay(150);
	}
}
