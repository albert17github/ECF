/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <stdint.h>
#include "platform.h"
#include "drivers/bus.h"
#include "drivers/io.h"
#include "drivers/pwm_mapping.h"
#include "drivers/timer.h"
#include "drivers/sensor.h"


#include "io/serial.h"
#include "drivers/bus_i2c.h"

static bool ecfUart3Enabled;
static bool ecfUart8Enabled;

static void ecfLatchSharedInterfaces(void)
{
    const uint8_t mode = ecfSharedIoMode();
    ecfUart3Enabled = (mode & 1U) != 0;
    ecfUart8Enabled = (mode & 2U) != 0;
}

bool ecfSerialPortAllowed(uint8_t identifier)
{
    return (identifier != SERIAL_PORT_USART3 || ecfUart3Enabled) &&
           (identifier != SERIAL_PORT_USART8 || ecfUart8Enabled);
}

bool ecfI2cDeviceAllowed(uint8_t device)
{
    return device != I2CDEV_4 || !ecfUart3Enabled;
}

BUSDEV_REGISTER_SPI_TAG(busdev_icm45686_internal, DEVHW_ICM45686, BUS_SPI1, PA4, PC3, 0, DEVFLAGS_NONE, CW0_DEG);
BUSDEV_REGISTER_SPI_TAG(busdev_icm45686_external, DEVHW_ICM45686, BUS_SPI4, PE3, PE4, 1, DEVFLAGS_NONE, CW0_DEG);

BUSDEV_REGISTER_I2C(ecf_mag_hmc5883, DEVHW_HMC5883, BUS_I2C3, 0x1E, NONE, DEVFLAGS_NONE, 0);
BUSDEV_REGISTER_I2C(ecf_mag_qmc5883, DEVHW_QMC5883, BUS_I2C3, 0x0D, NONE, DEVFLAGS_NONE, 0);
BUSDEV_REGISTER_I2C(ecf_mag_qmc5883p, DEVHW_QMC5883P, BUS_I2C3, 0x2C, NONE, DEVFLAGS_NONE, 0);
BUSDEV_REGISTER_I2C(ecf_mag_ist8310, DEVHW_IST8310_0, BUS_I2C3, 0x0E, NONE, DEVFLAGS_NONE, 0);
BUSDEV_REGISTER_I2C(ecf_mag_lis3mdl, DEVHW_LIS3MDL, BUS_I2C3, 0x1E, NONE, DEVFLAGS_NONE, 0);
BUSDEV_REGISTER_I2C(ecf_mag_mmc5983, DEVHW_MMC5983MA, BUS_I2C2, 0x30, NONE, DEVFLAGS_NONE, 0);

timerHardware_t timerHardware[] = {
    DEF_TIM(TIM2, CH1, PA0,  TIM_USE_OUTPUT_AUTO, 0, 0), // CH1 / M1
    DEF_TIM(TIM2, CH2, PA1,  TIM_USE_OUTPUT_AUTO, 0, 1), // CH2 / M2
    DEF_TIM(TIM2, CH3, PA2,  TIM_USE_OUTPUT_AUTO, 0, 2), // CH3 / M3
    DEF_TIM(TIM2, CH4, PA3,  TIM_USE_OUTPUT_AUTO, 0, 3), // CH4 / M4
    DEF_TIM(TIM3, CH4, PB1,  TIM_USE_OUTPUT_AUTO, 0, 4), // CH5
    DEF_TIM(TIM3, CH3, PB0,  TIM_USE_OUTPUT_AUTO, 0, 5), // CH6
    DEF_TIM(TIM4, CH3, PD14, TIM_USE_OUTPUT_AUTO, 0, 6), // CH7
    DEF_TIM(TIM4, CH4, PD15, TIM_USE_OUTPUT_AUTO, 0, 7), // CH8
    /* Native CH1N polarity: PB7 is low at CCR1=0, high at full duty. */
    DEF_TIM(TIM17, CH1N, PB7, TIM_USE_LED, 0, 9),
};
const int timerHardwareCount = sizeof(timerHardware) / sizeof(timerHardware[0]);

void initialisePreBootHardware(void)
{
    ecfLatchSharedInterfaces();
    static const ioTag_t highImpedancePins[] = {
        IO_TAG(PA0), IO_TAG(PA1), IO_TAG(PA2), IO_TAG(PA3),
        IO_TAG(PB1), IO_TAG(PB0), IO_TAG(PD14), IO_TAG(PD15),
        IO_TAG(PE0), IO_TAG(PE1), IO_TAG(PC4), IO_TAG(PC5),
        IO_TAG(PD8), IO_TAG(PD9), IO_TAG(PD12), IO_TAG(PD13),
        IO_TAG(PE9), IO_TAG(PE13),
        IO_TAG(PB8), IO_TAG(PB9), IO_TAG(PB5), IO_TAG(PB6)
    };
    for (unsigned i = 0; i < sizeof(highImpedancePins) / sizeof(highImpedancePins[0]); i++) {
        IOConfigGPIO(IOGetByTag(highImpedancePins[i]),
            IO_CONFIG(GPIO_MODE_ANALOG, GPIO_SPEED_FREQ_LOW, GPIO_NOPULL));
    }
    /* PC3_C is the populated ICM45686 interrupt pad. */
    __HAL_RCC_SYSCFG_CLK_ENABLE();
    HAL_SYSCFG_AnalogSwitchConfig(SYSCFG_SWITCH_PC3, SYSCFG_SWITCH_PC3_CLOSE);
    IOConfigGPIO(IOGetByTag(IO_TAG(PC3)), IOCFG_IN_FLOATING);
}
