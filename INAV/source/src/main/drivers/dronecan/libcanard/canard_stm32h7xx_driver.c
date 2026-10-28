/*
 * canard_stm32_driver.c
 *
 *  Created on: Jul 8, 2024
 *      Author: Roni Kant
 */

#include "platform.h"

#ifdef USE_DRONECAN

#include "common/log.h"
#include "common/time.h"
#include "drivers/io.h"
#include "drivers/nvic.h"
#include "drivers/time.h"
#include "build/atomic.h"
#include "drivers/dronecan/dronecan.h"
#include "drivers/dronecan/ecf_can_diagnostics.h"
#include "fc/runtime_config.h"
#include "canard.h"
#include "canard_stm32_driver.h"
#include "canard_stm32_timing.h"

#include "stm32h7xx_hal.h"
#include "stm32h7xx_hal_def.h"
#include "stm32h7xx_hal_fdcan.h"

#include <string.h>
#include <stdint.h>

struct Timings {
        uint16_t prescaler;
        uint8_t sjw;
        uint8_t bs1;
        uint8_t bs2;
};

static bool canardSTM32ComputeTimings(const uint32_t target_bitrate, struct Timings*out_timings);
static void canardSTM32GPIO_Init(void);

static FDCAN_HandleTypeDef hfdcan1;
static bool ecfCanInitialized;
static uint8_t ecfCanDevice;
static volatile uint32_t ecfCanTxQueued;
static volatile uint32_t ecfCanTxCompleted;
static volatile uint32_t ecfCanRxReceived;

// ---- Public API -------------------------------------------------------------

/**
  * @brief CAN1 Initialization Function
  * @param  bitrate desired bitrate to run the CAN network at.
  * @retval ret == 0: OK (CANARD_OK), ret < 0: CANARD_ERROR
  */
int16_t canardSTM32CAN1_Init(uint32_t bitrate)
{
    struct Timings out_timings;

    FDCAN_FilterTypeDef sFilterConfig;
    sFilterConfig.IdType = FDCAN_EXTENDED_ID;
    sFilterConfig.FilterIndex = 0;
    sFilterConfig.FilterType = FDCAN_FILTER_MASK; /* ID1=pattern 0x0, ID2=mask 0x0: all bits don't care, accept any extended ID */
    sFilterConfig.FilterConfig = FDCAN_FILTER_TO_RXFIFO0;
    sFilterConfig.FilterID1 = 0x0;
    sFilterConfig.FilterID2 = 0x0;
    ecfCanInitialized = false;
    ecfCanDevice = dronecanConfig()->canDevice;
    if (ecfCanDevice < 1 || ecfCanDevice > 2) {
        return -CANARD_ERROR_INVALID_ARGUMENT;
    }
    hfdcan1.Instance = ecfCanDevice == 2 ? FDCAN2 : FDCAN1;
    hfdcan1.Init.FrameFormat = FDCAN_FRAME_CLASSIC;  // Initialize in CAN2.0 mode not CAN_FD
    hfdcan1.Init.Mode = dronecanConfig()->internalLoopback ? FDCAN_MODE_INTERNAL_LOOPBACK : FDCAN_MODE_NORMAL;
    hfdcan1.Init.AutoRetransmission = DISABLE;  // ENABLE fills the 32-slot TX FIFO on a degraded bus; DroneCAN reliability is handled at the application layer
    hfdcan1.Init.TransmitPause = DISABLE;
    hfdcan1.Init.ProtocolException = DISABLE;

    __HAL_RCC_FDCAN_CLK_ENABLE();

    if (!canardSTM32ComputeTimings(bitrate, &out_timings))
    {
        LOG_ERROR(CAN, "Failed to compute CAN timings for bitrate %lu", (unsigned long)bitrate);
        return -CANARD_ERROR_INTERNAL;
    }

    hfdcan1.Init.NominalPrescaler = out_timings.prescaler;
    hfdcan1.Init.NominalSyncJumpWidth = out_timings.sjw;
    hfdcan1.Init.NominalTimeSeg1 = out_timings.bs1;
    hfdcan1.Init.NominalTimeSeg2 = out_timings.bs2;
    LOG_DEBUG(CAN, "Prescaler: %d, SJW: %d, BS1: %d, BS2: %d", out_timings.prescaler, out_timings.sjw, out_timings.bs1, out_timings.bs2);

    hfdcan1.Init.RxFifo0ElmtsNbr = 30;
    hfdcan1.Init.RxFifo0ElmtSize = FDCAN_DATA_BYTES_8;
    hfdcan1.Init.RxBuffersNbr = 0;
    hfdcan1.Init.RxBufferSize = FDCAN_DATA_BYTES_8;
    hfdcan1.Init.StdFiltersNbr = 0;
    hfdcan1.Init.ExtFiltersNbr = 1;
    hfdcan1.Init.TxFifoQueueElmtsNbr = 3;
    hfdcan1.Init.TxEventsNbr = 0;
    hfdcan1.Init.TxBuffersNbr = 0;
    hfdcan1.Init.TxFifoQueueMode = FDCAN_TX_QUEUE_OPERATION;
    hfdcan1.Init.TxElmtSize = FDCAN_DATA_BYTES_8;

    canardSTM32GPIO_Init();  // Set up the pins for CAN and optional listen only mode

    if (HAL_FDCAN_Init(&hfdcan1) != HAL_OK)
    {
        LOG_ERROR(CAN, "Failed CAN Init");
        return -CANARD_ERROR_INTERNAL;
    }
    if (HAL_FDCAN_ConfigFilter(&hfdcan1, &sFilterConfig) != HAL_OK) {
        LOG_ERROR(CAN, "Failed Config Filter");
        return -CANARD_ERROR_INTERNAL;
    }
    if (HAL_FDCAN_ConfigGlobalFilter(&hfdcan1, FDCAN_REJECT, FDCAN_ACCEPT_IN_RX_FIFO0, FDCAN_REJECT_REMOTE, FDCAN_REJECT_REMOTE) != HAL_OK) {
        LOG_ERROR(CAN, "Failed to config FDCAN filter");
        return -CANARD_ERROR_INTERNAL;
    }

    if (HAL_FDCAN_Start(&hfdcan1) != HAL_OK) {
        LOG_ERROR(CAN, "Failed to Start");
        return -CANARD_ERROR_INTERNAL;
    }

    if (HAL_FDCAN_ActivateNotification(&hfdcan1, FDCAN_IT_TX_COMPLETE,
        FDCAN_TX_BUFFER0 | FDCAN_TX_BUFFER1 | FDCAN_TX_BUFFER2) != HAL_OK) { /* Must match TxFifoQueueElmtsNbr = 3 */
        LOG_ERROR(CAN, "Failed to activate interrupt notification");
        return -CANARD_ERROR_INTERNAL;
    }

    /* FDCAN_IT_TX_COMPLETE routes to LINE0 by default (ILS resets to 0) */
    const IRQn_Type irq = ecfCanDevice == 2 ? FDCAN2_IT0_IRQn : FDCAN1_IT0_IRQn;
    HAL_NVIC_SetPriority(irq, NVIC_PRIO_CAN, 0);
    HAL_NVIC_EnableIRQ(irq);
    ecfCanInitialized = true;

    return CANARD_OK;
}

/**
  * @brief  Process CAN message from RxLocation FIFO into rx_frame
  * @param  hfdcan pointer to an FDCAN_HandleTypeDef structure that contains
  *         the configuration information for the specified FDCAN.
  * @param  RxLocation Location of the received message to be read.
  *         This parameter can be a value of @arg FDCAN_Rx_location.
  * @param  rx_frame pointer to a CanardCANFrame structure where the received CAN message will be
  * 		stored.
  * @retval ret == 0: OK (CANARD_OK), ret < 0: CANARD_ERROR
  */
int16_t canardSTM32Receive(CanardCANFrame *const rx_frame) {
	if (rx_frame == NULL) {
		return -CANARD_ERROR_INVALID_ARGUMENT;
	}

    if (!ecfCanInitialized || HAL_FDCAN_GetRxFifoFillLevel(&hfdcan1, FDCAN_RX_FIFO0) == 0) {
        return 0;
    }
    FDCAN_RxHeaderTypeDef RxHeader;
	uint8_t RxData[8];

	if (HAL_FDCAN_GetRxMessage(&hfdcan1, FDCAN_RX_FIFO0, &RxHeader, RxData) == HAL_OK) {

		// Process ID to canard format
		rx_frame->id = RxHeader.Identifier;

		if (RxHeader.IdType == FDCAN_EXTENDED_ID) { // canard will only process the message if it is extended ID
			rx_frame->id |= CANARD_CAN_FRAME_EFF;
		}

		if (RxHeader.RxFrameType == FDCAN_REMOTE_FRAME) { // canard won't process the message if it is a remote frame
			rx_frame->id |= CANARD_CAN_FRAME_RTR;
		}

		/* FDCAN_DLC_BYTES_0..8 equal 0..8, so DataLength is the byte count in FDCAN_FRAME_CLASSIC mode. */
		if (RxHeader.DataLength > CANARD_CAN_FRAME_MAX_DATA_LEN) {
			return -CANARD_ERROR_INVALID_ARGUMENT;  /* should never happen in FDCAN_FRAME_CLASSIC mode */
		}
		rx_frame->data_len = (uint8_t)RxHeader.DataLength;
		memcpy(rx_frame->data, RxData, rx_frame->data_len);

		// assume a single interface
		rx_frame->iface_id = 0;
        ecfCanRxReceived++;

		return 1;
	}

	// Either no CAN msg to be read, or an error that can be read from hfdcan->ErrorCode
	return 0;
}

/**
  * @brief  Process tx_frame CAN message into Tx FIFO/Queue and transmit it
  * @param  tx_frame pointer to a CanardCANFrame structure that contains the CAN message to
  * 		transmit.
  * @retval ret == 0: OK (CANARD_OK), ret < 0: CANARD_ERROR
  */
int16_t canardSTM32Transmit(const CanardCANFrame* const tx_frame) {
	if (tx_frame == NULL) {
		return -CANARD_ERROR_INVALID_ARGUMENT;
	}

	if (tx_frame->id & CANARD_CAN_FRAME_ERR) {
		return -CANARD_ERROR_INVALID_ARGUMENT; // unsupported frame format
	}

	FDCAN_TxHeaderTypeDef TxHeader;
	uint8_t TxData[8];

	// Process canard id to STM FDCAN header format
	if (tx_frame->id & CANARD_CAN_FRAME_EFF) {
		TxHeader.IdType = FDCAN_EXTENDED_ID;
		TxHeader.Identifier = tx_frame->id & CANARD_CAN_EXT_ID_MASK;
	} else {
		TxHeader.IdType = FDCAN_STANDARD_ID;
		TxHeader.Identifier = tx_frame->id & CANARD_CAN_STD_ID_MASK;
	}

	TxHeader.DataLength = tx_frame->data_len;  /* FDCAN_DLC_BYTES_0..8 == 0..8; valid only in FDCAN_FRAME_CLASSIC mode */

	if (tx_frame->id & CANARD_CAN_FRAME_RTR) {
		TxHeader.TxFrameType = FDCAN_REMOTE_FRAME;
	} else {
		TxHeader.TxFrameType = FDCAN_DATA_FRAME;
	}

	TxHeader.ErrorStateIndicator = FDCAN_ESI_ACTIVE;
	TxHeader.BitRateSwitch = FDCAN_BRS_OFF;
	TxHeader.FDFormat = FDCAN_CLASSIC_CAN;
	TxHeader.TxEventFifoControl = FDCAN_NO_TX_EVENTS;
	TxHeader.MessageMarker = 0;
    if (TxHeader.DataLength <= sizeof(TxData))
    {
	    memcpy(TxData, tx_frame->data, TxHeader.DataLength);
    }
    else
    {
        LOG_ERROR(CAN, "Data to transmit is larger than 8 byte frame size");
        return -CANARD_ERROR_INVALID_ARGUMENT;
    }

    if (HAL_FDCAN_AddMessageToTxFifoQ(&hfdcan1, &TxHeader, TxData) == HAL_OK) {
        ecfCanTxQueued++;
		return 1;
	}

	// This might be for many reasons including the Tx Fifo being full, the error can be read from hfdcan->ErrorCode
	return 0;
}

void canardSTM32GetProtocolStatus(canardProtocolStatus_t *pProtocolStat){
    FDCAN_ProtocolStatusTypeDef protocolStatus = {};
    HAL_FDCAN_GetProtocolStatus(&hfdcan1, &protocolStatus);
    pProtocolStat->BusOff       = protocolStatus.BusOff;
    pProtocolStat->ErrorPassive = protocolStatus.ErrorPassive;
    /* HAL provides no accessor for ECR (TEC/REC); read directly. PSR fields (BusOff, ErrorPassive, LEC) come from HAL. */
    uint32_t ecr = hfdcan1.Instance->ECR;
    pProtocolStat->tec          = (uint8_t)(ecr & 0xFF);           /* ECR[7:0]  */
    pProtocolStat->rec          = (uint8_t)((ecr >> 8) & 0x7F);   /* ECR[14:8] */
    pProtocolStat->lec          = (uint8_t)(protocolStatus.LastErrorCode & 0x07);
}

uint32_t canardSTM32GetAndClearRxDropCount(void) {
    return 0;  // H7 FIFO0 (30 slots) has no software ring buffer; FIFO overflow drops are not currently counted
}

int32_t canardSTM32GetTxQueueFillLevel(void){
    return 0;
}

int32_t canardSTM32GetRxFifoFillLevel(void){
    return (HAL_FDCAN_GetRxFifoFillLevel(&hfdcan1, FDCAN_RX_FIFO0));
}

void canardSTM32RecoverFromBusOff(void){
    hfdcan1.Instance->TXBCR = 0xFFFFFFFFU;  // Cancel all pending TX requests before recovery
    /* H7 FDCAN does not set CCCR.INIT on bus-off entry (unlike F7 bxCAN ABOM).
       Hardware runs the 128x11 recessive-bit sequence autonomously. This clear
       is a defensive no-op in case software previously entered init mode. */
    CLEAR_BIT(hfdcan1.Instance->CCCR, FDCAN_CCCR_INIT);
}

/*
  get a 16 byte unique ID for this node, this should be based on the CPU unique ID or other unique ID
 */
void canardSTM32GetUniqueID(uint8_t id[16]) {
    uint32_t HALUniqueIDs[3];
    // Make Unique ID out of the 96-bit STM32 UID and fill the rest with 0s
    memset(id, 0, 16);
    HALUniqueIDs[0] = HAL_GetUIDw0();
    HALUniqueIDs[1] = HAL_GetUIDw1();
    HALUniqueIDs[2] = HAL_GetUIDw2();
    memcpy(id, HALUniqueIDs, 12);
}

// ---- ISR handler ------------------------------------------------------------

void FDCAN1_IT0_IRQHandler(void)
{
    if (ecfCanDevice == 1) {
        HAL_FDCAN_IRQHandler(&hfdcan1);
    }
}

void FDCAN2_IT0_IRQHandler(void)
{
    if (ecfCanDevice == 2) {
        HAL_FDCAN_IRQHandler(&hfdcan1);
    }
}

void ecfCanNoteTxComplete(const void *handle)
{
    if (handle == &hfdcan1) {
        ecfCanTxCompleted++;
    }
}

// ---- Private helpers --------------------------------------------------------

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void canardSTM32GPIO_Init(void)
{
    const ioTag_t txTag = ecfCanDevice == 2 ? IO_TAG(CAN2_TX) : IO_TAG(CAN1_TX);
    const ioTag_t rxTag = ecfCanDevice == 2 ? IO_TAG(CAN2_RX) : IO_TAG(CAN1_RX);
    const uint8_t af = ecfCanDevice == 2 ? GPIO_AF9_FDCAN2 : GPIO_AF9_FDCAN1;
    const uint8_t index = ecfCanDevice;
    IOInit(IOGetByTag(txTag), OWNER_DRONECAN, RESOURCE_CAN_TX, index);
    IOInit(IOGetByTag(rxTag), OWNER_DRONECAN, RESOURCE_CAN_RX, index);
    IOConfigGPIOAF(IOGetByTag(txTag), IO_CONFIG(GPIO_MODE_AF_PP, GPIO_SPEED_FREQ_VERY_HIGH, GPIO_PULLUP), af);
    IOConfigGPIOAF(IOGetByTag(rxTag), IO_CONFIG(GPIO_MODE_AF_PP, GPIO_SPEED_FREQ_VERY_HIGH, GPIO_PULLUP), af);
}

static bool canardSTM32ComputeTimings(const uint32_t target_bitrate, struct Timings *out_timings)
{
    CanardCanTimingSolution sol;
    if (!canardComputeCanTimingSolution(HAL_RCCEx_GetPeriphCLKFreq(RCC_PERIPHCLK_FDCAN), target_bitrate, &sol)) {
        return false;
    }

    out_timings->prescaler = sol.prescaler;
    out_timings->sjw = 1;
    out_timings->bs1 = sol.bs1;  // The HAL takes care of the 1 bs offset in the register so don't remove it here like AP does.
    out_timings->bs2 = sol.bs2;  // The HAL takes care of the 1 bs offset in the register so don't remove it here like AP does.

    return true;
}

void ecfCanGetDriverInfo(ecfCanDriverInfo_t *info)
{
    memset(info, 0, sizeof(*info));
    info->initialized = ecfCanInitialized;
    info->device = ecfCanDevice;
    info->txQueued = ecfCanTxQueued;
    info->txCompleted = ecfCanTxCompleted;
    info->rxReceived = ecfCanRxReceived;
    if (hfdcan1.Instance) {
        info->instance = (uint32_t)hfdcan1.Instance;
        info->clockHz = HAL_RCCEx_GetPeriphCLKFreq(RCC_PERIPHCLK_FDCAN);
        info->cccr = hfdcan1.Instance->CCCR;
        info->test = hfdcan1.Instance->TEST;
        info->nbtp = hfdcan1.Instance->NBTP;
        info->psr = hfdcan1.Instance->PSR;
        info->ecr = hfdcan1.Instance->ECR;
        info->error = hfdcan1.ErrorCode;
    }
}

bool ecfCanDriverLoopbackExchange(const CanardCANFrame *tx, CanardCANFrame *rx)
{
    if (!ecfCanInitialized || hfdcan1.Init.Mode != FDCAN_MODE_INTERNAL_LOOPBACK || ARMING_FLAG(ARMED)) {
        return false;
    }
    int16_t queued = 0;
    ATOMIC_BLOCK(NVIC_PRIO_CAN) {
        queued = canardSTM32Transmit(tx);
    }
    if (queued != 1) {
        return false;
    }
    const uint32_t start = micros();
    while ((uint32_t)(micros() - start) < 10000U) {
        if (canardSTM32Receive(rx) == 1 && rx->id == tx->id && rx->data_len == tx->data_len &&
            memcmp(rx->data, tx->data, tx->data_len) == 0) {
            return true;
        }
    }
    return false;
}

#endif
