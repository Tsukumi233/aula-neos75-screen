"""Run the actual linked Thumb machine code; peripheral calls are explicit stubs."""
import ctypes as c
import json
from pathlib import Path
import struct
import zlib
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC

BUILD=Path(__file__).resolve().parents[1]/'build'
RGB_BASE=0x800fe604
REGION_RGB=RGB_BASE+480*360*2

class Image(c.LittleEndianStructure):
    _fields_=[('header',c.c_uint32),('size',c.c_uint32),('data',c.c_uint32)]

class Runtime(c.LittleEndianStructure):
    _fields_=[(n,c.c_uint32) for n in ('magic','head','tail','overflow','route')]
    _fields_ += [('reports',(c.c_uint8*64)*32),('tx_head',c.c_uint32),('tx_tail',c.c_uint32),
                ('tx',(c.c_uint8*64)*32)]
    _fields_ += [(n,c.c_uint32) for n in ('active','state','type','total','received','crc','block',
        'ok','errors','seq','last_error','decode_ms','width','height','jpeg_len','screen','image',
        'previous','rgb','copy_offset','boot_tick','confirmed','boot_seen','gui_ticks','frame_tick','restoring')]
    _fields_ += [('descriptor',Image)]
    _fields_ += [(n,c.c_uint32) for n in ('armed','host_tick','previous_page','discard','pages','page',
                                          'canvas','region','rx','ry','rw','rh')]
    _fields_ += [('cache_len',c.c_uint32*8),('cache_crc',c.c_uint32*8),('cache_page',c.c_uint32)]
    _fields_ += [('frame_tag',c.c_uint32),('takeover',c.c_uint32),('pass_',c.c_uint32)]

class Machine:
    def __init__(self,variant='dev',cpu_model=None,build_dir=None):
        build_dir=Path(build_dir) if build_dir else BUILD
        meta=json.loads((build_dir/f'neos75_rt_{variant}.json').read_text())
        self.symbols={k:int(v,16) for k,v in meta['symbols'].items()}
        self.uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
        if cpu_model is not None: self.uc.ctl_set_cpu_model(cpu_model)
        self.uc.mem_map(0,0x80000)
        self.uc.mem_map(0x20000000,0x10000)
        self.uc.mem_map(0x80000000,0x200000)
        self.uc.mem_write(0x8000,(build_dir/f'neos75_rt_{variant}.bin').read_bytes())
        self.sent=[]; self.calls=[]; self.call_args=[]; self.decode_success=True
        self.decode_pattern='fixed'   # 'input': pixels derived from the JPEG bytes (which image was decoded)
        self.pending_knobs=[]; self.pending_codes=[]; self.current_screen=0x80003000
        self.objects={}; self.decode_calls=[]; self.invalidated=[]
        self.alloc=0x80060000
        self.stubs={0x2637c:self.allocate,0x26dd4:lambda:0x80001000,
                    0x2412c:lambda:0x80002000,0x2b114:lambda:self.current_screen,
                    0x2a770:lambda:self.object_set('size',2),
                    0x2a6e8:lambda:self.object_set('pos',2),0x2b120:self.load_screen,
                    0x2aa60:self.clear_insets,
                    0x2a744:lambda:self.object_set('scrollbar',1),
                    0x2a9b4:lambda:self.object_set('bg_color',1),
                    0x2a9c4:lambda:self.object_set('bg_gradient',1),
                    0x2a9d4:lambda:self.object_set('bg_opa',1),
                    0x26798:lambda:self.visibility(True),
                    0x26cb8:lambda:self.visibility(False),
                    0x23fa0:lambda:0,0x235d4:self.clear_resource_cookie,
                    0x24e78:lambda:self.object_set('source',1),0x29558:lambda:0,0x295a4:self.invalidate_area,0x32034:self.send,
                    0x1cb88:self.decode,0x2e010:lambda:1,
                    0x2c88c:lambda:self.uc.reg_read(UC_ARM_REG_R0),0x17a1c:lambda:0,
                    0x1d094:self.stock_key,0x33fa4:lambda:0,0x1cd4c:self.key_push}
        self.stubs.update({a:lambda:0 for n,a in self.symbols.items() if n.startswith('stock_')})
        self.uc.hook_add(UC_HOOK_CODE,self.hook)
        self.call('rt_init')
        self.uc.mem_write(0x2000bf77,b'\x01')

    def hook(self,uc,address,size,_):
        if address==0x7e000: uc.emu_stop(); return
        if address in self.stubs:
            self.calls.append(address)
            self.call_args.append((address,*(uc.reg_read(r) for r in
                (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3))))
            value=self.stubs[address]()
            uc.reg_write(UC_ARM_REG_R0,value)
            uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
        elif address<0x4ab00:
            raise AssertionError(f'Unexpected stock/peripheral execution at {address:#x}')

    def allocate(self):
        result=self.alloc; self.alloc+=self.uc.reg_read(UC_ARM_REG_R0)+16; return result
    def load_screen(self):
        self.current_screen=self.uc.reg_read(UC_ARM_REG_R0); return 0
    def stock_key(self):
        # The lightweight harness models only page indices. StockGUI drops this
        # stub and executes the original key, LVGL page and delayed-save code.
        code=self.uc.reg_read(UC_ARM_REG_R0)
        if code in (0x11,0x14,0x12,0x13):
            page=self.uc.mem_read(0x200044ad,1)[0]
            page=(page+(1 if code in (0x12,0x13) else -1))%6
            self.uc.mem_write(0x200044ad,bytes([page]))
            self.current_screen=0x80003000+page*0x100
        return 0
    def object_set(self,key,count):
        obj=self.uc.reg_read(UC_ARM_REG_R0)
        self.objects.setdefault(obj,{})[key]=tuple(self.uc.reg_read(r) for r in
            (UC_ARM_REG_R1,UC_ARM_REG_R2)[:count])
        return 0
    def visibility(self,hidden):
        obj=self.uc.reg_read(UC_ARM_REG_R0)
        if self.uc.reg_read(UC_ARM_REG_R1)&1:
            self.objects.setdefault(obj,{})['hidden']=hidden
        return 0
    def clear_insets(self):
        self.objects.setdefault(self.uc.reg_read(UC_ARM_REG_R0),{})['padding']=(0,0,0,0)
        return 0
    def clear_resource_cookie(self):
        self.uc.mem_write(0x20003b78,bytes(4)); return 0
    def send(self):
        self.sent.append(bytes(self.uc.mem_read(self.uc.reg_read(UC_ARM_REG_R0),64))); return 0
    def decode(self):
        args=tuple(self.uc.reg_read(r) for r in
                   (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3))
        args+=struct.unpack('<II',self.uc.mem_read(self.uc.reg_read(UC_ARM_REG_SP),8))
        self.decode_calls.append(args)
        # Deterministic peripheral output, not a simulation of JPEG hardware.
        if self.decode_success:
            state=self.state()
            if args[3]==REGION_RGB:
                # region: a distinct word per pixel pair (u32 index + 0xA0000000), to check placement
                size=state.rw*state.rh*2
                assert size<=args[4]
                self.uc.mem_write(args[3],b''.join(struct.pack('<I',0xa0000000+i) for i in range(size//4)))
            else:
                if state.active:
                    assert self.objects[state.image]['hidden']
                size=(state.width*state.height or 480*360)*2
                assert size<=args[4]
                if self.decode_pattern=='input':
                    word=struct.pack('<H',zlib.crc32(bytes(self.uc.mem_read(args[0],args[1])))&0xffff)
                    self.uc.mem_write(args[3],word*(size//2))
                else:
                    self.uc.mem_write(args[3],bytes.fromhex('34 12')*(size//2))
        self.uc.mem_write(0x20003662,bytes([self.decode_success]))
        return self.uc.reg_read(UC_ARM_REG_R3) if self.decode_success else 0

    def invalidate_area(self):
        obj=self.uc.reg_read(UC_ARM_REG_R0)
        self.invalidated.append((obj,struct.unpack('<4h',self.uc.mem_read(self.uc.reg_read(UC_ARM_REG_R1),8))))
        return 0

    def call(self,name,*args,limit=3000000):
        for reg,value in zip((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3),args):
            self.uc.reg_write(reg,value)
        self.uc.reg_write(UC_ARM_REG_SP,0x2000f000)
        self.uc.reg_write(UC_ARM_REG_LR,0x7e001)
        self.uc.emu_start(self.symbols[name]|1,0,count=limit)
        assert self.uc.reg_read(UC_ARM_REG_PC)==0x7e000, f'{name} exceeded instruction limit'
        assert self.uc.reg_read(UC_ARM_REG_SP)==0x2000f000, f'{name} unbalanced stack'
        return self.uc.reg_read(UC_ARM_REG_R0)

    def state(self): return Runtime.from_buffer_copy(self.uc.mem_read(self.symbols['rt'],c.sizeof(Runtime)))
    def set(self,name,value):
        self.uc.mem_write(self.symbols['rt']+getattr(Runtime,name).offset,struct.pack('<I',value))
    def packet(self,name,p):
        self.uc.mem_write(0x2000e000,bytes(p).ljust(64,b'\0'))
        return self.call(name,0x2000e000)
    def hid(self,p):
        self.uc.mem_write(0x2000e000,bytes(p).ljust(64,b'\0'))
        if p[0]==0xba: self.call('rt_ba',0x2000e000,64,0xc)
        else: self.call('rt_usb',0,0x2000e000,64)
    def inject_knob(self,direction):
        assert direction in (-1,1)
        self.pending_knobs.append(direction)
    def key_push(self):
        # Stock producer 0x1CD4C(type,value): the UART A5 39 mapping (2,0) next, (1,0) previous reaches the
        # GUI read callback as key codes 0x12/0x11. StockGUI runs the real producer and callback instead.
        kind,value=self.uc.reg_read(UC_ARM_REG_R0),self.uc.reg_read(UC_ARM_REG_R1)
        code={(2,0):0x12,(1,0):0x11}.get((kind,value))
        assert code is not None, f'unexpected key message {(kind,value)}'
        self.pending_codes.append(code)
        return 0
    def gui(self):
        while self.pending_knobs:
            direction=self.pending_knobs.pop(0)
            self.call('rt_key',0x13 if direction==1 else 0x14)
        while self.pending_codes:
            self.call('rt_key',self.pending_codes.pop(0))
        self.call('rt_gui',0)
    def advance(self,milliseconds):
        assert milliseconds>=0
        tick=(struct.unpack('<I',self.uc.mem_read(0x20003a54,4))[0]+milliseconds)&0xffffffff
        for address in (0x20003a54,0x20003a58,0x2000bfcc):
            self.uc.mem_write(address,struct.pack('<I',tick))
        self.gui()
    def control(self,sub):
        p=bytearray([0xa5,0x3b,0,2,0,sub]); p.append(sum(p[1:])%256^255)
        self.hid(p); self.gui()
        for _ in range(32):
            if self.state().tx_head==self.state().tx_tail: break
            self.gui()

