"""Knob regression through original queue, input, page and configuration code."""
import struct

import pytest

from stock_gui import StockGUI, SAVE_CHAIN
from test_runtime import upload, stream, packets, checksum


def native():
    m=StockGUI(); m.bootstrap(); m.tick(500); m.trace.clear()
    return m


def calls(m):
    return [r for r in m.trace if r['kind']=='config_call']


def turn(m,direction):
    m.inject_knob(direction); m.gui()
    for _ in range(4): m.gui()


def page(m): return m.uc.mem_read(0x200044ad,1)[0]
def config(m): return bytes(m.uc.mem_read(0x7f000,4096))
def events(m): return [p for p in m.sent if p[0]==0xa5 and p[1]==0x3b and p[5] in (0x20,0x21)]
def index_saves(m): return [r for r in calls(m) if r['address']=='0x17a4c']


def test_disarmed_real_queue_key_and_pages_match_original_step_for_step():
    patched,original=native(),native()
    # The reference executes the exact original BL at the GUI call site.
    # All queue, key, page, timer and config routines execute native instructions.
    original.uc.mem_write(0x1d268,bytes.fromhex('fff714ff'))
    for direction in (1,)*6+(-1,)*6:
        for m in (patched,original):
            m.trace.clear(); turn(m,direction); m.advance(2100)
        for address,size in ((0x200044ad,2),(0x200045d6,4),(0x20004434,32)):
            assert bytes(patched.uc.mem_read(address,size))==bytes(original.uc.mem_read(address,size))
        assert patched.call('stock_screen')==original.call('stock_screen')
        assert config(patched)==config(original)
        assert [(r['address'],r['args']) for r in calls(patched)]==[(r['address'],r['args']) for r in calls(original)]
        assert not patched.state().armed and not patched.state().active
    assert not events(patched)
    assert {0x1cd4c,0x1cd10,0x1d184,0x1d094,0x3c128,0x33fa4}.issubset(patched.covered)
    assert not set(SAVE_CHAIN)&set(patched.stubs)-{0x8fd0,0x8f68,0x8fb0,0x90e8}


@pytest.mark.parametrize('direction',[1,-1])
def test_real_slot_cycle_only_user_selected_stock_pages_schedule_saves(direction):
    m=native(); initial=config(m); old=page(m)
    m.control(2); upload(m,stream(16,16)); m.gui()
    assert m.state().ok==1 and not calls(m) and config(m)==initial
    for expected in (range(6) if direction==1 else range(5,-1,-1)):
        # Knob input is not host activity. A real host sends STATUS while no
        # frames are sent; six stock save deadlines exceed the 10 s lease.
        before_keepalive=list(calls(m))
        m.control(4)
        assert calls(m)==before_keepalive
        m.trace.clear(); m.sent.clear(); turn(m,direction)
        assert not m.state().active and m.state().armed and page(m)==expected
        assert not index_saves(m)
        assert [r[6] for r in events(m) if r[5]==0x21]==[direction&255]
        m.advance(1999); assert not index_saves(m)
        m.advance(1)
        saved=index_saves(m)
        assert len(saved)==1 and saved[0]['args'][:2]==['0xf',hex(expected)]
        assert not any(r['address']=='0x179d8' for r in calls(m))
    m.trace.clear(); m.sent.clear(); saved=config(m)
    turn(m,direction)
    assert m.state().active and m.state().armed
    assert [(r[5],r[6],r[7] if r[5]==0x20 else None) for r in events(m)]==[(0x21,direction&255,None),(0x20,1,1)]
    assert not calls(m)
    # Renew activity while allowing any delayed original timer to run.
    m.control(4); m.advance(2100)
    assert not calls(m) and config(m)==saved
    m.control(3)
    assert not calls(m) and page(m)==(5 if direction==1 else 0)
    assert old==5


def test_real_pending_user_save_survives_entering_the_slot():
    m=native(); m.control(2); turn(m,1)
    assert page(m)==0 and not index_saves(m)
    # Reverse before the original two-second save deadline. Entering the slot
    # must neither clear the legitimate pending page nor restart its timestamp.
    pending=bytes(m.uc.mem_read(0x200045d6,4)); turn(m,-1)
    assert m.state().active and bytes(m.uc.mem_read(0x200045d6,4))==pending
    m.trace.clear(); m.advance(2000)
    assert [(r['args'][0],r['args'][1]) for r in index_saves(m)]==[('0xf','0x0')]
    assert page(m)==0 and m.state().active
    assert bytes(m.uc.mem_read(0x20004447,2))==b'\0\0'


@pytest.mark.parametrize('hidden',[False,True])
def test_real_timeout_does_not_create_a_configuration_save(hidden):
    m=native(); m.control(2)
    if hidden:
        turn(m,1); m.advance(2100)
        assert index_saves(m)
    m.control(4); m.trace.clear(); m.sent.clear(); before=config(m)
    expected=page(m)
    m.advance(10000)
    for _ in range(4): m.gui()
    assert not m.state().armed and not m.state().active and page(m)==expected
    assert not calls(m) and config(m)==before
    assert [(p[6],p[7]) for p in events(m) if p[5]==0x20]==([] if hidden else [(0,3)])
    # Once disarmed, the next reverse wraps through stock instead of the slot.
    turn(m,-1)
    assert page(m)==(expected+5)%6 and not m.state().active


def test_real_hidden_frames_ack_without_touching_shared_buffers_or_configuration():
    m=native(); m.control(2); turn(m,1); m.advance(2100)
    m.control(4); m.trace.clear(); m.sent.clear()
    rx=bytes(m.uc.mem_read(0x800d4200,172804))
    rgb=bytes(m.uc.mem_read(0x800fe604,345600)); before=config(m)
    upload(m,stream(64,64)); m.gui()
    assert m.state().last_error==5 and m.state().errors==1
    assert bytes(m.uc.mem_read(0x800d4200,172804))==rx
    assert bytes(m.uc.mem_read(0x800fe604,345600))==rgb
    assert len([p for p in m.sent if p[0]==0xba and p[6]==0xfe])==3
    assert not calls(m) and config(m)==before
    for p in events(m): checksum(p)


@pytest.mark.parametrize('kind,value,direction',[(1,0,-1),(2,0,1),(8,7,1),(8,8,-1)])
def test_all_stock_direction_mappings_reach_the_gui_hook(kind,value,direction):
    m=native(); m.control(2); m.sent.clear(); m.trace.clear()
    m.call('key_push',kind,value)
    assert m.state().active and not calls(m) and not m.sent
    m.gui()
    for _ in range(3): m.gui()
    assert not m.state().active and page(m)==(0 if direction==1 else 5)
    assert [(p[6]) for p in events(m) if p[5]==0x21]==[direction&255]
    assert not calls(m)


def test_original_lock_and_overlay_are_not_bypassed_and_no_press_is_invented():
    m=native(); m.control(2); m.sent.clear(); m.trace.clear()
    context=bytes(m.uc.mem_read(0x2000349c,4))
    # Stock checks both BF77 and the context header; BF77=0 alone is not locked.
    m.uc.mem_write(0x2000349c,struct.pack('<I',1))
    m.uc.mem_write(0x2000bf77,b'\0'); current=page(m)
    turn(m,1)
    assert m.state().active and page(m)==current
    assert [p[6] for p in events(m) if p[5]==0x21]==[1]
    m.uc.mem_write(0x2000349c,context)
    m.uc.mem_write(0x2000bf77,b'\1'); m.uc.mem_write(0x200044ae,b'\x80')
    turn(m,-1)
    assert m.state().active and page(m)==(current+5)%6
    m.uc.mem_write(0x200044ae,b'\0'); m.sent.clear()
    m.call('key_push',3,0); m.gui()
    assert not events(m) and not calls(m)


def test_original_context_null_exception_still_allows_leaving_the_slot():
    m=native(); m.control(2); m.sent.clear(); m.trace.clear()
    m.uc.mem_write(0x2000349c,bytes(4)); m.uc.mem_write(0x2000bf77,b'\0')
    turn(m,1)
    assert not m.state().active and m.state().armed and page(m)==0
    assert [(p[5],p[6]) for p in events(m)]==[(0x21,1),(0x20,0)]
    assert not calls(m)
    m.advance(1999); assert not calls(m)
    m.advance(1)
    assert len(index_saves(m))==1 and index_saves(m)[0]['args'][:2]==['0xf','0x0']



def _live_animation_timers(m):
    """Distinct LVGL timers running the stock frame-animation callback 0x3F4A0 during 2 s."""
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0
    seen=set()
    handle=m.uc.hook_add(UC_HOOK_CODE,lambda uc,a,s,d: seen.add(uc.reg_read(UC_ARM_REG_R0)),begin=0x3f4a0,end=0x3f4a1)
    for _ in range(40): m.advance(50)
    m.uc.hook_del(handle)
    return len(seen)


def _walk(m):
    """Timer count on each stock page, visiting all six with the knob (disarmed: original path)."""
    counts={}
    for _ in range(6):
        turn(m,1); counts[page(m)]=_live_animation_timers(m)
    return counts


def _stock_reference():
    """Pure stock: the original BL, all pages visited once. The original firmware itself leaves one
    orphan page-0 timer when page 0 is first created (its create path runs the load function too), so
    pages 0 and 5 show 2 timers there; that count is constant and is the baseline, not a regression."""
    m=native(); m.uc.mem_write(0x1d268,bytes.fromhex('fff714ff'))
    for _ in range(6): turn(m,1)
    return _walk(m)


def test_slot_round_trips_do_not_leak_stock_animation_timers():
    """Hardware 2026-11-07: after many knob trips through the slot the stock animations ran much
    faster (each trip left a live timer behind). Covers both animated pages (0 and 5), both directions,
    returning to the page the slot was entered from, and host ENTER followed by the knob."""
    reference=_stock_reference()
    m=native()
    assert page(m)==5
    m.control(2); upload(m,stream(16,16)); m.gui()
    def step(direction):
        m.control(4); turn(m,direction)
        return 'RT' if m.state().active else page(m)
    for _ in range(4):
        # slot entered from 5 -> 5, slot, 0, slot (from 0), 0, slot (from 0), 5, slot (from 5)
        assert [step(d) for d in (-1,1,1,-1,1,-1,-1,1)]==[5,'RT',0,'RT',0,'RT',5,'RT']
    m.control(4); turn(m,1); assert not m.state().active
    m.control(2); m.control(4); turn(m,1)                                         # host ENTER, knob away
    assert not m.state().active
    m.control(3)                                                                  # hidden LEAVE keeps page
    assert not m.state().armed
    assert _walk(m)==reference


def test_visible_leave_after_knob_entry_keeps_stock_animation_count():
    reference=_stock_reference()
    m=native()
    m.control(2); m.control(4); turn(m,1); assert page(m)==0                     # visit page 0 once
    for _ in range(5): m.control(4); turn(m,1)
    assert page(m)==5
    m.control(4); turn(m,1); assert m.state().active                              # 5 -> slot by knob
    m.control(3); assert page(m)==5 and not m.state().active                      # visible LEAVE
    assert _live_animation_timers(m)==reference[5]
    assert _walk(m)==reference


@pytest.mark.parametrize('seed',[1,2])
def test_random_knob_walk_with_host_requests_keeps_stock_animation_timers(seed):
    """Randomised: knob turns in both directions mixed with host ENTER/LEAVE/STATUS, then disarm and walk all
    pages. Live animation timers must equal a pure-stock run, and the key-0x11 save path is never used."""
    import random
    rng=random.Random(seed)
    reference=_stock_reference()
    m=native()
    m.control(2); upload(m,stream(16,16)); m.gui()
    for _ in range(36):
        roll=rng.random()
        if roll<0.08: m.control(3)
        elif roll<0.16: m.control(2)
        else: m.control(4)
        turn(m,rng.choice([1,-1]))
    m.control(3)
    assert not m.state().armed and not m.state().active
    assert not any(r['address']=='0x179d8' for r in calls(m))
    assert _walk(m)==reference



def inject(m,code):
    """A5 3B 30 <code>: a knob step through the stock producer, as the UART A5 39 handler does."""
    p=bytearray([0xa5,0x3b,0,3,0,0x30,code]); p.append(~sum(p[1:])&255)
    m.hid(bytes(p)); m.gui()
    for _ in range(4): m.gui()
    return [r for r in m.sent if r[0]==0xa5 and r[1]==0x3b and r[5]==0x30][-1]


def test_injected_knob_step_matches_the_uart_knob_path_step_for_step():
    """The dev injection queues the same key message as UART A5 39 01/02 ((2,0) next, (1,0) previous) and
    then everything runs as for a physical turn: compared with a machine whose producer is called directly."""
    injected,direct=native(),native()
    for code,message in ((1,(2,0)),(1,(2,0)),(0xff,(1,0)),(0xff,(1,0)),(0xff,(1,0))):
        injected.trace.clear(); direct.trace.clear()
        reply=inject(injected,code)
        assert reply[6]==0
        direct.call('key_push',*message); direct.gui()
        for _ in range(4): direct.gui()
        injected.advance(2100); direct.advance(2100)
        assert page(injected)==page(direct)
        assert bytes(injected.uc.mem_read(0x200045d6,4))==bytes(direct.uc.mem_read(0x200045d6,4))
        assert config(injected)==config(direct)
        assert [(r['address'],r['args']) for r in calls(injected)]==[(r['address'],r['args']) for r in calls(direct)]
    assert page(injected)==4  # 5 -> 0 -> 1 -> 0 -> 5 -> 4


def test_injected_knob_cycles_the_armed_slot_and_reports_events():
    m=native(); m.control(2); upload(m,stream(16,16)); m.gui()
    seen=[]
    for _ in range(7):
        m.control(4); m.sent.clear()
        inject(m,1)
        seen.append('RT' if m.state().active else page(m))
        assert [r[6] for r in events(m) if r[5]==0x21]==[1]
    assert seen==[0,1,2,3,4,5,'RT']
    assert inject(m,0x02)[6]==0xee  # only 01 and FF are steps
    assert m.state().active


def test_release_build_steps_the_knob_from_the_pc_too():
    m=StockGUI('release'); m.bootstrap(); m.tick(500); m.trace.clear()
    before=page(m)
    assert inject(m,1)[6]==0
    assert page(m)==(before+1)%6
    assert inject(m,0x02)[6]==0xee and page(m)==(before+1)%6


def set_pages(m,count):
    p=bytearray([0xa5,0x3b,0,3,0,0x06,count]); p.append(~sum(p[1:])&255)
    m.hid(bytes(p)); m.gui()
    for _ in range(4): m.gui()
    return [r for r in m.sent if r[0]==0xa5 and r[1]==0x3b and r[5]==0x06][-1]


def page_events(m): return [(p[6],p[7]) for p in m.sent if p[0]==0xa5 and p[1]==0x3b and p[5]==0x22]


@pytest.mark.parametrize('direction',[1,-1])
def test_host_pages_are_stepped_like_stock_pages(direction):
    """Three host pages: the knob moves between them inside the slot, leaves only past either end, walks
    the six stock pages, and re-enters at the first page clockwise or the last page counter-clockwise."""
    m=native(); initial=config(m)
    m.control(2); upload(m,stream(16,16)); m.gui()
    assert set_pages(m,3)[6:9]==bytes([0,0,3])
    if direction==-1:  # start on the last host page, as after a counter-clockwise entry
        m.control(4); turn(m,1); m.control(4); turn(m,1)
    shown=[]
    for _ in range(3+6+3):
        m.control(4); m.trace.clear(); m.sent.clear(); turn(m,direction)
        state=('P',m.state().page) if m.state().active else page(m)
        shown.append(state)
        knob=[r[6] for r in events(m) if r[5]==0x21]
        assert knob==[direction&255]
        if m.state().active:
            assert page_events(m)==[(m.state().page,3)]
            assert not index_saves(m)
        m.advance(2100)
    if direction==1:
        assert shown==[('P',1),('P',2),0,1,2,3,4,5,('P',0),('P',1),('P',2),0]
    else:
        assert shown==[('P',1),('P',0),5,4,3,2,1,0,('P',2),('P',1),('P',0),5]
    m.control(3); assert not m.state().armed
    assert not any(r['address']=='0x179d8' for r in calls(m))


def test_host_page_entry_event_order_and_status():
    m=native(); m.control(2); set_pages(m,3)
    for _ in range(8): m.control(4); turn(m,1)        # host pages 0 -> 1 -> 2, then stock 0..5
    assert not m.state().active and page(m)==5
    m.sent.clear(); m.control(4); m.sent.clear(); turn(m,1)
    assert m.state().active and m.state().page==0
    seq=[r[5] for r in m.sent if r[0]==0xa5 and r[1]==0x3b and r[5] in (0x20,0x21,0x22)]
    assert seq==[0x21,0x22,0x20]  # page before visibility: the host sends the right frame first
    m.control(4); status=m.sent[-1]
    assert status[5]==4 and status[3]==18 and status[5+13:5+17]==bytes([1,1,0,3])
    assert set_pages(m,2)[6:9]==bytes([0,0,2])
    turn(m,1); assert m.state().page==1
    assert set_pages(m,1)[6:9]==bytes([0,0,1]) and m.state().page==0
    for bad in (0,9): assert set_pages(m,bad)[6]==0xee
    assert m.state().pages==1


def test_one_host_page_is_the_previous_behaviour():
    m=native(); m.control(2); upload(m,stream(16,16)); m.gui()
    set_pages(m,1); m.sent.clear()
    m.control(4); turn(m,1)
    assert not m.state().active and page(m)==0 and not page_events(m)


@pytest.mark.parametrize('seed',[3,4])
def test_random_knob_walk_with_host_pages_keeps_stock_animation_timers(seed):
    import random
    rng=random.Random(seed)
    reference=_stock_reference()
    m=native()
    m.control(2); upload(m,stream(16,16)); m.gui(); set_pages(m,3)
    for _ in range(48):
        roll=rng.random()
        if roll<0.06: m.control(3)
        elif roll<0.12: m.control(2)
        elif roll<0.16: set_pages(m,rng.randint(1,4))
        else: m.control(4)
        turn(m,rng.choice([1,-1]))
        assert m.state().page<max(1,m.state().pages)
    m.control(3)
    assert not m.state().armed and not m.state().active
    assert not any(r['address']=='0x179d8' for r in calls(m))
    assert _walk(m)==reference


def takeover(m,on):
    """A5 3B 07 <on>: knob takeover for this session (turns only reported)."""
    p=bytearray([0xa5,0x3b,0,3,0,0x07,on]); p.append(~sum(p[1:])&255)
    m.hid(bytes(p)); m.gui()
    for _ in range(4): m.gui()
    return [r for r in m.sent if r[0]==0xa5 and r[1]==0x3b and r[5]==0x07][-1]


@pytest.mark.parametrize('variant',['dev','release'])
def test_takeover_reports_turns_and_moves_no_page(variant):
    """With takeover a physical turn is reported (21) and nothing else happens: no page, host page or
    visibility change and no configuration call, wherever the knob is (slot shown or a stock page)."""
    m=StockGUI(variant); m.bootstrap(); m.tick(500); m.trace.clear(); initial=config(m)
    m.control(2); upload(m,stream(16,16)); m.gui(); set_pages(m,3)
    assert takeover(m,1)[6:8]==bytes([0,1])
    for direction in (1,1,1,1,-1,-1,1,-1):
        m.control(4); m.trace.clear(); m.sent.clear(); turn(m,direction); m.advance(2100)
        assert [(r[3],r[6],r[7]) for r in events(m)]==[(4,direction&255,1)]   # reported as taken
        assert m.state().active and m.state().page==0 and not page_events(m) and not calls(m)
    assert config(m)==initial
    # Leave the slot for a stock page with takeover off, then take over again: the stock page stays.
    takeover(m,0)
    for _ in range(3): m.control(4); turn(m,1)
    assert not m.state().active; stock=page(m); m.advance(2100); saved=config(m)
    takeover(m,1)
    for direction in (1,-1,1):
        m.control(4); m.trace.clear(); turn(m,direction); m.advance(2100)
        assert page(m)==stock and not m.state().active and not calls(m)
    assert config(m)==saved


def test_takeover_lets_host_steps_through_unreported():
    m=native(); m.control(2); upload(m,stream(16,16)); m.gui(); set_pages(m,3); takeover(m,1)
    m.control(4); m.sent.clear()
    assert inject(m,1)[6]==0
    assert m.state().page==1 and page_events(m)==[(1,3)] and not [r for r in events(m) if r[5]==0x21]
    assert m.state().pass_==0
    m.sent.clear(); turn(m,1)  # the next physical turn is taken over again
    assert m.state().page==1 and [(r[3],r[6],r[7]) for r in events(m)]==[(4,1,1)]
    m.sent.clear(); inject(m,0xff)
    assert m.state().page==0 and not [r for r in events(m) if r[5]==0x21]


def test_takeover_ends_with_the_session_and_needs_one():
    m=native()
    m.control(2); m.sent.clear(); turn(m,1)
    assert [(r[3],r[6]) for r in events(m) if r[5]==0x21]==[(3,1)]   # a plain turn: two fields, not taken
    m.control(3)
    assert takeover(m,1)[6]==0xee            # no session: refused
    m.control(2); assert takeover(m,1)[6:8]==bytes([0,1])
    assert takeover(m,2)[6:8]==bytes([0xee,1])
    m.control(3); assert not m.state().takeover and not m.state().armed
    m.control(2); assert not m.state().takeover  # a new session starts with stock paging
    takeover(m,1)
    m.advance(10100); m.gui()                    # host timeout
    assert not m.state().armed and not m.state().takeover
    before=page(m); turn(m,1)
    assert page(m)!=before or m.state().active   # the knob pages again
