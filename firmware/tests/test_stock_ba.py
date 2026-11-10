"""Compare BA details against the installed backup's original Thumb routines."""
import struct
import zlib
import pytest
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2
from emulator import Machine
from make_patch import BACKUP, BASE, CODE, check_input


class StockBA(Machine):
    def __init__(self):
        self.stock_mode=False
        super().__init__()
        original=BACKUP.read_bytes(); check_input(original)
        # Restore original hooks while keeping the appended runtime available.
        self.uc.mem_write(BASE,original[:CODE-BASE])
        self.stubs[0x083e4]=self.memcpy
        self.stubs[0x08432]=self.memzero
        self.stubs[0x3d308]=lambda:1
        self.symbols['original_ack']=0x34fc8
        self.symbols['original_ba']=0x31e6c
        self.stock_mode=True

    def hook(self,uc,address,size,data):
        # These ranges perform only packet logic, checksum and progress arithmetic.
        ranges=((0x104dc,0x10500),(0x31a6c,0x31dec),(0x31e6c,0x31fbe),
                (0x32040,0x32078),(0x34fc8,0x35050))
        if self.stock_mode and any(start<=address<end for start,end in ranges): return
        super().hook(uc,address,size,data)

    def memcpy(self):
        dst,src,n=(self.uc.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2))
        self.uc.mem_write(dst,bytes(self.uc.mem_read(src,n))); return dst

    def memzero(self):
        dst,n=(self.uc.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1))
        self.uc.mem_write(dst,bytes(n)); return dst


@pytest.mark.parametrize('kind',[0x5a,0x5b])
@pytest.mark.parametrize('first',[0,1])
def test_first_and_block_ack_match_original_machine_code(kind,first):
    stock=StockBA()
    stock.uc.mem_write(0x2000c118,struct.pack('<I',0xc))
    stock.uc.mem_write(0x2000e000,bytes([0xfe,first]))
    stock.call('original_ack',kind,0x2000e000,2)
    patched=Machine(); patched.set('active',1)
    if first:
        packet=b'\xba\x06\xaa'+bytes([kind])+struct.pack('>I',8192)
    else:
        for field,value in (('state',1),('type',kind),('total',8192),('received',4092),
                            ('block',4092),('crc',0xffffffff)):
            patched.set(field,value)
        packet=b'\xba\x04ABCD'+struct.pack('<I',zlib.crc32(b'ABCD'))
    patched.packet('rt_receive',packet); patched.gui()
    report=patched.sent[-1]
    assert report==stock.sent[-1]
    assert report[:8]==bytes([0xba,7,0xa5,kind,0,2,0xfe,first])
    assert report[8]==(~sum(report[3:8])&255) and report[9:]==bytes(55)


@pytest.mark.parametrize('n',[1,7,31,61])
def test_short_mid_block_packets_match_original_receiver(n):
    stock=StockBA(); total=8201
    prefix=bytes(range(62)); payload=b'\x31'*n
    for address,value in ((0x20004758,total),(0x2000475c,62)):
        stock.uc.mem_write(address,struct.pack('<I',value))
    stock.uc.mem_write(0x20004778,struct.pack('<H',62))
    stock.uc.mem_write(0x20005868,prefix)
    packet=(b'\xba'+bytes([n])+payload).ljust(64,b'\0')
    stock.uc.mem_write(0x2000e000,packet)
    stock.call('original_ba',0x2000e000,64,0xc)
    assert struct.unpack('<I',stock.uc.mem_read(0x2000475c,4))[0]==62+n
    assert bytes(stock.uc.mem_read(0x20005868,62+n))==prefix+payload
    assert not stock.sent
    patched=Machine(); patched.set('active',1)
    patched.packet('rt_receive',b'\xba\x06\xaa\x5b'+struct.pack('>I',total))
    patched.packet('rt_receive',b'\xba\x3e'+prefix)
    patched.packet('rt_receive',packet)
    assert patched.state().received==62+n and patched.state().state==1
    assert bytes(patched.uc.mem_read(0x800d4200,62+n))==prefix+payload
