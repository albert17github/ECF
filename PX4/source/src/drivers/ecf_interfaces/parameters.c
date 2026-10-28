/****************************************************************************
 *
 *   Copyright (C) 2021 PX4 Development Team. All rights reserved.
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

/**
 * ECF shared pad mode
 *
 * ADC/UART8 use H1.15/H1.16; I2C4/UART3 use H2.5/H2.6.
 * External wiring must be mutually exclusive and use 3.3 V logic.
 * @value 0 ADC and I2C4
 * @value 1 ADC and UART3
 * @value 2 UART8 and I2C4
 * @value 3 UART8 and UART3
 * @min 0
 * @max 3
 * @reboot_required true
 * @group ECF
 */
PARAM_DEFINE_INT32(ECF_IO_MODE, 1);

/**
 * ECF INA238 I2C address
 *
 * Seven-bit address on the shared I2C4 bus.
 * @min 8
 * @max 119
 * @reboot_required true
 * @group ECF
 */
PARAM_DEFINE_INT32(ECF_INA_ADDR, 69);

/**
 * Enable the SPI4 secondary ICM45686
 *
 * SPI4 uses PE2/PE5/PE6, CS PE3 and DRDY PE4.
 * @boolean
 * @reboot_required true
 * @group ECF
 */
PARAM_DEFINE_INT32(ECF_IMU2_EN, 0);

/**
 * SPI4 secondary IMU rotation
 *
 * Native PX4 driver rotation relative to the core frame.
 * @min 0
 * @max 43
 * @reboot_required true
 * @group ECF
 */
PARAM_DEFINE_INT32(ECF_IMU2_ROT, 0);
