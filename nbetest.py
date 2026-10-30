#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import logging
import random
import select
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from random import SystemRandom, randrange
try:
    from Crypto.PublicKey import RSA
except ImportError:
    raise SystemExit("Missing dependency: install with python -m pip install pycryptodome")


PASSWORD = ""
SERIAL_NUMBER = ""
IP_ADDRESS = ""
PORT = 8483

START = b"\x02"
END = b"\x04"
STATUS_CODES = (0, 1, 2, 3)
FUNCTION_CODES = tuple(range(12))
_LOGGER = logging.getLogger(__name__)

class Request_frame(object):
    def __init__(self):
        self.REQUEST_HEADER_SIZE = 52
        self.appid = 'NBEConnect_'
        self.controllerid = ''.join([random.choice('0123456789') for a in range(10)])
        
        self.encrypted = False
        self.sequencenumber = 1
        self.pincode = '0123456789'
        self.payload = ''
        self.payloadsize = len(self.payload)
        self.function = 0

    def encode(self):
        while True:
            success = True
            self.framedata = ('%12s'%self.appid[:12]).encode('ascii')
            self.framedata += ('%06d'%int(self.controllerid[:6])).encode('ascii')

            if self.encrypted:
                if hasattr(self, 'xtea_key'):
                    #print ('xtea encrypt 1')
                    self.framedata += ('%1s'%'-').encode('ascii')
                else:
                    self.framedata += ('%1s'%'*').encode('ascii')
            else:
                self.framedata += ('%1s'%' ').encode('ascii')

            h = START;
            if self.function not in FUNCTION_CODES:
                raise IOError
            h += ('%02u'%self.function).encode('ascii')
            h += ('%02d'%self.sequencenumber).encode('ascii')

            if self.encrypted:
                h += ('%10s'%self.pincode[:10]).encode('ascii')
            else:
                h += '0000000000'.encode('ascii')

            h += ('%10s'%int(time.time())).encode('ascii')
            h += ('%4s'%'pad ').encode('ascii')
            h += ('%03u'%len(self.payload)).encode('ascii')
            if len(self.payload) > 495:
                raise IOError
            try:
                h += self.payload.encode('ascii')
            except UnicodeError:
                h += self.payload
            h += END;
            if self.encrypted: 
                pad = b''.join([bytes(chr(SystemRandom().randrange(128)), 'utf-8') for x in range(64-len(h))])
                h+=pad
                if hasattr(self, 'xtea_key'):
                    h = self.xtea_key.encrypt(h)
                else:
                    h = self.compatencrypt(h)
                    if len(h) != 64:
                        success = False
            self.framedata += h
            if success:
                return self.framedata
            print ('ERROR chipertext wrong length', len(h))
    


    def compatencrypt(self, message):
        pt_int = int.from_bytes(message, 'big')
        ct_int = pow(pt_int, self.public_key.e, self.public_key.n)
        ct = ct_int.to_bytes(self.public_key.size_in_bytes(), 'big')
        return ct.lstrip(b'\x00')
    
    def compatdecrypt(self, message):
        ct_int = int.from_bytes(message, 'big')
        pt_int = pow(ct_int, self.public_key.e, self.public_key.n)
        pt = pt_int.to_bytes(self.public_key.size_in_bytes(), 'big').lstrip(b'\x00')
        return pt

  
    def decode(self, record):
        i = 0
        self.appid = record[i:12]
        i+=12
        self.controllerid = record[i:6]
        i+=6
        self.encryption = record[i:1]
        i+=1
        if not record[i] == START[0]:
            raise IOError
        if len(record) < 17:
            raise IOError
        i += 1
        self.function = int(record[i:i+2])
        i += 2
        self.sequencenumber = int(record[i:i+2])
        i += 2
        self.pincode = record[i:i+10].decode('ascii')
        i += 10
        self.timestamp = int(record[i:i+10].decode('ascii'))
        i += 10
        self.extra = record[i:i+4].decode('ascii')
        i += 4
        self.payloadsize = int(record[i:i+3])
        i += 3
        if len(record) < self.payloadsize + self.REQUEST_HEADER_SIZE:
            raise IOError
        self.payload = record[i:i+self.payloadsize]
        i += self.payloadsize
        if not record[i] == END[0]:
            raise IOError

class Response_frame(object):
    def __init__(self, request):
        self.RESPONSE_HEADER_SIZE = 28
        self.request = request

    def encode(self):
        self.framedata = ('%12s'%self.request.appid).encode('ascii')
        self.framedata += ('%6s'%self.request.controllerid).encode('ascii')
        self.framedata += START;
        if int(self.function) > 13:
            raise IOError
        self.framedata += ('%02u'%self.function).encode('ascii')
        if self.status not in STATUS_CODES:
            raise IOError
        self.framedata += ('%2d'%self.request.sequencenumber).encode('ascii')
        self.framedata += ('%1s'%self.status).encode('ascii')
        if len(self.payload) > 1007:
            raise IOError
        self.framedata += ('%03u'%len(self.payload)).encode('ascii')
        self.framedata += self.payload.encode('ascii')
        self.framedata += END;
        return self.framedata

    def decode(self, record):
        self.framedata = record
        i = 0
        self.appid = record[i:i+12]
        i+=12
        self.controllerid = record[i:i+6]
        i+=6
        #import pdb;pdb.set_trace()i
        if not record[i] == START[0]:
            raise IOError
        if len(record) < self.RESPONSE_HEADER_SIZE:
            raise IOError
        i += 1
        self.function = int(record[i:i+2])
        i += 2
        self.sequencenumber = int(record[i:i+2])
        if self.sequencenumber != self.request.sequencenumber:
            raise IOError
        i += 2
        self.status = int(record[i:i+1])
        i += 1
        self.size = int(record[i:i+3])
        i += 3
        if not len(record) == self.size + self.RESPONSE_HEADER_SIZE:
            print(f"Invalid response lenght {len(record)}")

        self.payload = (record[i:i+self.size]).decode('utf-8', errors='ignore')

        i += self.size
        if not record[i] == END[0]:
            print(f"Invalid response END")

    def parse_payload(self):
        frame = self.payload
        d = {}
        for item in frame.split(';'):
            name, value = item.split('=')
            d[name] = value
        return d

class Proxy:
    root = ('settings', 'operating_data', 'advanced_data', 'consumption_data', 'event_log','sw_versions','info')
    settings = ('boiler', 'hot_water', 'regulation', 'weather', 'weather2', 'oxygen', 'cleaning', 'hopper', 'fan', 'auger', 'ignition', 'pump', 
        'sun', 'vacuum', 'misc', 'alarm', 'manual')
    consumption_data = ('total_hours', 'total_days', 'total_months', 'total_years', 'dhw_hours', 'dhw_days', 'dhw_months', 'dhw_years', 'counter')

    def __init__(self, password, port=8483, addr=None, serialnumber=None):
        self.password = password
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for p in range(port+1, 9999):
            try:
                s.bind(('', p))
                break
            except socket.error:
                if p==9999:
                    print ('No free port found')
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if addr == '<broadcast>':
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(7)
        self.s = s
        request = Request_frame()
        self.response = Response_frame(request)

        request.function = 0
        request.payload = 'NBE Discovery'
        if serialnumber:
            request.controllerid = serialnumber
        request.sequencenumber = randrange(0,100)
        self.s.sendto(request.encode() , (addr, port))
        data, server = self.s.recvfrom(4096)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 0)
        self.addr = server

        self.response.decode(data)
        res = self.response.parse_payload()
        if 'Serial' in res:
            self.serial = res['Serial']
        if 'IP' in res:
            self.ip = res['IP']

        request.payload = 'misc.rsa_key'
        request.function = 1
        request.sequencenumber += 1
        self.s.sendto(request.encode() , self.addr)
        data, server = self.s.recvfrom(4096)
        self.response.decode(data)
        try:
            key = self.response.payload.split('rsa_key=')[1]
            key = base64.b64decode(key)
            request.public_key = RSA.importKey(key)
        except (ValueError, IndexError, TypeError, IOError):
            request.public_key = None
        request.pincode = self.password
        self.request = request

    def get(self, d=None):
        d = d.rstrip('/').split('/')
        if d[0] == None or d[0] == '*':
            return [p + '/' for p in self.root]
        elif d[0] == 'settings':
            if len(d) == 1:
                return ['settings/%s/'%s for s in self.settings]
            elif d[1] in self.settings:
                if len(d) == 2:
                    response = self.make_request(1, d[1] + '.*')
                    return ['settings/%s/%s'%(d[1], s) for s in response.payload.split(';')]
                else:
                    response = self.make_request(1, d[1] + '.' + d[2])
                    try:
                        return (response.payload.split('=', 1)[1],)
                    except IndexError:
                        return (response.payload,)
            else:
                return []
        elif d[0] in ('operating_data', 'advanced_data'):
            if d[0] == 'operating_data':
                f = 4
            else:
                f = 5
            if len(d) == 1:
                response = self.make_request(f, '*')
                return [d[0] + '/' + s for s in response.payload.split(';')]
            elif len(d) == 2:
                response = self.make_request(f, d[1])
                try:
                    return (response.payload.split('=')[1],)
                except IndexError:
                    return (response.payload,)
        elif d[0] == 'consumption_data':
            if len(d) == 1:
                return [d[0] + '/' + s for s in self.consumption_data]
            elif d[1] in self.consumption_data:
                response = self.make_request(6, d[1])
                return [d[0] + '/' + s for s in response.payload.split(';')]
            else:
                return []
        elif d[0] == 'sw_versions':
            if len(d) == 1:
                response = self.make_request(10, '')
                return [s for s in response.payload.split(';')]
            else:
                return []
        elif d[0] == 'info':
            if len(d) == 1:
                response = self.make_request(9, '')
                return [s for s in response.payload.split(';')]
            else:
                return []
        elif d[0] == 'event_log':
            if len(d) == 1:
                now = time.strftime('%y%m%d:%H%M%S;',time.localtime())
                response = self.make_request(8, now)
                return response.payload.split(';')
            else:
                response = self.make_request(8, d[1])
                return response.payload.split(';')

    @classmethod
    def discover(cls, password, port, serialnumber):
        return cls(password, port, addr='<broadcast>', serialnumber=serialnumber)

    def make_request(self, function, payload, encrypt=False, key=None):
        self.request.sequencenumber = (self.request.sequencenumber % 99) + 1
        self.request.payload = payload
        self.request.function = function
        self.request.encrypted = encrypt
        self.request.pincode = self.password
        _LOGGER.debug("Making request to boiler. function=%s, sequence=%s", function, self.request.sequencenumber)
        ready_to_read, _, _ = select.select([self.s], [], [], 0)

        if self.s in ready_to_read:
            try:
                while True:
                    dataDiscard, serverDiscard = self.s.recvfrom(4096)
                    if not dataDiscard:
                        break
            except socket.error:
                pass 

        self.s.sendto(self.request.encode(), self.addr)
        data, server = self.s.recvfrom(4096)
        self.response.decode(data)
        return self.response

    def __enter__(self):
        return self

    def __exit__(self, password, port=8483, addr=None, serialnumber=None):
        self.s.close()

MAIN_ENDPOINTS = [
    "operating_data/", "advanced_data/", "consumption_data/",
    "settings/", "event_log/", "sw_versions/", "info/",
]

SETTINGS_ENDPOINTS = [
    "settings/boiler/", "settings/hot_water/", "settings/regulation/",
    "settings/weather/", "settings/weather2/", "settings/oxygen/",
    "settings/cleaning/", "settings/hopper/", "settings/fan/",
    "settings/auger/", "settings/ignition/", "settings/pump/",
    "settings/sun/", "settings/vacuum/", "settings/misc/",
    "settings/alarm/", "settings/manual/",
]


def main():
    log_path = Path("nbe_diagnose_log.txt")
    dump_path = Path("komplet_fyr_dump.txt")
    logging.basicConfig(level=logging.WARNING)

    def log(message):
        line = f"{datetime.now().isoformat(timespec='seconds')} | {message}"
        print(line)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    log_path.write_text("NBE UDP DIAGNOSE - read-only\n", encoding="utf-8")
    if not PASSWORD or (not SERIAL_NUMBER and not IP_ADDRESS):
        log("CONFIG ERROR: Enter PASSWORD and SERIAL_NUMBER, or PASSWORD and IP_ADDRESS.")
        return 2

    if not IP_ADDRESS and not SERIAL_NUMBER.isdigit():
        log("CONFIG ERROR: SERIAL_NUMBER must contain digits only.")
        return 2

    mode = "direct IP" if IP_ADDRESS else "broadcast discovery"
    log(f"START | mode={mode} | port={PORT}")
    if IP_ADDRESS:
        log(f"Target IP: {IP_ADDRESS}")
    else:
        log("Target: local broadcast (IP_ADDRESS not configured)")

    proxy = None
    try:
        log("Connecting: sending NBE Discovery (function 0), then requesting RSA key (function 1)")
        if IP_ADDRESS:
            proxy = Proxy(password=PASSWORD, port=PORT, addr=IP_ADDRESS, serialnumber=None)
        else:
            proxy = Proxy.discover(password=PASSWORD, port=PORT, serialnumber=SERIAL_NUMBER)
        log(f"Connection established | responding IP={proxy.addr[0]} | port={proxy.addr[1]}")
    except Exception as exc:
        log(f"CONNECTION FAILED | {type(exc).__name__}: {exc}")
        log("No dump created. If using broadcast, retry with IP_ADDRESS and PASSWORD.")
        log("For controlled testing, connect boiler and computer by cable to the same LAN switch.")
        return 1

    all_data = {}
    failed = []
    try:
        for endpoint in MAIN_ENDPOINTS + SETTINGS_ENDPOINTS:
            log(f"GET {endpoint}")
            try:
                data = proxy.get(endpoint)
                if data:
                    all_data[endpoint] = data
                    log(f"OK {endpoint} | {len(data)} items")
                else:
                    log(f"EMPTY {endpoint}")
            except Exception as exc:
                failed.append(endpoint)
                log(f"FAILED {endpoint} | {type(exc).__name__}: {exc}")
    finally:
        proxy.s.close()

    with dump_path.open("w", encoding="utf-8") as f:
        f.write("KOMPLET NBE FYR DATA DUMP\n")
        f.write("=" * 80 + "\n\n")
        for endpoint, data in sorted(all_data.items()):
            f.write(f"\n{'=' * 80}\n")
            f.write(f"ENDPOINT: {endpoint}\n")
            f.write(f"{'=' * 80}\n")
            for item in data:
                f.write(f"{item}\n")
        f.write(f"\n\n{'=' * 80}\n")
        f.write(f"TOTAL: {sum(len(d) for d in all_data.values())} datapunkter fra {len(all_data)} endpoints\n")
        f.write(f"{'=' * 80}\n")

    log(f"DUMP SAVED: {dump_path} | {sum(map(len, all_data.values()))} data points | {len(all_data)} endpoints")
    log(f"FINISHED | failed endpoints={len(failed)}")
    if failed:
        log("Failed endpoints: " + ", ".join(failed))
    log("IMPORTANT: Review dump for personal/device data before uploading it publicly.")
    return 0 if not failed else 3


if __name__ == "__main__":
    sys.exit(main())
