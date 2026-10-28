/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "platform.h"
#if defined(ECF_CAN_DIAGNOSTICS) && defined(USE_DRONECAN)
#include "fc/runtime_config.h"
#include "build/atomic.h"
#include "drivers/nvic.h"
#include "drivers/dronecan/dronecan.h"
#include "drivers/dronecan/ecf_can_diagnostics.h"
#include <uavcan.protocol.NodeStatus.h>

extern CanardInstance canard;

bool ecfCanRunLoopback(uint32_t nonce, ecfCanLoopbackResult_t *result)
{
    static uint8_t transferId;
    memset(result, 0, sizeof(*result));
    result->parseResult = -1;
    if (ARMING_FLAG(ARMED) || !dronecanConfig()->enabled || !dronecanConfig()->internalLoopback ||
        dronecanGetState() != STATE_DRONECAN_NORMAL) {
        return false;
    }
    ecfCanDriverInfo_t driver;
    ecfCanGetDriverInfo(&driver);
    if (!driver.initialized) {
        return false;
    }
    result->device = driver.device;
    result->peerNodeId = dronecanConfig()->nodeID == 126 ? 127 : 126;
    struct uavcan_protocol_NodeStatus status = {
        .uptime_sec = nonce,
        .health = UAVCAN_PROTOCOL_NODESTATUS_HEALTH_OK,
        .mode = UAVCAN_PROTOCOL_NODESTATUS_MODE_OPERATIONAL,
        .sub_mode = 0,
        .vendor_specific_status_code = 0xECF3,
    };
    result->transmitted.id = CANARD_CAN_FRAME_EFF | (16U << 24) |
        (UAVCAN_PROTOCOL_NODESTATUS_ID << 8) | result->peerNodeId;
    const uint32_t size = uavcan_protocol_NodeStatus_encode(&status, result->transmitted.data
#if CANARD_ENABLE_TAO_OPTION
        , true
#endif
    );
    if (size != 7) {
        return false;
    }
    result->transmitted.data[7] = 0xC0 | (transferId++ & 31);
    result->transmitted.data_len = 8;
    if (!ecfCanDriverLoopbackExchange(&result->transmitted, &result->received)) {
        return false;
    }
    result->payloadMatched = result->received.id == result->transmitted.id &&
        result->received.data_len == result->transmitted.data_len &&
        memcmp(result->received.data, result->transmitted.data, 8) == 0;
    if (!result->payloadMatched) {
        return false;
    }
    // Parse only the frame actually read back from the controller's RX FIFO.
    // RX and the TX-complete IRQ share Libcanard's allocator.
    ATOMIC_BLOCK(NVIC_PRIO_CAN) {
        result->parseResult = canardHandleRxFrame(&canard, &result->received, dronecanMonotonicMicros());
    }
    for (uint8_t index = 0; index < dronecanGetNodeCount(); index++) {
        const dronecanNodeInfo_t *node = dronecanGetNode(index);
        if (node && node->nodeID == result->peerNodeId && node->uptime_sec == nonce &&
            node->vendor_status_code == 0xECF3 && node->health == 0 && node->mode == 0) {
            result->nodeMatched = true;
            break;
        }
    }
    return result->parseResult >= 0 && result->nodeMatched;
}
#endif
