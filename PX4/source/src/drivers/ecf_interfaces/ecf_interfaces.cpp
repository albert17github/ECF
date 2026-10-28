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

#include <board_config.h>
#include <arch/board/board.h>
#include <errno.h>
#include <parameters/param.h>
#include <px4_platform_common/log.h>
#include <px4_platform_common/module.h>
#include <cstdio>
#include <cstring>

extern "C" int ina238_main(int argc, char *argv[]);
extern "C" int icm45686_main(int argc, char *argv[]);

static bool read_integer(const char *name, int32_t &value)
{
	const param_t parameter = param_find(name);
	return parameter != PARAM_INVALID && param_get(parameter, &value) == PX4_OK;
}

extern "C" __EXPORT int ecf_interfaces_main(int argc, char *argv[])
{
	if (argc < 2) {
		PX4_INFO("configure | status | power | imu2");
		return -EINVAL;
	}
	if (!strcmp(argv[1], "configure")) {
		int32_t mode = -1;
		int32_t digital = -1;
		if (!read_integer("ECF_IO_MODE", mode) || !read_integer("SENS_EN_INA238", digital)
		    || mode < 0 || mode > 3 || digital < 0 || digital > 1) {
			PX4_ERR("invalid shared interface parameters");
			return -EINVAL;
		}
		const int result = ecf_shared_apply(static_cast<unsigned>(mode), digital == 0);
		if (result != 0) {
			PX4_ERR("interface selection requires reboot (%d)", result);
		}
		return result;
	}
	if (!strcmp(argv[1], "status")) {
		uint32_t base = 0;
		uint32_t channels = 0;
		ecf_adc_configuration(&base, &channels);
		PX4_INFO_RAW("ECF_IO_V1 MODE=%u ADC_BASE=0x%08lx ADC_MASK=0x%08lx I2C4=%u UART3=%u UART8=%u\n",
			ecf_shared_mode(), static_cast<unsigned long>(base), static_cast<unsigned long>(channels),
			unsigned(ecf_shared_i2c_allowed(4)), unsigned(ecf_shared_mode() <= 3 && (ecf_shared_mode() & 1)),
			unsigned(ecf_shared_mode() <= 3 && (ecf_shared_mode() & 2)));
		PX4_INFO_RAW("UART1 TEL1 /dev/ttyS0; UART3 TEL2 /dev/ttyS2; UART4 TEL3 /dev/ttyS3; "
			"UART5 TEL4 /dev/ttyS4; UART6 GPS1 /dev/ttyS5; UART7 GPS2 /dev/ttyS6; UART8 RC /dev/ttyS7\n");
		return 0;
	}
	if (!strcmp(argv[1], "power")) {
		int32_t enable = 0;
		int32_t address = 0;
		if (!read_integer("SENS_EN_INA238", enable) || enable != 1 || !ecf_shared_i2c_allowed(4)) {
			return -ENODEV;
		}
		if (!read_integer("ECF_INA_ADDR", address) || address < 8 || address > 119) {
			return -EINVAL;
		}
		char address_text[12];
		snprintf(address_text, sizeof(address_text), "%ld", static_cast<long>(address));
		char name[] = "ina238";
		char external[] = "-X";
		char bus_option[] = "-b";
		char bus[] = "4";
		char address_option[] = "-a";
		char battery_option[] = "-t";
		char battery[] = "1";
		char keep[] = "-k";
		char start[] = "start";
		char *arguments[] = {name, external, bus_option, bus, address_option, address_text,
			battery_option, battery, keep, start};
		return ina238_main(sizeof(arguments) / sizeof(arguments[0]), arguments);
	}
	if (!strcmp(argv[1], "imu2")) {
		int32_t enable = 0;
		int32_t rotation = 0;
		if (!read_integer("ECF_IMU2_EN", enable) || enable != 1) { return 0; }
		if (!read_integer("ECF_IMU2_ROT", rotation) || rotation < 0 || rotation > 43) { return -EINVAL; }
		char rotation_text[12];
		snprintf(rotation_text, sizeof(rotation_text), "%ld", static_cast<long>(rotation));
		char name[] = "icm45686";
		char internal[] = "-s";
		char bus_option[] = "-b";
		char bus[] = "4";
		char rotation_option[] = "-R";
		char start[] = "start";
		char *arguments[] = {name, internal, bus_option, bus, rotation_option, rotation_text, start};
		return icm45686_main(sizeof(arguments) / sizeof(arguments[0]), arguments);
	}
	return -EINVAL;
}
