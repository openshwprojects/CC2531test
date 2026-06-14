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

def send_znp(s, cmd0, cmd1, data):
    length = len(data)
    packet = bytes([0xFE, length, cmd0, cmd1]) + data
    packet += bytes([calc_fcs(packet[1:])])
    s.write(packet)

def send_time_sync(s, dst_addr, seq):
    print(f"[*] Sending Time Sync Response to 0x{dst_addr:04X}...", flush=True)
    now = int(time.time())
    
    # Tuya expects 4 bytes UTC, 4 bytes Local (we use current time for both)
    utc_bytes = struct.pack('>I', now)
    local_bytes = struct.pack('>I', now)
    
    # ZCL Payload: FC(0x09), Seq, Cmd(0x24), Len(0x00 0x08), Data(8)
    zcl = bytes([0x09, seq, 0x24, 0x00, 0x08]) + utc_bytes + local_bytes
    
    # AF_DATA_REQUEST: DstAddr(2), DstEp(1), SrcEp(1), Cluster(2), TransId(1), Options(1), Radius(1), Len(1), Data(N)
    af_data = struct.pack('<HBBHBBBB', dst_addr, 1, 1, 0xEF00, 0, 0x00, 0x0F, len(zcl)) + zcl
    send_znp(s, 0x24, 0x01, af_data)

def parse_tuya_dp(zcl_data):
    if len(zcl_data) < 5: return
    cmd_id = zcl_data[2]
    # Command 0x01 = Data Request, 0x02 = Data Report
    if cmd_id in (1, 2):  
        payload = zcl_data[5:]
        idx = 0
        while idx < len(payload):
            if idx + 4 > len(payload): break
            dp_id = payload[idx]
            dp_type = payload[idx+1]
            dp_len = struct.unpack('>H', payload[idx+2:idx+4])[0]
            idx += 4
            if idx + dp_len > len(payload): break
            dp_val_raw = payload[idx:idx+dp_len]
            idx += dp_len
            
            val = dp_val_raw.hex()
            if dp_type == 1: 
                val = f"{dp_val_raw[0] != 0}"
            elif dp_type == 2: 
                # Big-endian 4-byte integer (e.g. Temperature, Target Temp)
                int_val = struct.unpack('>I', dp_val_raw)[0]
                val = f"{int_val} (Raw Value)"
            elif dp_type == 4: 
                val = f"Enum({dp_val_raw[0]})"
            
            print(f"   -> [Tuya Data] DP ID: {dp_id} | Type: {dp_type} | Value: {val}", flush=True)

def parse_af_incoming(s, data):
    if len(data) < 17: return
    cluster_id = struct.unpack('<H', data[2:4])[0]
    src_addr = struct.unpack('<H', data[4:6])[0]
    zcl_len = data[16]
    zcl_data = data[17:17+zcl_len]
    
    print("\n" + "="*60)
    print(f"📦 INCOMING ZIGBEE PACKET!")
    print(f"📍 Device Network Addr: 0x{src_addr:04X}")
    print(f"🎯 Zigbee Cluster ID  : 0x{cluster_id:04X}")
    print(f"📄 Raw Data (Hex)     : {zcl_data.hex()}")
    
    if cluster_id == 0xEF00 and len(zcl_data) >= 3:
        seq = zcl_data[1]
        cmd = zcl_data[2]
        if cmd == 0x24:
            print("   -> 🕒 Thermostat is asking for Time Sync!", flush=True)
            send_time_sync(s, src_addr, seq)
        else:
            parse_tuya_dp(zcl_data)
            
    print("="*60, flush=True)

def listen_znp(port='COM8', baud=115200):
    try:
        s = serial.Serial(port, baud, timeout=1, rtscts=True)
        
        print(f"[*] Registering Endpoint 1 to receive Thermostat data...", flush=True)
        s.write(b'\xFE\x09\x24\x00\x01\x04\x01\x00\x01\x01\x00\x00\x00\x29')
        time.sleep(1)

        print(f"[*] Waking up Coordinator...", flush=True)
        s.write(b'\xFE\x01\x25\x40\x00\x64')
        time.sleep(2)

        print(f"[*] Opening network for Pairing (Permit Join) for 60 seconds...", flush=True)
        s.write(b'\xFE\x04\x25\x36\x00\x00\x3C\x00\x2B')
        time.sleep(0.5)
        
        print(f"\n[*] READY! Waiting for Tuya data...")
        print(f"[*] (Press Ctrl+C to stop)\n", flush=True)
        
        buffer = bytearray()
        while True:
            try:
                # Read whatever is available in the OS buffer (drastically faster)
                waiting = s.in_waiting or 1
                chunk = s.read(waiting)
                if chunk:
                    buffer.extend(chunk)
                
                # Parse all complete packets in our buffer
                while len(buffer) > 0:
                    if buffer[0] != 0xFE:
                        buffer.pop(0)
                        continue
                        
                    if len(buffer) < 2:
                        break # Need length byte
                        
                    length = buffer[1]
                    if len(buffer) < length + 5:
                        break # Need rest of packet (Cmd0, Cmd1, Data, FCS)
                        
                    # Extract the full packet
                    packet = buffer[:length+5]
                    buffer = buffer[length+5:]
                    
                    cmd0, cmd1 = packet[2], packet[3]
                    data = packet[4:-1]
                    fcs = packet[-1]
                    
                    if fcs != calc_fcs(packet[1:-1]):
                        print(f"[!] Bad FCS on packet: {packet.hex()}", flush=True)
                        continue
                        
                    if (cmd0, cmd1) == AF_INCOMING_MSG:
                        parse_af_incoming(s, data)
                    else:
                        print(f"[ZNP] Cmd: {cmd0:02X}:{cmd1:02X} | Length: {length} | Data: {data.hex()}", flush=True)
                        
            except serial.SerialException as e:
                print(f"\n[!] USB Disconnected or Crashed: {e}")
                print(f"[*] Attempting to reconnect in 3 seconds...", flush=True)
                time.sleep(3)
                break # Break to outer try/except or just let it restart? Actually, let's just raise so it stops cleanly.
                raise

                
    except KeyboardInterrupt:
        print("\n[*] Stopped.", flush=True)
    except Exception as e:
        print(f"[!] Error: {e}", flush=True)

if __name__ == '__main__':
    listen_znp()
