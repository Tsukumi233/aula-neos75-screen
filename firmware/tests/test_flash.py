import builtins
import hashlib
from pathlib import Path
import struct
import subprocess
import sys
import pytest
import flash


def image(n=0x1001):
    return struct.pack('<II',0x2000c548,0x81bd)+b'\xff'*(n-8)


def ready(plan=None):
    g=flash.Gate(plan); g.descriptors_ok=True; return g


@pytest.mark.parametrize('address',[0,0x7000,0x7f00,0x7fff,0x8001,0xa000,0x7f000,0x80000,0x70000000,0xffffffff])
def test_erase_white_list_rejects(address):
    gate=ready(flash.Plan(image()))
    with pytest.raises(ValueError): gate.authorize(False,1,0,b'\x41'+struct.pack('<I',address))


@pytest.mark.parametrize('n',[0,7,0x77001,0x78000])
def test_image_length_rejects(n):
    with pytest.raises(ValueError): flash.Plan(b'\0'*n)


@pytest.mark.parametrize('sp',[0,0x1fffffff,0x21000000,0x70000000,0xffffffff])
def test_stack_rejects(sp):
    data=bytearray(image()); struct.pack_into('<I',data,0,sp)
    with pytest.raises(ValueError): flash.Plan(data)


def test_sector_padding_max_image_and_read_only():
    plan=flash.Plan(image()); assert plan.sectors==(0x8000,0x9000)
    assert plan.sector(0x9000)==b'\xff'*4096
    assert flash.Plan(image(0x77000)).sectors[-1]==0x7e000
    with pytest.raises(ValueError): ready().authorize(False,1,0,b'\x41\0\x80\0\0')
    for addr in (0x80000,0x70000000):
        with pytest.raises(ValueError): ready().authorize(False,1,0,b'\x21'+struct.pack('<I',addr))


def test_exact_data_sequence_and_exit_requires_matching_readback():
    plan=flash.Plan(image()); g=ready(plan)
    erase=b'\x41'+struct.pack('<I',0x8000); set_address=b'\x21'+struct.pack('<I',0x8000)
    g.authorize(False,1,0,erase); g.completed(False,1,0,erase)
    g.authorize(False,1,0,set_address); g.completed(False,1,0,set_address)
    bad=bytearray(plan.sector(0x8000)); bad[50]^=1
    with pytest.raises(ValueError): g.authorize(False,1,2,bytes(bad))
    g.authorize(False,1,2,plan.sector(0x8000)); g.completed(False,1,2,plan.sector(0x8000))
    with pytest.raises(ValueError): g.authorize(False,1,2,plan.sector(0x8000))
    with pytest.raises(ValueError): g.authorize(False,1,0,b'')
    with pytest.raises(ValueError): g.verify(plan.image)
    assert not g.verified
    g.verify(plan.padded); g.authorize(False,1,0,b'')
    bad=bytearray(plan.padded); bad[-1]^=1
    with pytest.raises(ValueError): g.verify(bad)
    with pytest.raises(ValueError): g.authorize(False,1,0,b'')


def test_unvalidated_and_unknown_requests_rejected():
    g=flash.Gate(flash.Plan(image()))
    for req,value,payload in ((1,0,b'\x41\0\x80\0\0'),(0,0,b''),(4,0,b''),
                              (1,0,b'\x41'),(1,3,b'\xff'*4096)):
        with pytest.raises(ValueError): g.authorize(False,req,value,payload)
    g=ready(); g.address=0x7f000; g.upload_next=2
    with pytest.raises(ValueError): g.authorize(True,2,2,4097)
    with pytest.raises(ValueError): g.authorize(True,2,3,4096)
    with pytest.raises(ValueError): g.authorize(True,1,0,6)


def test_dry_run_never_imports_usb(monkeypatch,capsys):
    original_import=builtins.__import__
    def checked_import(name,*args,**kwargs):
        assert not name.startswith(('usb','libusb','enter_dfu'))
        return original_import(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',checked_import)
    path=Path(__file__).resolve().parents[1]/'build/neos75_rt_dev.bin'
    assert flash.main(['--image',str(path),'--dry-run'])==0
    assert '0x08000' in capsys.readouterr().out
    assert flash.main(['--restore','--dry-run'])==0
    assert flash.main(['--dump','unused','--dry-run'])==0


def test_checks_remain_effective_under_optimized_python():
    source='import flash; flash.Plan(bytes(0x78000))'
    result=subprocess.run([sys.executable,'-B','-O','-c',source],cwd=Path(flash.__file__).parent,
                          capture_output=True,text=True)
    assert result.returncode!=0 and 'ValueError' in result.stderr


def test_session_blocks_backend_before_any_bad_write():
    class Backend:
        def ctrl_transfer(self,*args): raise AssertionError('Backend must not be called')
    session=flash.Session.__new__(flash.Session)
    session.device=type('Device',(),{'idVendor':0x5542,'idProduct':0x1000})()
    session.backend=Backend(); session.gate=ready(flash.Plan(image())); session.handle=None
    with pytest.raises(ValueError): session.ctrl(False,1,0,b'\x41\0\xf0\x07\0')


@pytest.mark.parametrize('corrupt,flag_set',[(False,True),(True,True),(False,False)])
def test_complete_dfu_sequence_with_fake_memory(corrupt,flag_set):
    class Backend:
        def __init__(self):
            self.memory=bytearray(b'\x55'*0x80000)
            self.state=2; self.address=0; self.requests=[]
        def ctrl_transfer(self,handle,kind,request,value,index,buf,timeout):
            self.requests.append((kind,request,value,bytes(buf)))
            if kind==0xa1 and request==3:
                buf[:]=__import__('array').array('B',[0,0,0,0,self.state,0])
            elif kind==0xa1 and request==2:
                addr=self.address+(value-2)*4096
                data=bytearray(self.memory[addr:addr+len(buf)])
                if corrupt: data[0]^=1
                buf[:]=__import__('array').array('B',data); self.state=9
            elif request==6: self.state=2
            elif request==1:
                if not buf: self.state=8
                elif value==0 and buf[0]==0x41:
                    addr=struct.unpack_from('<I',buf,1)[0]
                    self.memory[addr:addr+4096]=b'\xff'*4096; self.state=5
                    if flag_set: self.memory[0x7f00:0x7f04]=struct.pack('<I',0x55aaaa55)
                elif value==0 and buf[0]==0x21:
                    self.address=struct.unpack_from('<I',buf,1)[0]; self.state=5
                elif value==2:
                    self.memory[self.address:self.address+4096]=bytes(buf); self.state=5
                else: raise AssertionError('Unexpected DFU request')
            else: raise AssertionError('Unexpected USB request')
            return len(buf)
    plan=flash.Plan(image()); s=flash.Session.__new__(flash.Session)
    s.device=type('Device',(),{'idVendor':0x5542,'idProduct':0x1000})()
    s.backend=Backend(); s.gate=ready(plan); s.handle=None
    if corrupt or not flag_set:
        with pytest.raises(ValueError,match='Readback mismatch' if corrupt else 'Trial flag mismatch'): s.write()
    else:
        s.write()
        assert bytes(s.backend.memory[0x8000:0xa000])==plan.padded
    exits=[r for r in s.backend.requests if r[:3]==(0x21,1,0) and not r[3]]
    assert bool(exits)==(not corrupt and flag_set)
    expected_boot=bytearray(b'\x55'*0x8000)
    if flag_set: expected_boot[0x7f00:0x7f04]=struct.pack('<I',0x55aaaa55)
    assert s.backend.memory[:0x8000]==expected_boot
    assert s.backend.memory[0xa000:]==b'\x55'*(0x80000-0xa000)
