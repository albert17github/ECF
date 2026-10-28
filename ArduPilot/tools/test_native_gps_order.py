"""Compile and exercise the actual serial-manager lookup with minimal host stubs."""
from pathlib import Path
import argparse
import subprocess
import tempfile

p=argparse.ArgumentParser()
p.add_argument('--project',type=Path,required=True)
a=p.parse_args()
code=(a.project/'source/libraries/AP_SerialManager/AP_SerialManager.cpp').read_text()
start=code.index('const AP_SerialManager::UARTState *AP_SerialManager::find_protocol_instance(')
end=code.index('// find_serial -',start)
function=code[start:end]
fixture=r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#define SERIALMANAGER_NUM_PORTS 9
#define AP_SERIALMANAGER_REGISTER_ENABLED 0
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define HAL_ECF_GPS_PORT_ORDER 6,4,0,1,2,3,5,7,8
class AP_SerialManager {
public:
 enum SerialProtocol { SerialProtocol_None=-1,SerialProtocol_MAVLink2=2,SerialProtocol_GPS=5,SerialProtocol_GPS2=6 };
 struct Param { int value=-1;int get()const{return value;} };
 struct UARTState { Param protocol; };
 UARTState state[SERIALMANAGER_NUM_PORTS];
 bool protocol_match(SerialProtocol a,SerialProtocol b)const {
   return a==b || ((a==SerialProtocol_GPS || a==SerialProtocol_GPS2)&&(b==SerialProtocol_GPS || b==SerialProtocol_GPS2));
 }
 const UARTState *find_protocol_instance(SerialProtocol,uint8_t)const;
};
'''
checks=r'''
int main(){
 AP_SerialManager m;
 m.state[4].protocol.value=5;m.state[6].protocol.value=5;
 assert(m.find_protocol_instance(AP_SerialManager::SerialProtocol_GPS,0)==&m.state[6]);
 assert(m.find_protocol_instance(AP_SerialManager::SerialProtocol_GPS,1)==&m.state[4]);
 assert(m.find_protocol_instance(AP_SerialManager::SerialProtocol_GPS,2)==nullptr);
 m.state[4].protocol.value=6;
 assert(m.find_protocol_instance(AP_SerialManager::SerialProtocol_GPS,1)==&m.state[4]);
 m.state[6].protocol.value=-1;
 assert(m.find_protocol_instance(AP_SerialManager::SerialProtocol_GPS,0)==&m.state[4]);
 assert(m.find_protocol_instance(AP_SerialManager::SerialProtocol_GPS,1)==nullptr);
 m.state[6].protocol.value=5;m.state[4].protocol.value=-1;
 assert(m.find_protocol_instance(AP_SerialManager::SerialProtocol_GPS,0)==&m.state[6]);
 m.state[1].protocol.value=2;m.state[5].protocol.value=2;
 assert(m.find_protocol_instance(AP_SerialManager::SerialProtocol_MAVLink2,0)==&m.state[1]);
 assert(m.find_protocol_instance(AP_SerialManager::SerialProtocol_MAVLink2,1)==&m.state[5]);
 m.state[0].protocol.value=5;m.state[4].protocol.value=5;
 assert(m.find_protocol_instance(AP_SerialManager::SerialProtocol_GPS,2)==&m.state[0]);
 puts("10 assertions passed on the actual native lookup function");
}
'''
with tempfile.TemporaryDirectory(prefix='ecf-gps-order-') as work:
 src=Path(work)/'test.cpp';exe=Path(work)/'test'
 src.write_text(fixture+function+checks)
 subprocess.run(['g++','-std=c++11','-Wall','-Wextra','-Werror',str(src),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
