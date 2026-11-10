"""Observe real stock GUI/config code; no GUI/page/config function is stubbed."""
import struct

import pytest

from stock_gui import StockGUI, SAVE_CHAIN
from emulator import BUILD
from test_runtime import upload, stream


@pytest.fixture(params=['v1','v2_failed','v2'])
def stock(request):
    directory=BUILD/request.param if request.param in ('v1','v2_failed') else None
    machine=StockGUI(build_dir=directory)
    machine.baseline=request.param
    machine.bootstrap()
    return machine


def saves(machine):
    return [r for r in machine.trace if r['kind']=='config_call']


def test_real_gui_sixty_seconds_twenty_cycles_frames_and_restored_timers(stock):
    m=stock
    assert not saves(m)
    initial=bytes(m.uc.mem_read(0x7f000,4096)); m.trace.clear()
    for tick in range(10,60001,10):
        for a in (0x20003a54,0x20003a58): m.uc.mem_write(a,struct.pack('<I',tick))
        m.gui()
        if tick%500==0: m.tick(tick)
    for i in range(20):
        m.control(2)
        if i in (0,10,19):
            upload(m,stream(16,16)); m.gui()
        m.control(3); m.tick(60000+(i+1)*500)
    assert m.state().ok==3 and m.state().errors==0 and not m.state().active
    assert not saves(m)
    assert bytes(m.uc.mem_read(0x7f000,4096))==initial
    assert bytes(m.uc.mem_read(0x2000466d,2))==b'\x03\x64'
    required={0x3423c,0x2b120,0x2b128,0x3370c,0x3401c}
    if m.baseline!='v1': required.update((0x23fa0,0x24814))
    assert required.issubset(m.covered),[hex(a) for a in required-m.covered]
    assert not set(SAVE_CHAIN)&set(m.stubs) - {0x8fd0,0x8fb0,0x8f68,0x90e8}


def test_real_telemetry_then_page_reload_produces_the_observed_68_bytes(stock):
    m=stock; m.trace.clear(); initial=bytes(m.uc.mem_read(0x7f000,4096))
    # Explicit stimulus: native A5 35 ingress followed by native page callback.
    # This is a possible mechanism, not a recording of the hardware schedule.
    m.telemetry(2); m.call('page_load',0x2000349c)
    m.telemetry(3); m.call('page_load',0x2000349c)
    after=bytes(m.uc.mem_read(0x7f000,4096))
    diff=[i for i,(a,b) in enumerate(zip(initial,after)) if a!=b]
    assert diff==list(range(0x420,0x424))+list(range(0x440,0x480))
    assert after[0x464:0x480]==initial[0x424:0x440]
    calls=saves(m)
    assert [r['address'] for r in calls].count('0x179d8')==2
    assert [r['address'] for r in calls].count('0x178b4')==2
    assert [r['address'] for r in calls].count('0x90e8')==4
    assert all(r['lr']=='0x337a1' for r in calls if r['address']=='0x179d8')
    ram=[r for r in m.trace if r['kind']=='ram_write' and r['address']=='0x2000466c']
    assert [r['pc'] for r in ram]==['0x840e','0x840e']
    assert [int(r['value'],16)>>8&255 for r in ram]==[2,3]


def test_native_legal_config_saves_are_observable_and_not_blocked():
    m=StockGUI(); m.bootstrap(); m.trace.clear()
    m.symbols.update(set_byte=0x17a1c,set_word=0x17a4c,save_pending=0x33fa4)
    m.set('restoring',2)
    m.call('set_byte',0xe,7)
    m.call('set_word',0xf,4)
    assert bytes(m.uc.mem_read(0x20004438+0xe,3))==b'\x07\x04\x00'
    assert [r['address'] for r in saves(m)].count('0x178b4')==2
    m.telemetry(2)
    m.call('save_pending',1)
    assert any(r['address']=='0x179d8' for r in saves(m))
    assert m.state().restoring==2
    assert bytes(m.uc.mem_read(0x20004449,1))==b'\x02'
    assert [r['address'] for r in saves(m)].count('0x178b4')==3


def test_fixed_leave_syncs_live_ui_and_does_not_defer_configuration_writes():
    m=StockGUI(); m.bootstrap(); m.tick(500); m.trace.clear()
    initial=bytes(m.uc.mem_read(0x7f000,4096))
    for i in range(20):
        m.control(2); m.telemetry(2)
        if i in (0,10,19):
            upload(m,stream(16,16)); m.gui()
        m.control(3)
        assert m.state().restoring==0
        assert bytes(m.uc.mem_read(0x2000466d,2))==b'\x02\x64'
        assert bytes(m.uc.mem_read(0x20004689,2))==b'\x02\x64'
        for j in range(3): m.tick(1000+i*2000+j*500)
        # The live status remains correct; a later return to the stored state
        # must not create the second half of the observed 03->02->03 writes.
        m.telemetry(3); m.tick(2500+i*2000)
    assert m.state().ok==3 and not m.state().active
    assert not saves(m)
    assert bytes(m.uc.mem_read(0x7f000,4096))==initial


@pytest.mark.parametrize('baseline',['v1','v2_failed'])
def test_unfixed_real_leave_with_telemetry_exposes_the_latent_save_path(baseline):
    m=StockGUI(build_dir=BUILD/baseline); m.bootstrap(); m.tick(500); m.trace.clear()
    initial=bytes(m.uc.mem_read(0x7f000,4096))
    for i,mode in enumerate((2,3)):
        m.control(2); m.telemetry(mode); m.control(3); m.tick(1000+i*500)
    after=bytes(m.uc.mem_read(0x7f000,4096))
    assert sum(a!=b for a,b in zip(initial,after))==68
    attempts=[r for r in saves(m) if r['address']=='0x179d8']
    assert len(attempts)==2
    for attempt in attempts:
        assert [r.get('call') for r in attempt['stack']][-3:]==['0x343ee','0x340d4','0x3379c']


@pytest.mark.parametrize('baseline',['v1','v2'])
def test_v1_style_static_restoration_and_v2_static_preservation_are_flash_free(baseline):
    m=StockGUI(build_dir=BUILD/'v1' if baseline=='v1' else None,page=4)
    m.bootstrap(); m.tick(500); m.trace.clear()
    initial=bytes(m.uc.mem_read(0x7f000,4096))
    for i in range(20):
        m.control(2)
        if i in (0,10,19): upload(m,stream(16,16)); m.gui()
        m.control(3); m.tick(1000+i*500)
    assert not saves(m)
    assert bytes(m.uc.mem_read(0x7f000,4096))==initial
    assert m.state().ok==3
    if baseline=='v1':
        assert m.symbols['stock_static'] in m.covered
        assert 0x11918 in m.covered


def test_runtime_telemetry_reads_what_the_stock_a5_35_handler_stored():
    # Current build only: the archived v1/v2_failed have no sub 05. The stock handler and the stock status
    # updater on the following GUI ticks save key 0x11 after a mode change by themselves; a twin machine
    # that sends STATUS instead shows the telemetry query adds nothing to that.
    machines=[]
    for sub in (5,4):
        m=StockGUI(); m.bootstrap()
        m.telemetry(2)   # native ingress: charging (mode 2), battery and link from the fixture
        m.control(sub); machines.append(m)
    m,twin=machines
    stored=bytes(m.uc.mem_read(0x20004660,26))
    assert stored[13]==2
    p=next(r for r in m.sent if r[1]==0x3b and r[5]==5)
    assert p[6:32]==stored
    assert bytes(m.uc.mem_read(0x7f000,4096))==bytes(twin.uc.mem_read(0x7f000,4096))
    assert bytes(m.uc.mem_read(0x20004600,0x100))==bytes(twin.uc.mem_read(0x20004600,0x100))
    assert [r['address'] for r in saves(m)]==[r['address'] for r in saves(twin)]
