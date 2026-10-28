/* SPDX-License-Identifier: GPL-3.0-or-later */
#pragma once
#include <stdint.h>
#include "config/parameter_group.h"
typedef struct ecfSharedIoConfig_s {
    uint8_t mode;
} ecfSharedIoConfig_t;
PG_DECLARE(ecfSharedIoConfig_t, ecfSharedIoConfig);
