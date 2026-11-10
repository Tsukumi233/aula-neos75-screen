import ctypes
import random
import struct
import zlib
import pytest
from emulator import Machine, Runtime


def stream(w=2,h=2):
    pixels=bytes((i*37)&255 for i in range(w*h*2))
    return b'WQ'+struct.pack('>HHH',w,h,1)+b'\0'+pixels


def packets(data,kind=0x5b):
    yield b'\xba\x06\xaa'+bytes([kind])+struct.pack('>I',len(data))
    pos=0
    while pos<len(data):
        room=4096-pos%4096
        n=min(62,len(data)-pos,room)
        chunk=data[pos:pos+n]
        pos+=n
        if pos%4096==0:
            chunk+=struct.pack('<I',zlib.crc32(data[pos-4096:pos]))
        yield b'\xba'+bytes([n])+chunk


def stock_upload_frames(data,kind=0x5b):
    """Independently mirror screen_upload.py build_frames, without its HID imports."""
    def frame(payload):
        assert len(payload)<=63
        return b'\xba'+bytes(payload)+bytes(63-len(payload))

    yield frame([6,0xaa,kind,*struct.pack('>I',len(data))]),True
    whole=len(data)//4096*4096
    for start in range(0,whole,4096):
        end=start+4096
        crc=struct.pack('<I',zlib.crc32(data[start:end])&0xffffffff)
        pos=start
        while pos<end:
            n=min(62,end-pos)
            last=pos+n>=end
            yield frame([n,*data[pos:pos+n],*(crc if last else b'')]),last
            pos+=n
    pos=whole
    while pos<len(data):
        n=min(62,len(data)-pos)
        yield frame([n,*data[pos:pos+n]]),False
        pos+=n


def upload(m,data,kind=0x5b):
    for p in packets(data,kind):
        m.hid(p); m.gui()


def checksum(report):
    end=4+report[3]
    assert report[end]==(sum(report[1:end])&255)^255


def minimal_jpeg(w=2,h=2):
    def segment(marker,data): return bytes([255,marker])+struct.pack('>H',len(data)+2)+data
    qt=segment(0xdb,b'\0'+b'\x01'*64+b'\x01'+b'\x01'*64)
    tables=b''.join(bytes([i])+b'\x01'+b'\0'*15+b'\0' for i in (0,1,0x10,0x11))
    sof=segment(0xc0,b'\x08'+struct.pack('>HH',h,w)+b'\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01')
    sos=segment(0xda,b'\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00')
    return b'\xff\xd8'+qt+segment(0xc4,tables)+sof+sos+b'\x00\x0f\xff\xd9'


@pytest.mark.parametrize('variant,flags',[('dev',127),('release',127)])
def test_hello_and_hid_context(variant,flags):
    m=Machine(variant); m.control(1)
    p=m.sent[-1]; checksum(p)
    assert p[:6]==bytes.fromhex('a5 3b 00 18 00 01')
    # flags bit 0: test knob injection, dev builds only; bit 1: keyboard telemetry; bit 2: host pages; bit 3: region frames
    assert struct.unpack_from('<2sBHHHHHBI BBH'.replace(' ',''),p,6)==(
        b'NS',2,3,480,360,480,360,3,0x2a304,1,flags,10000)
    m.calls.clear(); m.hid(b'\xba\x06\xaa\x5b'+struct.pack('>I',17))
    assert not m.calls
    assert m.state().head-m.state().tail==1


def test_enter_leave_rgb_and_event():
    m=Machine(); m.control(2)
    assert m.state().active==1 and next(p for p in m.sent if p[5]==2)[6]==0
    data=stream(); upload(m,data); m.gui()
    assert bytes(m.uc.mem_read(0x800d4200,len(data)))==data
    assert bytes(m.uc.mem_read(m.state().rgb,8))==data[9:]
    assert m.state().ok==1 and m.state().state==0
    event=m.sent[-1]; checksum(event)
    assert event[:12]==bytes.fromhex('a5 3b 00 07 00 10 01 01 00 00 00 ab')
    desc=m.state().descriptor
    assert desc.header==4|(2<<10)|(2<<21) and desc.size==8
    m.control(3); assert m.state().active==0
    assert 0x2e010 not in m.calls


def test_full_blocks_crc_and_ack():
    m=Machine(); m.control(2); data=stream(64,64); upload(m,data)
    for _ in range(5): m.gui()
    assert m.state().ok==1
    assert bytes(m.uc.mem_read(0x800d4200,len(data)))==data
    ack=[p for p in m.sent if p[0]==0xba]
    assert len(ack)==3 and all(p[6]==0xfe for p in ack)


@pytest.mark.parametrize('variant',['dev','release'])
def test_stock_upload_packetization_is_byte_identical_and_accepted(variant):
    m=Machine(variant); m.control(2); data=stream(64,64)
    reports=list(stock_upload_frames(data))
    assert [p.ljust(64,b'\0') for p in packets(data)]==[p for p,_ in reports]
    # A full block has 66 reports of 62 data bytes, then n=4 plus four CRC bytes.
    for block in range(2):
        start=1+block*67
        assert [p[1] for p,_ in reports[start:start+67]]==[62]*66+[4]
        final,needs_ack=reports[start+66]
        assert needs_ack and final[6:10]==struct.pack('<I',zlib.crc32(data[block*4096:(block+1)*4096]))
    for report,needs_ack in reports:
        before=len(m.sent); m.hid(report); m.gui()
        replies=m.sent[before:]
        assert any(p[0]==0xba and p[6]==0xfe for p in replies)==needs_ack
    for _ in range(5): m.gui()
    assert m.state().ok==1 and m.state().errors==0
    assert bytes(m.uc.mem_read(0x800d4200,len(data)))==data
    assert bytes(m.uc.mem_read(m.state().rgb,len(data)-9))==data[9:]


def test_short_packets_inside_blocks_and_partial_tail():
    m=Machine(); m.control(2); data=stream(64,64)
    m.hid(next(packets(data))); m.gui()
    pos=0
    # Keep a full first report; insert every possible short length inside each block.
    for end in (4096,8192):
        for n in (62,*range(1,62)):
            m.hid(b'\xba'+bytes([n])+data[pos:pos+n]); m.gui(); pos+=n
            assert m.state().received==pos and m.state().state==1
        while pos<end:
            n=min(62,end-pos); payload=data[pos:pos+n]; pos+=n
            if pos==end: payload+=struct.pack('<I',zlib.crc32(data[end-4096:end]))
            m.hid(b'\xba'+bytes([n])+payload); m.gui()
    # The short stream tail has no CRC or block ACK, even when split further.
    while pos<len(data):
        n=min(3,len(data)-pos); before=len(m.sent)
        m.hid(b'\xba'+bytes([n])+data[pos:pos+n]); m.gui(); pos+=n
        assert not any(p[0]==0xba for p in m.sent[before:])
    for _ in range(5): m.gui()
    assert m.state().ok==1 and m.state().errors==0
    assert bytes(m.uc.mem_read(0x800d4200,len(data)))==data


@pytest.mark.parametrize('n',[4,58,59,62])
def test_block_crc_report_capacity_and_legacy_length_rejection(n):
    m=Machine(); m.set('active',1)
    m.set('state',1); m.set('total',8192); m.set('received',4096-n); m.set('block',4096-n)
    m.set('crc',0xffffffff)
    payload=b'\x31'*n
    # Exercise the wire-capacity guard without ever writing more than one report.
    packet=b'\xba'+bytes([n])+payload
    if n<=58: packet+=struct.pack('<I',zlib.crc32(payload))
    m.packet('rt_receive',packet)
    assert m.state().state==(1 if n<=58 else 0)
    assert m.state().last_error==(0 if n<=58 else 3)
    if n<=58:
        assert m.state().received==4096 and m.state().block==0
    m=Machine(); m.set('active',1)
    m.set('state',1); m.set('total',8192); m.set('received',4092); m.set('block',4092)
    m.packet('rt_receive',b'\xba\x08ABCD'+struct.pack('<I',zlib.crc32(b'ABCD')))
    assert m.state().state==0 and m.state().last_error==3


def test_bad_crc_and_boundaries():
    m=Machine(); m.control(2)
    for p in packets(stream(64,32)):
        if p[1]<62 and len(p)>6 and p[0]==0xba and p[2]!=0xaa:
            p=p[:-1]+bytes([p[-1]^1])
        m.hid(p); m.gui()
    assert m.state().ok==0 and m.state().errors>=1
    m.packet('rt_receive',b'\xba\x06\xaa\x5b'+struct.pack('>I',0x2a305))
    assert m.state().state==0


@pytest.mark.parametrize('data',[stream(1,2),stream(482,2),stream(2,362),b'WQ'+b'\0'*12,
                                  stream()[:-1],stream()[:8]+b'\x01'+stream()[9:]])
def test_bad_headers(data):
    m=Machine(); m.control(2); upload(m,data)
    assert m.state().ok==0 and m.state().last_error==3


def test_not_active_busy_and_overflow():
    m=Machine(); upload(m,stream()); assert m.state().last_error==5
    m.control(2)
    first=next(packets(stream(64,64))); m.hid(first); m.gui()
    # A second start while receiving replaces the abandoned stream without an event.
    sent=len(m.sent); m.hid(first); m.gui()
    assert m.state().state==1 and m.state().received==0 and m.state().last_error==5
    assert not [p for p in m.sent[sent:] if p[0]==0xa5] and m.sent[-1][0]==0xba
    for _ in range(33): m.hid(b'\xba\x01\0')
    assert m.state().overflow==1
    m.gui(); assert m.state().overflow==0 and m.state().state==0 and m.state().last_error==4
    # Overflow with nothing being received sends no frame-done (it would look like a reply to the next start).
    sent=len(m.sent)
    for _ in range(33): m.hid(b'\xba\x01\0')
    m.gui(); assert m.state().overflow==0 and not [p for p in m.sent[sent:] if p[0]==0xa5 and p[5]==0x10]


def test_jpeg_validation_and_decode_failure():
    m=Machine(); m.control(2); jpg=minimal_jpeg()
    data=b'UB'+struct.pack('>HHH',2,2,1)+b'\0'+struct.pack('>I',len(jpg))+jpg
    m.decode_success=False; upload(m,data,0x5a)
    assert m.state().last_error==2 and m.state().ok==0
    m.decode_success=True; upload(m,data,0x5a)
    for _ in range(3): m.gui()
    assert m.state().ok==1
    assert 0x1cb88 in m.calls
    assert bytes(m.uc.mem_read(0x800d4200,len(jpg)))==jpg


def test_jpeg_malformed_segments_bounded():
    m=Machine(); jpg=minimal_jpeg(); m.uc.mem_write(0x800d4200,jpg)
    assert m.call('rt_jpeg',0x800d4200,len(jpg),2,2)==1
    for pos in (0,1,4,5,138,139,len(jpg)-1):
        bad=bytearray(jpg); bad[pos]^=0xff; m.uc.mem_write(0x800d4200,bytes(bad))
        assert m.call('rt_jpeg',0x800d4200,len(bad),2,2)==0
    rng=random.Random(42)
    for _ in range(80):
        bad=rng.randbytes(rng.randrange(4,300))
        m.uc.mem_write(0x800d4200,bad)
        assert m.call('rt_jpeg',0x800d4200,len(bad),2,2)==0


def test_a5_checksum_unknown_status_and_seq_wrap():
    m=Machine(); m.control(0x99); assert m.sent[-1][6]==0xee
    before=m.state().tx_head
    m.packet('rt_control',bytes.fromhex('a5 3b 00 02 00 01 00'))
    assert m.state().tx_head==before
    m.set('seq',65535); m.call('rt_event',3,123)
    assert m.state().seq==0
    m.gui(); event=m.sent[-1]; checksum(event)
    assert event[5:11]==bytes([0x10,3,0,0,123,0])
    m.control(4); checksum(m.sent[-1]); assert m.sent[-1][5]==4


def test_release_delays_original_flag_clear():
    m=Machine('release'); m.gui()
    m.uc.mem_write(0x20003a54,struct.pack('<I',60000))
    m.set('gui_ticks',998); m.gui(); assert 0x2e010 not in m.calls
    m.gui(); assert m.calls.count(0x2e010)==1
    m.gui(); assert m.calls.count(0x2e010)==1


def test_guards_and_no_flash_calls():
    m=Machine(); m.control(2); m.calls.clear()
    for name in ('rt_static','rt_anim','rt_resource','rt_page'): m.call(name,0)
    assert m.calls==[]
    m.set('active',0)
    for name in ('rt_static','rt_anim','rt_resource','rt_page'): m.call(name,0)
    assert len(m.calls)==4
    assert ctypes.sizeof(Runtime)==0x1110 and ctypes.sizeof(Runtime)<=0x1200  # inside the GUI heap carve


def test_heap_carve_preserves_upper_bound():
    m=Machine(); assert m.symbols['rt']==0x8017cf08
    assert m.call('rt_heap',0x8017cf08,0x60000)==0x8017e108
    from unicorn.arm_const import UC_ARM_REG_R1
    assert m.uc.reg_read(UC_ARM_REG_R1)==0x5ee00
    assert m.state().magic==0x3154534e


def test_stock_rgb_is_preserved_and_legacy_save_guard():
    m=Machine(); m.uc.mem_write(0x20006868,struct.pack('<I',0x80060000))
    m.uc.mem_write(0x20003568,struct.pack('<I',0x80003000))
    original=b'\x5a'*0x32008; m.uc.mem_write(0x80060000,original)
    m.control(2); assert m.state().rgb==0x800fe604 and 0x2637c not in m.calls
    upload(m,stream(64,64))
    for _ in range(5): m.gui()
    m.control(3); assert m.symbols['stock_static'] not in m.calls
    assert bytes(m.uc.mem_read(0x80060000,len(original)))==original
    assert bytes(m.uc.mem_read(0x20006868,4))==struct.pack('<I',0x80060000)
    assert 0x17a1c not in m.calls and 0x2e010 not in m.calls
    m.set('restoring',1); m.calls.clear(); m.call('rt_static_save',0x13,0x55)
    assert not m.calls
    m.set('restoring',0); m.call('rt_static_save',0x13,0x55)
    assert m.calls==[0x17a1c]


@pytest.mark.parametrize('variant',['dev','release'])
def test_telemetry_returns_the_stock_status_bytes_read_only(variant):
    m=Machine(variant)
    status=bytes(range(0x40,0x40+26))
    m.uc.mem_write(0x20004660,status)
    before=bytes(m.uc.mem_read(0x20004600,0x100))
    m.control(5)
    p=m.sent[-1]; checksum(p)
    assert p[:6]==bytes.fromhex('a5 3b 00 1c 00 05') and p[6:32]==status
    assert bytes(m.uc.mem_read(0x20004600,0x100))==before
    # Nothing beyond what any request does (reply send, the inactive tick's stock poll).
    reference=Machine(variant); reference.control(4)
    assert not m.state().active and set(m.calls)<=set(reference.calls)


def jpeg_stream(w,h):
    j=minimal_jpeg(w,h)
    return b'UB'+struct.pack('>HHH',w,h,1)+b'\0'+struct.pack('>I',len(j))+j


def region_stream(x,y,w,h,jpeg=None):
    j=jpeg if jpeg is not None else minimal_jpeg(w,h)
    return b'UR'+struct.pack('>HHHHI',w,h,x,y,len(j))+j


def frame_done(m): return [p for p in m.sent if p[0]==0xa5 and p[1]==0x3b and p[5]==0x10][-1]


def full_canvas(m):
    m.control(2); upload(m,jpeg_stream(480,360),0x5a)
    for _ in range(3): m.gui()
    assert frame_done(m)[6]==1 and m.state().canvas==1


@pytest.mark.parametrize('variant',['dev','release'])
def test_region_frame_replaces_exactly_its_rectangle(variant):
    m=Machine(variant); full_canvas(m)
    before=bytes(m.uc.mem_read(0x800fe604,480*360*2))
    x,y,w,h=100,50,64,32
    upload(m,region_stream(x,y,w,h),0x5a)
    for _ in range(3): m.gui()
    assert frame_done(m)[6]==1 and m.state().ok==2
    after=bytes(m.uc.mem_read(0x800fe604,480*360*2))
    words=[0xa0000000+i for i in range(w*h//2)]
    expect=bytearray(before)
    for r in range(h):
        row=b''.join(struct.pack('<I',v) for v in words[r*w//2:(r+1)*w//2])
        o=((y+r)*480+x)*2; expect[o:o+w*2]=row
    assert after==bytes(expect)
    assert m.invalidated[-1]==(m.state().image,(x,y,x+w-1,y+h-1))
    # the descriptor still describes the full canvas; nothing was hidden or re-placed
    assert m.state().descriptor.header==4|(480<<10)|(360<<21) and m.state().canvas==1


def test_region_needs_a_shown_full_canvas():
    m=Machine(); m.control(2)
    upload(m,region_stream(0,0,16,16),0x5a); m.gui()
    assert frame_done(m)[6]==6 and not m.invalidated
    upload(m,jpeg_stream(320,320),0x5a)                 # a smaller frame is centred, not a canvas
    for _ in range(3): m.gui()
    assert frame_done(m)[6]==1 and m.state().canvas==0
    upload(m,region_stream(0,0,16,16),0x5a); m.gui()
    assert frame_done(m)[6]==6
    upload(m,jpeg_stream(480,360),0x5a)
    for _ in range(3): m.gui()
    assert m.state().canvas==1
    m.control(3); assert m.state().canvas==0            # stock pages may reuse the pool
    m.control(2)
    upload(m,region_stream(0,0,16,16),0x5a); m.gui()
    assert frame_done(m)[6]==6


@pytest.mark.parametrize('x,y,w,h',[(0,0,8,16),(0,0,16,24),(1,0,16,16),(0,3,16,16),(472,0,16,16),
                                     (0,352,16,16),(0,0,480,192),(0,0,0,16)])
def test_region_geometry_is_validated(x,y,w,h):
    m=Machine(); full_canvas(m)
    before=bytes(m.uc.mem_read(0x800fe604,480*360*2))
    upload(m,region_stream(x,y,w,h,minimal_jpeg(max(w,2),max(h,2))),0x5a); m.gui()
    assert frame_done(m)[6]==3 and not m.invalidated
    assert bytes(m.uc.mem_read(0x800fe604,480*360*2))==before


def test_largest_region_fits_the_pool_tail_and_decode_failure_keeps_the_canvas():
    m=Machine(); full_canvas(m)
    upload(m,region_stream(0,0,320,128),0x5a)
    for _ in range(3): m.gui()
    assert frame_done(m)[6]==1
    call=m.decode_calls[-1]
    assert call[3]==0x80152c04 and call[3]+call[4]==0x80152c04+81920  # region scratch, the page cache follows
    before=bytes(m.uc.mem_read(0x800fe604,480*360*2))
    m.decode_success=False
    upload(m,region_stream(32,32,32,32),0x5a)
    for _ in range(3): m.gui()
    assert frame_done(m)[6]==2 and m.state().canvas==1
    assert bytes(m.uc.mem_read(0x800fe604,480*360*2))==before
