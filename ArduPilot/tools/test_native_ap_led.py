"""Exercise the actual native ArduPilot LED policy with deterministic status flags."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
assert not a.out.exists()
cpp=a.root/'source/libraries/AP_Notify/RGBLed.cpp';header=a.root/'source/libraries/AP_Notify/RGBLed.h'
text=cpp.read_text();start=text.index('uint32_t RGBLed::get_colour_sequence(void) const');end=text.index('uint32_t RGBLed::get_colour_sequence_traffic_light',start)
function=text[start:end]
h=header.read_text();definitions=h[h.index('#define DEFINE_COLOUR_SEQUENCE('):h.index('    uint8_t last_step;')]
gps=(a.root/'source/libraries/AP_GPS/AP_GPS.h').read_text()
status=re.search(r'enum GPS_Status\s*\{.*?\};',gps,re.S)[0]
fixture='''#include <cstdint>
#include <cassert>
#include <cstdio>
#define AP_GPS_ENABLED 1
#define AP_AHRS_ENABLED 1
struct Location {};
bool good_location=true;
namespace AP { struct AHRS { bool get_location(Location&) {return good_location;} bool get_origin(Location&) {return good_location;} }; AHRS object; AHRS& ahrs(){return object;} }
namespace AP_Notify { struct Flags {bool initialising,save_trim,esc_calibration,compass_cal_running,temp_cal_running,failsafe_radio,failsafe_gcs,failsafe_battery,ekf_bad,gps_glitching,leak_detected,armed,pre_arm_check,pre_arm_gps_check;uint8_t gps_status;} flags; }
class AP_GPS {public:
'''+status+'''};
class RGBLed {public: uint32_t get_colour_sequence() const;
'''+definitions+'''};
'''
tests='''int main() {
 RGBLed led; using namespace AP_Notify;
 flags={}; flags.initialising=true; assert(led.get_colour_sequence()==led.sequence_initialising);
 flags.save_trim=true;flags.failsafe_radio=true;assert(led.get_colour_sequence()==led.sequence_initialising);
 flags.initialising=false;assert(led.get_colour_sequence()==led.sequence_trim_or_esc);
 flags={};flags.failsafe_radio=true;assert(led.get_colour_sequence()==led.sequence_failsafe_radio_or_battery);
 flags.ekf_bad=true;assert(led.get_colour_sequence()==led.sequence_failsafe_ekf);
 flags.leak_detected=true;assert(led.get_colour_sequence()==led.sequence_failsafe_leak);
 flags={};flags.gps_glitching=true;assert(led.get_colour_sequence()==led.sequence_failsafe_gps_glitching);
 flags={};flags.armed=true;flags.gps_status=AP_GPS::GPS_OK_FIX_3D;assert(led.get_colour_sequence()==led.sequence_armed);
 flags.gps_status=0;assert(led.get_colour_sequence()==led.sequence_armed_no_gps_or_no_location);
 flags={};assert(led.get_colour_sequence()==led.sequence_prearm_failing);
 flags.pre_arm_check=true;assert(led.get_colour_sequence()==led.sequence_disarmed_bad_gps_or_no_location);
 flags.pre_arm_gps_check=true;flags.gps_status=AP_GPS::GPS_OK_FIX_3D;assert(led.get_colour_sequence()==led.sequence_disarmed_good_gps_and_location);
 flags.gps_status=AP_GPS::GPS_OK_FIX_3D_DGPS;assert(led.get_colour_sequence()==led.sequence_disarmed_good_dgps_and_location);
 good_location=false;assert(led.get_colour_sequence()==led.sequence_disarmed_bad_gps_or_no_location);
 puts("14 native LED policy checks passed");
}
'''
with tempfile.TemporaryDirectory(prefix='ecf-native-led-') as d:
    source=Path(d)/'test.cpp';binary=Path(d)/'test';source.write_text(fixture+function+tests)
    subprocess.run(['g++','-std=c++11','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
doc=dict(project='ArduCopter',passed=True,checks=14,actual_source=cpp.relative_to(a.root).as_posix(),
    source_sha256=hashlib.sha256(cpp.read_bytes()).hexdigest(),native_policy=True,flight_logic_modified=False)
a.out.write_text(json.dumps(doc,indent=2)+'\n');print(json.dumps(doc))
