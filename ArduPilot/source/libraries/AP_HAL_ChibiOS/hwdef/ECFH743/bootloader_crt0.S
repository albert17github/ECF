/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Reuse the pinned ChibiOS startup after the native ROM-DFU handoff check. */
    .syntax unified
    .thumb
    .text
    .align 2
    .thumb_func
    .global _crt0_entry
_crt0_entry:
    ldr r0, =__main_stack_end__
    msr MSP, r0
    /* Keep reset-time CONTROL and interrupt masks for the ROM reset handler. */
    bl __entry_hook
    b ecf_crt0_continue

#define _crt0_entry ecf_crt0_continue
#include "crt0_v7m.S"
#undef _crt0_entry
