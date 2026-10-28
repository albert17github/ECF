/* SPDX-License-Identifier: GPL-3.0-or-later */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "drivers/dronecan/libcanard/canard.h"

typedef struct {
    bool initialized;
    uint8_t device;
    uint32_t clockHz;
    uint32_t txQueued;
    uint32_t txCompleted;
    uint32_t rxReceived;
    uint32_t instance;
    uint32_t cccr;
    uint32_t test;
    uint32_t nbtp;
    uint32_t psr;
    uint32_t ecr;
    uint32_t error;
} ecfCanDriverInfo_t;

typedef struct {
    uint8_t device;
    uint8_t peerNodeId;
    bool payloadMatched;
    bool nodeMatched;
    int16_t parseResult;
    CanardCANFrame transmitted;
    CanardCANFrame received;
} ecfCanLoopbackResult_t;

void ecfCanGetDriverInfo(ecfCanDriverInfo_t *info);
void ecfCanNoteTxComplete(const void *handle);
bool ecfCanDriverLoopbackExchange(const CanardCANFrame *tx, CanardCANFrame *rx);
bool ecfCanRunLoopback(uint32_t nonce, ecfCanLoopbackResult_t *result);
uint64_t dronecanMonotonicMicros(void);
