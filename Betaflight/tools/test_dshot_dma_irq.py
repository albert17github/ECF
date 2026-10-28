# SPDX-License-Identifier: GPL-3.0-or-later
SOURCE_FILE = "source/src/platform/STM32/pwm_output_dshot_hal.c"
FUNCTION = "FAST_CODE static void motor_DMA_IRQHandler("
PREFIX = '\n// SPDX-License-Identifier: GPL-3.0-or-later\n// Test fixture includes an unmodified callback from Betaflight.\n#include <stdbool.h>\n#include <stdint.h>\n#include <stdio.h>\n#define FAST_CODE\n#define USE_DSHOT_DMAR\n#define USE_DSHOT_TELEMETRY\n#define DMA_IT_TCIF 1\n#define GCR_TELEMETRY_INPUT_LEN 32\ntypedef struct { int stream; bool enabled; } dmaResource_t;\ntypedef struct { bool update_enabled; } TIM_TypeDef;\ntypedef struct { dmaResource_t *dmaTimUPRef; TIM_TypeDef *tim; } timerHardware_t;\ntypedef struct { dmaResource_t *dmaBurstRef; } timerState_t;\ntypedef struct { timerHardware_t *timerHardware; dmaResource_t *dmaRef; timerState_t *timer; bool isInput; unsigned timerDmaSource; } motorDmaOutput_t;\ntypedef struct { unsigned userParam; bool tc; } dmaChannelDescriptor_t;\nstatic motorDmaOutput_t dmaMotors[1];\nstatic bool useBurstDshot, useDshotTelemetry;\nstatic struct { unsigned irqAt, changeDirectionCompletedAt; } dshotDMAHandlerCycleCounters;\n#define DMA_GET_FLAG_STATUS(d,f) ((d)->tc)\n#define DMA_CLEAR_FLAG(d,f) ((d)->tc = false)\nstatic unsigned getCycleCounter(void) { return 0; }\nstatic void xLL_EX_DMA_DisableResource(dmaResource_t *r) { r->enabled = false; }\nstatic void xLL_EX_DMA_EnableResource(dmaResource_t *r) { r->enabled = true; }\nstatic void xLL_EX_DMA_SetDataLength(dmaResource_t *r, unsigned n) { (void)r; (void)n; }\nstatic void LL_TIM_DisableDMAReq_UPDATE(TIM_TypeDef *t) { t->update_enabled = false; }\nstatic void LL_EX_TIM_DisableIT(TIM_TypeDef *t, unsigned n) { (void)t; (void)n; }\nstatic void LL_EX_TIM_EnableIT(TIM_TypeDef *t, unsigned n) { (void)t; (void)n; }\nstatic void pwmDshotSetDirectionInput(motorDmaOutput_t *m) { m->isInput = true; }\n'
SUFFIX = '\nint main(void) {\n    int violations = 0;\n    for (unsigned i = 0; i < 4; i++) {\n        dmaResource_t unrelated = {0, true};\n        // A normal DMA stream has already cleared EN on transfer completion.\n        dmaResource_t configured = {(int)(10 + i % 3), false};\n        TIM_TypeDef timer = {true};\n        timerHardware_t hardware = {&unrelated, &timer};\n        timerState_t state = {&configured};\n        dmaMotors[0] = (motorDmaOutput_t){&hardware, &configured, &state, false, 1};\n        useBurstDshot = i < 3;\n        dmaChannelDescriptor_t descriptor = {0, true};\n        motor_DMA_IRQHandler(&descriptor);\n        bool passed = unrelated.enabled && !configured.enabled && !descriptor.tc;\n        if (!passed) violations++;\n        printf("case=%u burst=%u configured_option=%d unrelated_dma_enabled=%u pass=%u\\n",\n            i, useBurstDshot, configured.stream, unrelated.enabled, passed);\n    }\n    return violations ? 1 : 0;\n}\n'
"""Compile and execute the release driver function with isolated host registers."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--cc', default='gcc')
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
root = Path(__file__).resolve().parents[1]
source = root / SOURCE_FILE
text = source.read_text()
start = text.index(FUNCTION)
brace = text.index('{', start)
end = brace + 1
depth = 1
while depth:
    depth += (text[end] == '{') - (text[end] == '}')
    end += 1
a.out.mkdir(parents=True, exist_ok=False)
code = a.out / 'regression.c'
code.write_text(PREFIX + text[start:end] + SUFFIX)
binary = a.out / 'regression'
subprocess.run([a.cc, '-std=c11', '-Wall', '-Wextra', '-Werror', str(code), '-o', str(binary)], check=True)
run = subprocess.run([str(binary.resolve())], text=True, capture_output=True)
record = {'passed': run.returncode == 0, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'output': run.stdout, 'scope': 'host regression with mocked peripheral registers; no physical timing claim'}
(a.out / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record), flush=True)
raise SystemExit(run.returncode)
