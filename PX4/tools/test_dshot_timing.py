# SPDX-License-Identifier: BSD-3-Clause
SOURCE_FILE = "source/platforms/nuttx/src/px4/stm/stm32_common/io_pins/io_timer.c"
FUNCTION = "int io_timer_set_dshot_burst_mode("
PREFIX = '\n// SPDX-License-Identifier: BSD-3-Clause\n#include <stdint.h>\n#include <stdbool.h>\n#include <stdio.h>\n#include <math.h>\n#define BOARD_DSHOT_TIMING_CORRECTION 1\n#define OK 0\n#define ERROR -1\n#define DSHOT_MOTOR_PWM_BIT_WIDTH 20u\n#define TIM_DMABURSTLENGTH_1TRANSFER 0\n#define TIM_DMABURSTLENGTH_2TRANSFERS 256\n#define TIM_DMABURSTLENGTH_3TRANSFERS 512\n#define TIM_DMABURSTLENGTH_4TRANSFERS 768\n#define TIM_DMABASE_CCR1 13\n#define TIM_DMABASE_CCR2 14\n#define TIM_DMABASE_CCR3 15\n#define TIM_DMABASE_CCR4 16\n#define ATIM_EGR_UG 1\nstatic uint32_t arr[3], psc[3], egr[3], dcr[3];\n#define rARR(t) arr[t]\n#define rPSC(t) psc[t]\n#define rEGR(t) egr[t]\n#define rDCR(t) dcr[t]\nstatic struct { uint32_t clock_freq; } io_timers[] = {{200000000}, {200000000}, {200000000}};\nstatic struct { struct { unsigned first_channel_index, channel_count; } element[3]; }\n    io_timers_channel_mapping = {{{0,4}, {4,2}, {6,2}}};\nstatic struct { unsigned timer_channel; } timer_io_channels[] = {{1},{2},{3},{4},{4},{3},{3},{4}};\n'
SUFFIX = '\nint main(void) {\n    const unsigned rates[] = {150000, 300000, 600000};\n    const unsigned prescalers[] = {66, 32, 16};\n    for (unsigned t=0; t<3; t++) {\n        for (unsigned i=0; i<3; i++) {\n            unsigned count = t == 0 ? 4 : 2;\n            if (io_timer_set_dshot_burst_mode(t, rates[i], count) != OK) return 1;\n            double actual = (double)io_timers[t].clock_freq / ((psc[t]+1)*(arr[t]+1));\n            double error = (actual/rates[i]-1)*100;\n            unsigned base = t == 0 ? TIM_DMABASE_CCR1 : TIM_DMABASE_CCR3;\n            bool passed = fabs(error) < 2 && arr[t] == 19 && psc[t] == prescalers[i]\n                && egr[t] == ATIM_EGR_UG && (dcr[t] & 255) == base;\n            printf("timer=%u rate=%u actual=%.6f error_percent=%.6f arr=%u psc=%u pass=%u\\n",\n                t, rates[i], actual, error, arr[t], psc[t], passed);\n            if (!passed) return 1;\n        }\n    }\n    if (io_timer_set_dshot_burst_mode(0, 0, 4) != ERROR) return 1;\n    if (io_timer_set_dshot_burst_mode(0, UINT32_MAX, 4) != ERROR) return 1;\n    if (io_timer_set_dshot_burst_mode(0, 300000, 0) != ERROR) return 1;\n    if (io_timer_set_dshot_burst_mode(0, 300000, 5) != ERROR) return 1;\n    puts("invalid inputs rejected");\n    return 0;\n}\n'
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
