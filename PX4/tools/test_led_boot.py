"""Compile the actual ECF startup LED code against a deterministic host clock."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
p.add_argument('--cxx', default='g++')
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
assert not a.out.exists()
r = json.loads((a.root / 'release.json').read_text(encoding='utf-8-sig'))
project = r['project']
source = a.root / 'source'
common = '#include <cassert>\n#include <cstdint>\n#include <vector>\n#include <array>\nuint32_t now_ms; bool armed;\n'
cases = 0
if project in ('Betaflight', 'INAV'):
    path = source / 'src/main/drivers/light_ws2811strip.c'
    text = path.read_text()
    start = text.index('#ifdef ECF_LED_BOOT_HUE\n    // Identify')
    block = text[start:text.index('#endif', start) + len('#endif')]
    hue, level = (180, 255) if project == 'Betaflight' else (300, 26)
    code = common + f'#define ECF_LED_BOOT_HUE {hue}\n'
    code += '''#define ARMING_FLAG(flag) (armed)
using hsvColor_t = struct { int h; int s; int v; };
uint32_t millis() { return now_ms; }
hsvColor_t sample() {
''' + block + '''
return ecfBootActive ? ecfBootColor : hsvColor_t{-1, 0, 0};
}
int main() {
    const uint32_t origin = 0xffffff00u;
    const uint32_t times[] = {0,149,150,299,300,449,450,599,600,749,750,899,900,1200};
    const bool on[] = {true,true,false,false,true,true,false,false,true,true,false,false,false,false};
    for (unsigned i=0; i<14; i++) {
        now_ms=origin+times[i]; armed=false;
        const auto color=sample();
        if (times[i]<900) {
            assert(color.h==HUE && color.s==0 && color.v==(on[i] ? LEVEL : 0));
            armed=true;
            assert(sample().h==-1);
        } else {
            assert(color.h==-1);
        }
    }
}
'''.replace('HUE', str(hue)).replace('LEVEL', str(level))
    cases = 26
elif project == 'ArduCopter':
    path = source / 'libraries/AP_Notify/RGBLed.cpp'
    text = path.read_text()
    start = text.index('#if defined(HAL_ECF_LED_BOOT_BLUE)', text.index('void RGBLed::update()'))
    block = text[start:text.index('#endif', start) + len('#endif')]
    code = common + '''#define HAL_ECF_LED_BOOT_BLUE 1
namespace AP_HAL { uint32_t millis() { return now_ms; } }
namespace AP_Notify { struct { bool armed; } flags; }
class Led {
    bool _ecf_boot_started=false;
    uint32_t _ecf_boot_start_ms=0;
    const uint8_t _led_off=0;
    enum class Source {standard};
    Source rgb_source() { return Source::standard; }
    uint8_t get_brightness() {return 51;}
    void _set_rgb(uint8_t r,uint8_t g,uint8_t b) {rgb={r,g,b};}
public:
    std::array<uint8_t,3> rgb{};
    void update() {
''' + block + '''
        _set_rgb(0,11,0);
    }
};
int main() {
    const uint32_t origin=0xffffff00u;
    Led led;
    const uint32_t times[] = {0,149,150,299,300,449,450,599,600,749,750,899,900,1200};
    const bool on[] = {true,true,false,false,true,true,false,false,true,true,false,false,false,false};
    for(unsigned i=0;i<14;i++) {
        now_ms=origin+times[i]; AP_Notify::flags.armed=false; led.update();
        if(times[i]<900) {
            assert(led.rgb[0]==0 && led.rgb[1]==0 && led.rgb[2]==(on[i]?51:0));
            AP_Notify::flags.armed=true; led.update();
            assert(led.rgb[0]==0 && led.rgb[1]==11 && led.rgb[2]==0);
        } else {
            assert(led.rgb[0]==0 && led.rgb[1]==11 && led.rgb[2]==0);
        }
    }
}
'''
    cases = 26
else:
    assert project == 'PX4'
    path = source / 'boards/ecf/core-h743/src/led.cpp'
    text = path.read_text()
    start = text.index('extern "C" void ecf_rgb_boot_test(void)')
    function = text[start:].strip()
    assert function.endswith('}')
    code = common + '''uint8_t red,green,blue;
std::vector<std::array<unsigned,4>> samples;
void write_rgb() {samples.push_back({now_ms,red,green,blue});}
void up_mdelay(unsigned ms) {now_ms+=ms;}
''' + function + '''
int main() {
    ecf_rgb_boot_test();
    assert(now_ms==900 && samples.size()==6);
    for(unsigned i=0;i<6;i++) {
        assert(samples[i][0]==150*i);
        for(unsigned c=1;c<4;c++) assert(samples[i][c]==(i%2==0?8u:0u));
    }
}
'''
    cases = 25

with tempfile.TemporaryDirectory(prefix='ecf-led-test-') as directory:
    cpp = Path(directory) / 'test.cpp'
    binary = Path(directory) / 'test'
    cpp.write_text(code)
    subprocess.run([a.cxx, '-std=c++17', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
doc = dict(at=datetime.now(timezone.utc).isoformat(), project=project, passed=True,
           actual_source=str(path.relative_to(a.root)), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
           checks=cases, timer_wrap_checked=project!='PX4', armed_bypass_checked=project!='PX4',
           final_native_state_restored=True, physical_color_observed=False)
a.out.parent.mkdir(parents=True,exist_ok=True)
a.out.write_text(json.dumps(doc,indent=2)+'\n')
print(json.dumps(doc))
