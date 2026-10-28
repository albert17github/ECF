/* MEMSIC MMC5983MA I2C driver. SPDX-License-Identifier: GPL-3.0-or-later */
#include <stdbool.h>
#include <stdint.h>
#include "platform.h"
#ifdef USE_MAG_MMC5983MA
#include "common/axis.h"
#include "drivers/time.h"
#include "drivers/sensor.h"
#include "drivers/compass/compass.h"
#include "compass_mmc5983ma.h"

#define MMC5983MA_DATA 0x00
#define MMC5983MA_STATUS 0x08
#define MMC5983MA_CTRL0 0x09
#define MMC5983MA_CTRL1 0x0A
#define MMC5983MA_CTRL2 0x0B
#define MMC5983MA_PRODUCT_ID 0x2F

static bool mmc5983maInit(magDev_t *mag)
{
    if (!busWrite(mag->busDev, MMC5983MA_CTRL1, 0x80)) {
        return false;
    }
    delay(15);
    /* Automatic SET/RESET, 200 Hz bandwidth, continuous 100 Hz. */
    return busWrite(mag->busDev, MMC5983MA_CTRL0, 0x20) &&
        busWrite(mag->busDev, MMC5983MA_CTRL1, 0x01) &&
        busWrite(mag->busDev, MMC5983MA_CTRL2, 0x0D);
}

static bool mmc5983maRead(magDev_t *mag)
{
    uint8_t status;
    uint8_t data[7];
    if (!busRead(mag->busDev, MMC5983MA_STATUS, &status) || !(status & 0x01) ||
        !busReadBuf(mag->busDev, MMC5983MA_DATA, data, sizeof(data))) {
        return false;
    }
    /* 18-bit offset binary, 16384 LSB/G; scale to 1024 counts/G. */
    for (unsigned axis = 0; axis < 3; axis++) {
        const uint32_t raw = ((uint32_t)data[axis * 2] << 10) |
            ((uint32_t)data[axis * 2 + 1] << 2) |
            ((data[6] >> (6 - axis * 2)) & 3);
        mag->magADCRaw[axis] = ((int32_t)raw - 131072) / 16;
    }
    /* The package uses a left-handed frame; INAV alignment is right-handed. */
    mag->magADCRaw[Y] = -mag->magADCRaw[Y];
    return true;
}

bool mmc5983maDetect(magDev_t *mag)
{
    mag->busDev = busDeviceInit(BUSTYPE_I2C, DEVHW_MMC5983MA, mag->magSensorToUse, OWNER_COMPASS);
    if (mag->busDev == NULL) {
        return false;
    }
    uint8_t id = 0;
    if (!busRead(mag->busDev, MMC5983MA_PRODUCT_ID, &id) || id != 0x30) {
        busDeviceDeInit(mag->busDev);
        return false;
    }
    mag->init = mmc5983maInit;
    mag->read = mmc5983maRead;
    return true;
}
#endif
