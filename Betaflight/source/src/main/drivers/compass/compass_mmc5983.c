// SPDX-License-Identifier: GPL-3.0-or-later
// MEMSIC MMC5983MA Rev A: I2C, 18-bit output, 100 Hz continuous mode.
#include <stdbool.h>
#include <stdint.h>
#include "platform.h"
#ifdef USE_MAG_MMC5983
#include "drivers/bus.h"
#include "drivers/bus_i2c.h"
#include "drivers/bus_i2c_busdev.h"
#include "drivers/time.h"
#include "compass.h"
#include "compass_mmc5983.h"

#ifdef ECF_ASYNC_I2C_SENSORS
static uint8_t mmc5983ReadState;
static uint8_t mmc5983Status;
static uint8_t mmc5983Buffer[7];
#endif

static bool mmc5983Init(magDev_t *magDev)
{
    extDevice_t *dev = &magDev->dev;
#ifdef ECF_ASYNC_I2C_SENSORS
    mmc5983ReadState = 0;
    mmc5983Status = 0;
#endif
    if (!busWriteRegister(dev, 0x0a, 0x80)) { return false; }
    delay(20);
    if (!busWriteRegister(dev, 0x09, 0x08)) { return false; }
    delay(1);
    // BW=00 (8ms); automatic SET/RESET; periodic SET every 100 samples.
    if (!busWriteRegister(dev, 0x0a, 0x00) ||
        !busWriteRegister(dev, 0x09, 0x20) ||
        !busWriteRegister(dev, 0x0b, 0xbd)) { return false; }
    busDeviceRegister(dev);
    magDev->magOdrHz = 100;
    return true;
}

static bool mmc5983Read(magDev_t *magDev, int16_t *data)
{
    extDevice_t *dev = &magDev->dev;
#ifdef ECF_ASYNC_I2C_SENSORS
    // Buffers persist until the interrupt-driven transfer completes.
    bool error = false;
    if (busBusy(dev, &error)) { return false; }
    if (error) { mmc5983ReadState = 0; }
    if (mmc5983ReadState == 0) {
        mmc5983Status = 0;
        if (busReadRegisterBufferStart(dev, 0x08, &mmc5983Status, 1)) {
            mmc5983ReadState = 1;
        }
        return false;
    }
    if (mmc5983ReadState == 1) {
        mmc5983ReadState = 0;
        if ((mmc5983Status & 1) && busReadRegisterBufferStart(dev, 0x00, mmc5983Buffer, sizeof(mmc5983Buffer))) {
            mmc5983ReadState = 2;
        }
        return false;
    }
    const uint8_t *b = mmc5983Buffer;
    mmc5983ReadState = 0;
#else
    uint8_t status = 0, b[7];
    if (!busReadRegisterBuffer(dev, 0x08, &status, 1) || !(status & 1)) { return false; }
    if (!busReadRegisterBuffer(dev, 0x00, b, sizeof(b))) { return false; }
#endif
    for (unsigned axis = 0; axis < 3; axis++) {
        const uint32_t raw = ((uint32_t)b[axis * 2] << 10) |
            ((uint32_t)b[axis * 2 + 1] << 2) | ((b[6] >> (6 - axis * 2)) & 3);
        // MEMSIC coordinates are left-handed. Convert Y before board rotation.
        int32_t value = ((int32_t)raw - 131072) / 4;
        if (axis == 1) { value = -value; }
        data[axis] = value > 32767 ? 32767 : value;
    }
    return true;
}

bool mmc5983Detect(magDev_t *magDev)
{
    extDevice_t *dev = &magDev->dev;
    if (dev->bus->busType != BUS_TYPE_I2C) { return false; }
    const uint8_t previousAddress = dev->busType_u.i2c.address;
    if (!previousAddress) { dev->busType_u.i2c.address = 0x30; }
    uint8_t id = 0;
    if (!busReadRegisterBuffer(dev, 0x2f, &id, 1) || id != 0x30) {
        dev->busType_u.i2c.address = previousAddress;
        return false;
    }
    magDev->init = mmc5983Init;
    magDev->read = mmc5983Read;
    return true;
}
#endif
