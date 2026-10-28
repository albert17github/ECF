"""Read native MSP identity and UID without configuration or actuator writes."""
import argparse
import json
import time
import serial

parser=argparse.ArgumentParser()
parser.add_argument('--port',required=True)
args=parser.parse_args()

def request(port,command):
    port.write(bytes([36,77,60,0,command,command]))
    data=bytearray()
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        data.extend(port.read(max(1,port.in_waiting)))
        start=data.find(b'$M>')
        if start<0 or len(data)<start+6:
            continue
        size=data[start+3]
        end=start+size+6
        if len(data)<end:
            continue
        check=0
        for value in data[start+3:end]:check^=value
        assert check==0 and data[start+4]==command
        return bytes(data[start+5:end-1])
    raise TimeoutError(command)

with serial.Serial(args.port,115200,timeout=.05,write_timeout=3) as port:
    port.reset_input_buffer()
    uid=request(port,160)
    variant=request(port,2).decode()
    version=request(port,3).hex()
    board=request(port,4).hex()
    status=request(port,101)
    print(json.dumps(dict(uid=uid.hex(),variant=variant,version_hex=version,board_info_hex=board,
                          armed=bool(int.from_bytes(status[6:10],'little')&1))))
