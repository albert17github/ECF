/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <stdint.h>
#include "platform.h"
#include "fc/config.h"
#include "io/ledstrip.h"
#include "drivers/bus_i2c.h"
#include "drivers/timer.h"
#include "flight/mixer.h"
#include "flight/mixer_profile.h"
#include "sensors/compass.h"
#include "sensors/barometer.h"
#include "sensors/acceleration.h"
#include "sensors/pitotmeter.h"
#include "rx/rx.h"
#include "blackbox/blackbox.h"
#include "blackbox/blackbox_io.h"
#include "drivers/pwm_mapping.h"
#include "drivers/adc.h"
#include "io/serial.h"
#include "sensors/battery.h"
#include "config/feature.h"
#include "config/parameter_group_ids.h"
#include "target/ECFH743/shared_io.h"
#include "drivers/dronecan/dronecan.h"
#include "sensors/esc_sensor.h"

PG_REGISTER_WITH_RESET_TEMPLATE(ecfSharedIoConfig_t, ecfSharedIoConfig, PG_ECF_SHARED_IO_CONFIG, 0);
PG_RESET_TEMPLATE(ecfSharedIoConfig_t, ecfSharedIoConfig, .mode = 1);

uint8_t ecfSharedIoMode(void)
{
    return ecfSharedIoConfig()->mode <= 3 ? ecfSharedIoConfig()->mode : 1;
}

void targetConfiguration(void)
{
    accelerometerConfigMutable()->acc_hardware = ACC_ICM45686;
    barometerConfigMutable()->baro_hardware = BARO_BMP581;
    compassConfigMutable()->mag_hardware = MAG_AUTODETECT;
    systemConfigMutable()->i2c_speed = I2C_SPEED_400KHZ;
    /* Align the MMC5983MA driver frame to the ICM45686 native frame. */
    compassConfigMutable()->mag_align = ALIGN_DEFAULT;
    /* Mount the onboard SD NAND even when Blackbox recording is disabled. */
    blackboxConfigMutable()->device = BLACKBOX_DEVICE_SDCARD;
    parseLedStripConfig(0, "0,0::AW:0");
    for (unsigned i = 0; i < LED_CONFIGURABLE_COLOR_COUNT; i++) {
        if (ledStripConfigMutable()->colors[i].v > 26) {
            ledStripConfigMutable()->colors[i].v = 26;
        }
    }
    batteryMetersConfigMutable()->voltage.type = VOLTAGE_SENSOR_ADC;
    batteryMetersConfigMutable()->current.type = CURRENT_SENSOR_ADC;
    featureSet(FEATURE_VBAT | FEATURE_CURRENT_METER | FEATURE_GPS | FEATURE_TELEMETRY);
    rxConfigMutable()->receiverType = RX_TYPE_SERIAL;
    rxConfigMutable()->serialrx_provider = SERIALRX_CRSF;
    rxConfigMutable()->serialrx_inverted = false;
    rxConfigMutable()->halfDuplex = 0;
    dronecanConfigMutable()->enabled = true;
    dronecanConfigMutable()->canDevice = 1;
    dronecanConfigMutable()->internalLoopback = false;
    dronecanConfigMutable()->nodeID = 10;
    escSensorConfigMutable()->listenOnly = false;
    featureClear(FEATURE_RSSI_ADC);
    for (unsigned i = 0; i < SERIAL_PORT_COUNT; i++) {
        serialPortConfig_t *port = &serialConfigMutable()->portConfigs[i];
        port->functionMask = FUNCTION_NONE;
        switch (port->identifier) {
        case SERIAL_PORT_USB_VCP:
        case SERIAL_PORT_USART5:
            port->functionMask = FUNCTION_MSP;
            port->msp_baudrateIndex = BAUD_115200;
            break;
        case SERIAL_PORT_USART1:
            port->functionMask = FUNCTION_RX_SERIAL;
            break;
        case SERIAL_PORT_USART6:
            port->functionMask = FUNCTION_GPS;
            port->gps_baudrateIndex = BAUD_115200;
            break;
        case SERIAL_PORT_USART7:
            port->functionMask = FUNCTION_ESCSERIAL;
            break;
        default:
            break;
        }
    }
    /* Keep M1-M4 on CH1-CH4 when a later timer is explicitly assigned motors. */
    timerOverridesMutable(timer2id(TIM2))->outputMode = OUTPUT_MODE_MOTORS;
    motorConfigMutable()->motorPwmProtocol = PWM_TYPE_DSHOT300;
    mixerConfigMutable()->platformType = PLATFORM_MULTIROTOR;
    *primaryMotorMixerMutable(0) = (motorMixer_t){ 1.0f, -1.0f,  1.0f, -1.0f };
    *primaryMotorMixerMutable(1) = (motorMixer_t){ 1.0f, -1.0f, -1.0f,  1.0f };
    *primaryMotorMixerMutable(2) = (motorMixer_t){ 1.0f,  1.0f,  1.0f,  1.0f };
    *primaryMotorMixerMutable(3) = (motorMixer_t){ 1.0f,  1.0f, -1.0f, -1.0f };
}


void ecfValidateSharedInterfaces(void)
{
    for (unsigned i = 0; i < SERIAL_PORT_COUNT; i++) {
        serialPortConfig_t *port = &serialConfigMutable()->portConfigs[i];
        if ((port->identifier == SERIAL_PORT_USART3 && !(ecfSharedIoMode() & 1U)) ||
            (port->identifier == SERIAL_PORT_USART8 && !(ecfSharedIoMode() & 2U))) {
            port->functionMask = FUNCTION_NONE;
        }
    }
    if (ecfSharedIoMode() & 2U) {
        batteryMetersConfigMutable()->voltage.type = VOLTAGE_SENSOR_NONE;
        batteryMetersConfigMutable()->current.type = CURRENT_SENSOR_NONE;
        featureClear(FEATURE_VBAT | FEATURE_CURRENT_METER | FEATURE_RSSI_ADC);
        if (rxConfig()->rssi_source == RSSI_SOURCE_ADC) {
            rxConfigMutable()->rssi_source = RSSI_SOURCE_NONE;
        }
#ifdef USE_PITOT
        if (pitotmeterConfig()->pitot_hardware == PITOT_ADC) {
            pitotmeterConfigMutable()->pitot_hardware = PITOT_NONE;
        }
#endif
    }
}
