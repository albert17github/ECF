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

#pragma once
#include <px4_platform_common/px4_config.h>
#include <nuttx/compiler.h>
#include <stdint.h>
#include <stm32_gpio.h>

#define BOARD_BMP581_DISABLE_INT_OUTPUT 1
#define BOARD_DSHOT_TIMING_CORRECTION 1
#define GPIO_OTGFS_VBUS (GPIO_INPUT|GPIO_FLOAT|GPIO_PORTA|GPIO_PIN15)
#define GPIO_ECF_RGB (GPIO_OUTPUT|GPIO_PUSHPULL|GPIO_SPEED_50MHz|GPIO_OUTPUT_CLEAR|GPIO_PORTB|GPIO_PIN7)
#define BOARD_HAS_N_S_RGB_LED 1
#define BOARD_SRGBLED_BIT 7
#define BOARD_SRGBLED_PORT STM32_GPIOB_ODR
#define HRT_TIMER 5
#define HRT_TIMER_CHANNEL 1
#define DIRECT_PWM_OUTPUT_CHANNELS 8
#define BOARD_NUM_IO_TIMERS 3
#define SDIO_SLOTNO 0
#define SDIO_MINOR 0
#define BOARD_NUMBER_BRICKS 1
#define BOARD_ADC_BRICK_VALID 0
#define BOARD_ADC_USB_CONNECTED px4_arch_gpioread(GPIO_OTGFS_VBUS)
#define ADC_BATTERY_VOLTAGE_CHANNEL 8
#define ADC_BATTERY_CURRENT_CHANNEL 4
#define ADC_CHANNELS ((1U << 8) | (1U << 4))
#define SYSTEM_ADC_BASE STM32_ADC1_BASE
#define BOARD_DMA_ALLOC_POOL_SIZE 5120
#define BOARD_HAS_ON_RESET 1
#define BOARD_HAS_ISP_BOOTLOADER 1
#define BOARD_ECF_USB_CLOCK_OFF_BEFORE_RESET 1
#define BOARD_ENABLE_CONSOLE_BUFFER
#define ECF_ANALOG(port, pin) (GPIO_ANALOG|GPIO_FLOAT|GPIO_PORT##port|GPIO_PIN##pin)
#define PX4_GPIO_INIT_LIST { \
		GPIO_OTGFS_VBUS, GPIO_ECF_RGB, \
		GPIO_CAN1_RX, GPIO_CAN1_TX, GPIO_CAN2_RX, GPIO_CAN2_TX, \
		ECF_ANALOG(E, 0), ECF_ANALOG(E, 1), ECF_ANALOG(C, 4), ECF_ANALOG(C, 5), \
		ECF_ANALOG(D, 8), ECF_ANALOG(D, 9), ECF_ANALOG(D, 12), ECF_ANALOG(D, 13), \
		ECF_ANALOG(A, 0), ECF_ANALOG(A, 1), ECF_ANALOG(A, 2), ECF_ANALOG(A, 3), \
		ECF_ANALOG(B, 0), ECF_ANALOG(B, 1), ECF_ANALOG(D, 14), ECF_ANALOG(D, 15), \
		ECF_ANALOG(E, 9), ECF_ANALOG(E, 13) }
__BEGIN_DECLS
int stm32_sdio_initialize(void);
void stm32_spiinitialize(void);
void stm32_usbinitialize(void);
void board_peripheral_reset(int ms);
void ecf_rgb_boot_test(void);
void ecf_rom_boot_stage(uint32_t stage);
#include <px4_platform_common/board_common.h>
__END_DECLS
