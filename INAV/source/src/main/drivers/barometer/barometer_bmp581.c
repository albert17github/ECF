/* Bosch BMP581 I2C forced-mode driver. SPDX-License-Identifier: GPL-3.0-or-later */
#include <stdbool.h>
#include <stdint.h>
#include "platform.h"
#ifdef USE_BARO_BMP581
#include "common/utils.h"
#include "drivers/time.h"
#include "drivers/barometer/barometer.h"
#include "barometer_bmp581.h"

#define BMP581_CHIP_ID 0x01
#define BMP581_INT_CONFIG 0x14
#define BMP581_INT_SOURCE 0x15
#define BMP581_TEMP_DATA 0x1D
#define BMP581_INT_STATUS 0x27
#define BMP581_STATUS 0x28
#define BMP581_OSR_CONFIG 0x36
#define BMP581_ODR_CONFIG 0x37
#define BMP581_CMD 0x7E
static int32_t pressureRaw;
static int32_t temperatureRaw;
static bool sampleValid;

static bool bmp581Start(baroDev_t *baro)
{
    sampleValid = false;
    /* Deep standby disabled; forced conversion. */
    return busWrite(baro->busDev, BMP581_ODR_CONFIG, 0x82);
}

static bool bmp581Read(baroDev_t *baro)
{
    uint8_t status, data[6];
    sampleValid = false;
    if (!busRead(baro->busDev, BMP581_INT_STATUS, &status) || !(status & 0x01) ||
        !busReadBuf(baro->busDev, BMP581_TEMP_DATA, data, sizeof(data))) {
        return false;
    }
    int32_t raw = (uint32_t)data[0] | ((uint32_t)data[1] << 8) | ((uint32_t)data[2] << 16);
    if (raw & 0x800000) {
        raw -= 0x1000000;
    }
    temperatureRaw = raw;
    pressureRaw = (uint32_t)data[3] | ((uint32_t)data[4] << 8) | ((uint32_t)data[5] << 16);
    sampleValid = pressureRaw >= 30000 * 64 && pressureRaw <= 125000 * 64;
    return sampleValid;
}

static bool bmp581Calculate(baroDev_t *baro, int32_t *pressure, int32_t *temperature)
{
    UNUSED(baro);
    if (!sampleValid) {
        return false;
    }
    if (pressure) {
        *pressure = pressureRaw / 64;
    }
    if (temperature) {
        *temperature = (int32_t)(((int64_t)temperatureRaw * 100) / 65536);
    }
    return true;
}

bool bmp581Detect(baroDev_t *baro)
{
    baro->busDev = busDeviceInit(BUSTYPE_I2C, DEVHW_BMP581, 0, OWNER_BARO);
    if (baro->busDev == NULL) {
        return false;
    }
    uint8_t id = 0, status = 0, intStatus = 0, intConfig = 0xFF;
    if (!busRead(baro->busDev, BMP581_CHIP_ID, &id) || (id != 0x50 && id != 0x51)) {
        goto fail;
    }
    /* The reset command may not be ACKed on I2C. Verify reset completion
     * using the identity, NVM state and clear-on-read POR status instead. */
    (void)busWrite(baro->busDev, BMP581_CMD, 0xB6);
    delay(5);
    if (!busRead(baro->busDev, BMP581_CHIP_ID, &id) || (id != 0x50 && id != 0x51) ||
        !busRead(baro->busDev, BMP581_STATUS, &status) || !(status & 0x02) || (status & 0x04) ||
        !busRead(baro->busDev, BMP581_INT_STATUS, &intStatus) || !(intStatus & 0x10) ||
        !busWrite(baro->busDev, BMP581_INT_CONFIG, 0x00) ||
        /* Enable internal data-ready status while the physical INT pin stays disabled. */
        !busWrite(baro->busDev, BMP581_INT_SOURCE, 0x01) ||
        !busRead(baro->busDev, BMP581_INT_CONFIG, &intConfig) || intConfig != 0x00 ||
        !busWrite(baro->busDev, BMP581_ODR_CONFIG, 0x80)) {
        goto fail;
    }
    delay(3);
    /* Temperature x1, pressure x8, pressure enabled. INT remains disabled. */
    if (!busWrite(baro->busDev, BMP581_OSR_CONFIG, 0x58)) {
        goto fail;
    }
    baro->ut_delay = 0;
    baro->start_ut = NULL;
    baro->get_ut = NULL;
    baro->up_delay = 20000;
    baro->start_up = bmp581Start;
    baro->get_up = bmp581Read;
    baro->calculate = bmp581Calculate;
    return true;
fail:
    busDeviceDeInit(baro->busDev);
    return false;
}
#endif
