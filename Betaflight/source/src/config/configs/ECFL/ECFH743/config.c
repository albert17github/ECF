#include <stdbool.h>
#include <stdint.h>
#include "platform.h"
#include "drivers/io.h"
#include "drivers/serial.h"
#include "pg/adc.h"
#include "pg/bus_i2c.h"

void configTargetPreInit(void)
{
    // TFBGA100 exposes PC3_C. Close its analog switch to digital PC3.
    __HAL_RCC_SYSCFG_CLK_ENABLE();
    HAL_SYSCFG_AnalogSwitchConfig(SYSCFG_SWITCH_PC3, SYSCFG_SWITCH_PC3_CLOSE);
    // Keep both sides of shared pads and all actuator outputs undriven.
    const ioTag_t highZ[] = {
        IO_TAG(PE0), IO_TAG(PE1), IO_TAG(PC4), IO_TAG(PC5),
        IO_TAG(PD8), IO_TAG(PD9), IO_TAG(PD12), IO_TAG(PD13),
        IO_TAG(PA0), IO_TAG(PA1), IO_TAG(PA2), IO_TAG(PA3),
        IO_TAG(PB0), IO_TAG(PB1), IO_TAG(PD14), IO_TAG(PD15),
        IO_TAG(PE9), IO_TAG(PE13)
    };
    for (unsigned i = 0; i < sizeof(highZ) / sizeof(highZ[0]); i++) {
        IOConfigGPIO(IOGetByTag(highZ[i]), IO_CONFIG(GPIO_MODE_ANALOG, 0, GPIO_NOPULL));
    }
    IOConfigGPIO(IOGetByTag(IO_TAG(PA15)), IOCFG_IN_FLOATING);
}

// Two different MCU pins are electrically joined at each shared pad.
// UART mode takes priority over a conflicting peripheral assignment.
void targetValidateConfiguration(void)
{
    const serialPinConfig_t *serial = serialPinConfig();
    if (serial->ioTagTx[2] == IO_TAG(PD8) || serial->ioTagRx[2] == IO_TAG(PD9)) {
        i2cConfigMutable(I2CDEV_4)->ioTagScl = IO_TAG_NONE;
        i2cConfigMutable(I2CDEV_4)->ioTagSda = IO_TAG_NONE;
    }
    adcConfig_t *adc = adcConfigMutable();
    adcChannelConfig_t *channels[] = { &adc->vbat, &adc->current, &adc->rssi, &adc->external1 };
    const bool uart8 = serial->ioTagTx[7] == IO_TAG(PE1) || serial->ioTagRx[7] == IO_TAG(PE0);
    for (unsigned i = 0; i < sizeof(channels) / sizeof(channels[0]); i++) {
        if (uart8 && (channels[i]->ioTag == IO_TAG(PC4) || channels[i]->ioTag == IO_TAG(PC5))) {
            channels[i]->ioTag = IO_TAG_NONE;
        }
        channels[i]->enabled = channels[i]->ioTag != IO_TAG_NONE;
    }
}
