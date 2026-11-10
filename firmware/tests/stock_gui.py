"""Offline execution of stock LVGL/page/config logic, with explicit hardware boundaries."""
from pathlib import Path
import struct
import sys
import re
import io
import hashlib
sys.path.insert(0,str(Path(__file__).parents[1]))

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
from unicorn import UC_HOOK_MEM_WRITE, UC_HOOK_MEM_INVALID
from unicorn.arm_const import (UC_CPU_ARM_CORTEX_M7, UC_ARM_REG_FPEXC, UC_ARM_REG_PC,
    UC_ARM_REG_LR, UC_ARM_REG_SP, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)

from emulator import Machine, BUILD
from make_patch import BACKUP, check_input

SAVE_CHAIN = (0x179d8,0x17a1c,0x17a4c,0x1788c,0x178b4,0x8fd0,0x8f68,0x8fb0,0x90e8)
WATCH = ((0x2000466d,0x2000466f),(0x20004434,0x20004454),
         (0x20004689,0x2000468b),(0x200045d6,0x200045db))


def config_fixture():
    """Match the observed last valid record/append position, without private dumps."""
    data=bytearray(b'\0'*0x420+b'\xff'*(4096-0x420))
    data[0x420:0x440]=bytes.fromhex('55aaaa5500000000000000000000000000000805000364550000000000000000')
    return bytes(data)


class StockGUI(Machine):
    def __init__(self, variant='dev', build_dir=None, captured_config=None, page=None):
        self.native = False; self.dma_pending=False
        self.trace = []; self.covered = set(); self.shadow = []; self.recent = []
        self.key_messages=[]; self.key_capacity=0
        self.decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB); self.ins_cache={}
        rows = (Path(__file__).parents[1]/'analysis/functions.tsv').read_text().splitlines()[1:]
        self.entries = sorted(int(r.split('\t')[0],16) for r in rows)
        if build_dir is not None:
            expected={'v1':'baee341bd20d76f655764ec10ec44d1a5d7b5d550af596ee3e0299bf32cc8f8f',
                      'v2_failed':'3655f260054814c55e286908edf44f38c2ee9345e039d85209adc5d53664049d'}
            if Path(build_dir).name in expected:
                assert hashlib.sha256((Path(build_dir)/f'neos75_rt_{variant}.bin').read_bytes()).hexdigest()==expected[Path(build_dir).name]
        super().__init__(variant,cpu_model=UC_CPU_ARM_CORTEX_M7,build_dir=build_dir)
        self.native = True
        self.uc.mem_map(0xe000e000,0x2000)
        self.uc.mem_map(0x4000c000,0x1000)
        self.uc.mem_map(0x4000b000,0x1000)
        self.uc.mem_map(0x70000000,0x800000)
        self.uc.mem_write(0xe000ed88,struct.pack('<I',0xf << 20))
        self.uc.reg_write(UC_ARM_REG_FPEXC,0x40000000)
        self.stubs = {0x32034:self.send,0x11dbc:lambda:0,0x2307c:lambda:0,
                      0x8fd0:lambda:0,0x8fb0:lambda:0,0x8f68:self.erase,
                      0x90e8:self.program,0x30ee4:self.jpeg_wait,
                      0x2f63c:self.key_create,0x2f680:self.key_put,0x2f560:self.key_get}
        self.uc.hook_add(UC_HOOK_MEM_WRITE,self.write_trace)
        self.uc.hook_add(UC_HOOK_MEM_INVALID,self.invalid)
        original=BACKUP.read_bytes(); check_input(original)
        self.original=original
        # Execute the installed scatter-load routines, including compressed data
        # and RAM-resident libc. Zero RAM would silently execute bogus helpers.
        for i, address in enumerate(range(0x4843c,0x484ac,16)):
            src,dst,n,handler=struct.unpack_from('<4I',original,address-0x8000)
            self.symbols[f'scatter_{i}']=handler
            self.call(f'scatter_{i}',src,dst,n)
        self.call('rt_init')
        data=bytearray(Path(captured_config).read_bytes() if captured_config else config_fixture())
        if page is not None: data[0x424+0xf]=page
        data=bytes(data)
        self.uc.mem_write(0x7f000,data)
        record=next(data[i:i+32] for i in range(0,4096,32) if data[i:i+4]==bytes.fromhex('55aaaa55'))
        self.uc.mem_write(0x20004434,record)
        self.uc.mem_write(0x2000466d,record[4+0x11:4+0x13])
        self.page_index=record[4+0xf]
        self.symbols.update(stock_init=0x25380,stock_screen=0x26dd4,stock_img=0x2412c,
                            stock_load=0x2b120,original_timer=0x3423c,
                            driver_init=0x2067c,buffer_init=0x2065c,display_register=0x206c4,
                            key_init=0x1cce8,key_push=0x1cd4c,key_read=0x1d184)
        self.call('key_init')

    def bootstrap(self):
        self.call('stock_init')
        # Deterministic read-only resource fixture; original M: file and image
        # decoders still perform lookup/open/read/close (no captured NOR image).
        names=set()
        for name in re.findall(rb'[MS]:/[^\0\r\n]{1,63}',self.original):
            names.update((name[2:],name[3:]))
        from PIL import Image
        encoded=io.BytesIO(); Image.new('RGB',(480,360),'red').save(encoded,'JPEG',quality=75)
        jpg=encoded.getvalue()
        header=bytearray(0x44+len(names)*0x4c)
        struct.pack_into('<I',header,0x40,len(names))
        for i,name in enumerate(sorted(names)):
            off=0x44+i*0x4c
            struct.pack_into('<III',header,off,0x10000,0,len(jpg))
            header[off+12:off+12+len(name)]=name
        self.uc.mem_write(0x80010000,bytes(header))
        self.uc.mem_write(0x2000bd0c,struct.pack('<I',0x80010000))
        self.uc.mem_write(0x2000bd10,struct.pack('<I',0x80010000))
        self.uc.mem_write(0x200045f4,struct.pack('<I',0x70090000))
        self.uc.mem_write(0x200045f8,struct.pack('<I',0x700b0000))
        self.uc.mem_write(0x700a0000,jpg)
        self.uc.mem_write(0x700c0000,jpg)
        persisted=io.BytesIO(); Image.new('RGB',(320,320),'red').save(persisted,'JPEG',quality=75)
        body=persisted.getvalue()
        self.uc.mem_write(0x70001000,b'UB'+struct.pack('<HHH',320,320,1)+b'\0'+struct.pack('>I',len(body))+body)
        self.call('driver_init',0x80001000)
        # Clip the virtual panel, not the application's 480x360 object geometry;
        # software pixel blending is irrelevant to configuration provenance.
        self.uc.mem_write(0x80001000,struct.pack('<HH',64,48))
        self.call('buffer_init',0x80001100,0x80020000,0,64*48)
        self.uc.mem_write(0x8000100c,struct.pack('<I',0x80001100))
        # A virtual panel completes flush synchronously using stock flush_ready.
        self.uc.mem_write(0x80001014,struct.pack('<I',0x20825))
        self.display=self.call('display_register',0x80001000)
        self.symbols.update(app_gui_boot=0xecc0,timer_handler=0x2c648)
        self.uc.mem_write(0x20000004,struct.pack('<I',0x5a5a5a5a))
        self.call('app_gui_boot',0x2000349c)
        global_root,create,load=struct.unpack('<III',self.uc.mem_read(0x44848+self.page_index*12,12))
        self.symbols['page_create']=create; self.symbols['page_load']=load
        self.page=struct.unpack('<I',self.uc.mem_read(global_root,4))[0]

    def tick(self,tick):
        for a in (0x2000bfcc,0x20003a54,0x20003a58): self.uc.mem_write(a,struct.pack('<I',tick))
        self.call('timer_handler')

    def key_create(self):
        self.key_capacity=self.uc.reg_read(UC_ARM_REG_R0)
        assert self.uc.reg_read(UC_ARM_REG_R1)==4
        return 0x2000d000

    def key_put(self):
        assert self.uc.reg_read(UC_ARM_REG_R0)==0x2000d000
        if len(self.key_messages)>=self.key_capacity: return 0xfffffffd
        self.key_messages.append(bytes(self.uc.mem_read(self.uc.reg_read(UC_ARM_REG_R1),4)))
        return 0

    def key_get(self):
        assert self.uc.reg_read(UC_ARM_REG_R0)==0x2000d000
        if not self.key_messages: return 0xfffffffd
        self.uc.mem_write(self.uc.reg_read(UC_ARM_REG_R1),self.key_messages.pop(0))
        return 0

    def inject_knob(self,direction):
        assert direction in (-1,1)
        # Execute the actual producer; LVGL's real read callback consumes later.
        self.call('key_push',8,7 if direction==1 else 8)

    def gui(self):
        while self.key_messages: self.call('key_read',0,0x2000dc00)
        super().gui()

    def advance(self,milliseconds):
        super().advance(milliseconds)
        self.call('timer_handler')

    def telemetry(self,mode):
        payload=bytearray(self.uc.mem_read(0x20004660,26)); payload[13]=mode
        report=bytearray([0xa5,0x35,0,26])+payload
        report.append((~sum(report[1:]))&255)
        self.uc.mem_write(0x2000e000,bytes(report).ljust(64,b'\0'))
        self.uc.mem_write(0x2000c118,struct.pack('<I',0xc))
        self.symbols['dispatch']=0x35220
        return self.call('dispatch',0x2000e000,64)

    def invalid(self,uc,access,address,size,value,data):
        self.trace.append(dict(kind='unmodelled_memory',address=hex(address),pc=hex(uc.reg_read(UC_ARM_REG_PC))))
        return False

    def write_trace(self,uc,access,address,size,value,data):
        if address==0x4000c008 and value&1: self.dma_pending=True
        if any(address<end and address+size>start for start,end in WATCH):
            self.trace.append(dict(kind='ram_write',address=hex(address),size=size,value=hex(value),
                                   before=bytes(uc.mem_read(address,size)).hex(),
                                   pc=hex(uc.reg_read(UC_ARM_REG_PC)),
                                   lr=hex(uc.reg_read(UC_ARM_REG_LR)),sp=hex(uc.reg_read(UC_ARM_REG_SP)),
                                   stack=self.shadow.copy()))

    def hook(self,uc,address,size,data):
        if not self.native:
            return super().hook(uc,address,size,data)
        if self.dma_pending:
            self.dma_pending=False
            read=lambda off:struct.unpack('<I',uc.mem_read(0x4000c008+off,4))[0]
            control=read(0); n=((read(0x38)>>16)+1)*2; dst=read(0x28)
            if control&0x300==0x300: payload=struct.pack('<H',read(0x34)&65535)*(n//2)
            else: payload=bytes(uc.mem_read(read(8),n))
            for start,end in WATCH:
                lo,hi=max(start,dst),min(end,dst+n)
                for a in range(lo,hi):
                    self.write_trace(uc,0,a,1,payload[a-dst],None)
            uc.mem_write(dst,payload)
            uc.mem_write(0x4000c008,struct.pack('<I',control&~1))
        if address==0x7e000: uc.emu_stop(); return
        while self.shadow and self.shadow[-1]['return']==address:
            self.shadow.pop()
        self.recent.append(hex(address)); self.recent=self.recent[-40:]
        if address in (*SAVE_CHAIN,0x33fa4):
            self.trace.append(dict(kind='config_poll' if address==0x33fa4 else 'config_call',address=hex(address),
                args=[hex(uc.reg_read(r)) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2)],
                lr=hex(uc.reg_read(UC_ARM_REG_LR)),sp=hex(uc.reg_read(UC_ARM_REG_SP)),
                stack=self.shadow.copy()))
        if address in (0x24e78,0x235e4):
            ptr=uc.reg_read(UC_ARM_REG_R1)
            if ptr:
                self.trace.append(dict(kind='image_source',address=hex(address),
                                       value=bytes(uc.mem_read(ptr,40)).split(b'\0')[0].hex()))
        self.covered.add(address)
        if address in self.stubs:
            value=self.stubs[address]()
            uc.reg_write(UC_ARM_REG_R0,value)
            uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR)); return
        if address not in self.ins_cache:
            self.ins_cache[address]=next(self.decoder.disasm(bytes(uc.mem_read(address,size)),address),None)
        ins=self.ins_cache[address]
        if ins and ins.mnemonic in ('bl','blx'):
            self.shadow.append({'call':hex(address),'target':ins.op_str,'return':address+size})

    def erase(self):
        self.uc.mem_write(self.uc.reg_read(UC_ARM_REG_R0),b'\xff'*4096); return 0

    def jpeg_wait(self):
        if self.uc.reg_read(UC_ARM_REG_R1):
            read=lambda a:struct.unpack('<I',self.uc.mem_read(a,4))[0]
            dims=read(0x4000b010); dst=read(0x4000b01c)
            n=((dims&65535)+1)*((dims>>16)+1)*2
            assert 0<n<=0x7e900
            self.uc.mem_write(dst,b'\x00\xf8'*(n//2))
            self.uc.mem_write(0x20003662,b'\1')
            return 1
        return 0

    def program(self):
        dst,src,n=(self.uc.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2))
        assert 0x7f000<=dst<dst+n*4<=0x80000
        old=self.uc.mem_read(dst,n*4); new=self.uc.mem_read(src,n*4)
        assert all(a&b==b for a,b in zip(old,new))
        self.uc.mem_write(dst,bytes(new)); return 0

    def call(self,name,*args,**kwargs):
        self.shadow=[dict(entry=name,address=hex(self.symbols[name]),**{'return':0x7e000})]
        return super().call(name,*args,**kwargs)

