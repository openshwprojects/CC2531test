import serial
import time
import struct
import sys

AF_INCOMING_MSG = (0x44, 0x81)

def calc_fcs(data):
    fcs = 0
    for byte in data:
        fcs ^= byte
    return fcs

def listen_znp(port='COM8', baud=115200):
    try:
        s = serial.Serial(port, baud, timeout=1, rtscts=True)
        # Send AF_REGISTER for Endpoint 1, Profile 0x0104 (Home Automation) FIRST
        # If we send this after startup, the CC2531 can crash and reboot (Cmd 41:80).
        print(f"[*] Registering Endpoint 1 to receive Thermostat data...", flush=True)
        s.write(b'\xFE\x09\x24\x00\x01\x04\x01\x00\x01\x01\x00\x00\x00\x29')
        time.sleep(1)

        # Send ZDO_STARTUP_FROM_APP (tells the stick to resume its saved network)
        # FE 01 25 40 00 64
        print(f"[*] Waking up Coordinator...", flush=True)
        s.write(b'\xFE\x01\x25\x40\x00\x64')
        time.sleep(2)

        # Send ZDO_MGMT_PERMIT_JOIN_REQ (open network for 60 seconds)
        print(f"[*] Opening network for Pairing (Permit Join) for 60 seconds...", flush=True)
        s.write(b'\xFE\x04\x25\x36\x00\x00\x3C\x00\x2B')
        time.sleep(0.5)
        
        print(f"\n[*] READY! The script is fully listening.")
        print(f"[*] 1. If it's already paired, just press a button on the thermostat.")
        print(f"[*] 2. If it's not paired, put it in PAIRING MODE now.")
        print(f"[*] (Press Ctrl+C to stop)\n", flush=True)
        
        while True:
            sof = s.read(1)
            if not sof: continue
            if sof[0] != 0xFE: continue
            
            length_b = s.read(1)
            if not length_b: continue
            length = length_b[0]
            
            cmds = s.read(2)
            if len(cmds) < 2: continue
            cmd0, cmd1 = cmds[0], cmds[1]
            
            data = s.read(length) if length > 0 else b''
            
            fcs_b = s.read(1)
            if not fcs_b: continue
            fcs = fcs_b[0]
            
            fcs_calc = calc_fcs(length_b + cmds + data)
            if fcs != fcs_calc: continue
            
            if (cmd0, cmd1) == AF_INCOMING_MSG:
                parse_af_incoming(data)
            else:
                # Print ALL other ZNP traffic to see pairing attempts!
                print(f"[ZNP] Cmd: {cmd0:02X}:{cmd1:02X} | Length: {length} | Data: {data.hex()}", flush=True)
                
    except KeyboardInterrupt:
        print("\n[*] Stopped.", flush=True)
    except Exception as e:
        print(f"[!] Error: {e}", flush=True)

def parse_af_incoming(data):
    if len(data) < 17: return
    cluster_id = struct.unpack('<H', data[2:4])[0]
    src_addr = struct.unpack('<H', data[4:6])[0]
    zcl_len = data[16]
    zcl_data = data[17:17+zcl_len]
    
    print("\n" + "="*60)
    print(f"📦 INCOMING ZIGBEE PACKET!")
    print(f"📍 Device Network Addr: 0x{src_addr:04X}")
    print(f"🎯 Zigbee Cluster ID  : 0x{cluster_id:04X} (identifies if it's Temp, HVAC, etc.)")
    print(f"📄 Raw Data (Hex)     : {zcl_data.hex()}")
    print("="*60, flush=True)

if __name__ == '__main__':
    listen_znp()
