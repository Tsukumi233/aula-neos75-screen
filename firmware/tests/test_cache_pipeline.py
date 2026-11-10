"""Smoothness features of patch 3 (HELLO flags bit 4): 5 ms GUI tick while shown, pipelined frame starts,
and the host page cache that shows a page the moment the knob reaches it."""
import struct
import zlib

import pytest

from emulator import Machine
from test_runtime import (checksum, frame_done, full_canvas, jpeg_stream, minimal_jpeg, packets,
                          region_stream, upload)

RGB_BASE = 0x800fe604
CACHE_BASE = RGB_BASE + 480 * 360 * 2 + 81920
CACHE_BYTES = 0x7e900 - 480 * 360 * 2 - 81920


def set_pages(m, count):
    p = bytearray([0xa5, 0x3b, 0, 3, 0, 0x06, count]); p.append(~sum(p[1:]) & 255)
    m.hid(bytes(p)); m.gui()
    return [r for r in m.sent if r[0] == 0xa5 and r[1] == 0x3b and r[5] == 0x06][-1]


def cache_stream(page, jpeg=None):
    j = jpeg if jpeg is not None else minimal_jpeg(480, 360)
    return b'UC' + bytes([page, 0]) + struct.pack('>I', len(j)) + j


def done_statuses(m):
    return [p[6] for p in m.sent if p[0] == 0xa5 and p[1] == 0x3b and p[5] == 0x10]


def knob(m, direction):
    m.pending_knobs.append(direction)
    for _ in range(4): m.gui()        # one reply leaves per tick


def pattern(jpeg):
    return struct.pack('<H', zlib.crc32(jpeg) & 0xffff) * 4


def variant_jpeg(tag):
    """A distinct valid 480x360 JPEG per tag (a COM segment after SOI)."""
    j = minimal_jpeg(480, 360)
    com = b'\xff\xfe' + struct.pack('>H', len(tag) + 2) + tag
    return j[:2] + com + j[2:]


# --- 5 ms tick ---------------------------------------------------------------------------------------

def test_gui_timer_period_is_5_ms_while_shown_and_10_otherwise():
    m = Machine(); timer = 0x2000e800
    m.uc.mem_write(timer, struct.pack('<I', 10))
    m.call('rt_gui', timer)
    assert struct.unpack('<I', m.uc.mem_read(timer, 4))[0] == 10
    m.control(2); m.call('rt_gui', timer)
    assert struct.unpack('<I', m.uc.mem_read(timer, 4))[0] == 5
    m.control(3); m.call('rt_gui', timer)
    assert struct.unpack('<I', m.uc.mem_read(timer, 4))[0] == 10


@pytest.mark.parametrize('variant,flags', [('dev', 127), ('release', 127)])
def test_hello_flags_report_the_smoothness_features(variant, flags):
    m = Machine(variant); m.control(1)
    p = m.sent[-1]; checksum(p)
    assert p[5 + 20] == flags


# --- pipelining --------------------------------------------------------------------------------------

def test_a_start_behind_a_complete_frame_shows_that_frame_first():
    m = Machine(); full_canvas(m); m.sent.clear()
    a, b = region_stream(0, 0, 16, 16), region_stream(16, 0, 16, 16)
    reports = list(packets(a, 0x5a)) + list(packets(b, 0x5a))
    assert len(reports) <= 32
    for r in reports: m.hid(r)            # both frames queued before one GUI tick
    for _ in range(6): m.gui()
    assert done_statuses(m) == [1, 1]     # no 04 busy: A was shown when B's start came
    assert [area for _, area in m.invalidated[-2:]] == [(0, 0, 15, 15), (16, 0, 31, 15)]
    assert m.state().ok == 3 and m.state().errors == 0


def test_rgb_copy_in_progress_keeps_the_next_start_queued():
    m = Machine(); m.control(2)
    big = b'WQ' + struct.pack('>HHH', 64, 64, 1) + b'\0' + bytes(64 * 64 * 2)   # copied over ticks
    upload(m, big, 0x5b)
    for p in packets(region_stream(0, 0, 16, 16), 0x5a): m.hid(p)
    for _ in range(8): m.gui()
    assert 4 not in done_statuses(m)


# --- page cache --------------------------------------------------------------------------------------

def test_set_pages_reports_the_cache_slot_size():
    m = Machine(); m.control(2)
    for count in (1, 3, 8):
        r = set_pages(m, count)
        assert r[6:9] == bytes([0, 0, count]) and struct.unpack_from('<I', r, 9)[0] == (CACHE_BYTES // count) & ~3


def test_cached_page_is_shown_when_the_knob_reaches_it():
    m = Machine(); full_canvas(m); m.decode_pattern = 'input'
    slot = struct.unpack_from('<I', set_pages(m, 3), 9)[0]
    pages = {p: variant_jpeg(b'page%d' % p) for p in (1, 2)}
    for p, j in pages.items():
        upload(m, cache_stream(p, j), 0x5a); m.gui()
        assert frame_done(m)[6] == 1
    assert bytes(m.uc.mem_read(CACHE_BASE + slot, len(pages[1]))) == pages[1]
    before = bytes(m.uc.mem_read(RGB_BASE, 8))
    assert before != pattern(pages[1])                           # storing does not touch the canvas
    m.sent.clear(); knob(m, 1)
    assert m.state().page == 1 and m.state().active
    assert bytes(m.uc.mem_read(RGB_BASE, 8)) == pattern(pages[1])
    assert m.state().canvas == 1 and not m.objects[m.state().image]['hidden']
    assert [p[5] for p in m.sent if p[0] == 0xa5 and p[5] in (0x21, 0x22)] == [0x21, 0x22]
    knob(m, 1)
    assert bytes(m.uc.mem_read(RGB_BASE, 8)) == pattern(pages[2])
    calls = len(m.decode_calls)
    knob(m, -1); knob(m, -1)                                     # page 0 has no cache: canvas stays
    assert m.state().page == 0 and len(m.decode_calls) == calls + 1


def test_corrupted_cache_is_never_shown():
    m = Machine(); full_canvas(m); m.decode_pattern = 'input'
    slot = struct.unpack_from('<I', set_pages(m, 2), 9)[0]
    upload(m, cache_stream(1, variant_jpeg(b'x')), 0x5a); m.gui()
    m.uc.mem_write(CACHE_BASE + slot + 40, b'\x00')             # e.g. stock code reused the pool
    calls = len(m.decode_calls); knob(m, 1)
    assert m.state().page == 1 and len(m.decode_calls) == calls and m.state().cache_len[1] == 0


def test_a_stream_of_the_previous_page_is_superseded():
    m = Machine(); full_canvas(m)
    set_pages(m, 2)
    upload(m, cache_stream(1), 0x5a); m.gui()
    first = list(packets(jpeg_stream(480, 360), 0x5a))
    for p in first[:3]: m.hid(p); m.gui()                       # an upload of page 0 is arriving
    assert m.state().state == 1
    m.sent.clear(); knob(m, 1)
    assert done_statuses(m) == [7] and m.state().state == 0
    for p in first[3:]: m.hid(p); m.gui()                       # its remaining packets are ignored
    assert done_statuses(m) == [7]


def test_entering_the_slot_by_knob_shows_the_cached_page():
    m = Machine(); full_canvas(m); m.decode_pattern = 'input'
    set_pages(m, 3)
    j = variant_jpeg(b'last')
    upload(m, cache_stream(2, j), 0x5a); m.gui()
    knob(m, -1)                                                  # page 0 -> back to stock page 6
    assert not m.state().active
    m.uc.mem_write(0x200044ad, b'\x00')                          # on stock page 1
    knob(m, -1)                                                  # counter-clockwise: the last host page
    assert m.state().active and m.state().page == 2
    assert bytes(m.uc.mem_read(RGB_BASE, 8)) == pattern(j) and m.state().canvas == 1


@pytest.mark.parametrize('case', ['page', 'size', 'jpeg', 'reserved'])
def test_cache_uploads_are_validated(case):
    m = Machine(); full_canvas(m)
    slot = struct.unpack_from('<I', set_pages(m, 8), 9)[0]
    j = minimal_jpeg(480, 360)
    data = {'page': cache_stream(8, j),
            'size': cache_stream(1, j + b'\0' * (slot - len(j) + 2)),
            'jpeg': cache_stream(1, minimal_jpeg(320, 320)),
            'reserved': b'UC\x01\x01' + struct.pack('>I', len(j)) + j}[case]
    upload(m, data, 0x5a); m.gui()
    assert frame_done(m)[6] in (2, 3) and not any(m.state().cache_len)


def test_changing_the_page_count_forgets_the_cache():
    m = Machine(); full_canvas(m)
    set_pages(m, 2)
    upload(m, cache_stream(1), 0x5a); m.gui()
    assert m.state().cache_len[1]
    set_pages(m, 2); assert m.state().cache_len[1]               # same count: kept
    set_pages(m, 3); assert not any(m.state().cache_len)


def test_region_scratch_stops_before_the_cache():
    m = Machine(); full_canvas(m)
    upload(m, region_stream(0, 0, 320, 128), 0x5a); m.gui()       # 81920 bytes: the largest allowed
    assert frame_done(m)[6] == 1
    call = m.decode_calls[-1]
    assert call[3] + call[4] == CACHE_BASE
    upload(m, region_stream(0, 0, 480, 96), 0x5a); m.gui()        # 92160 bytes: refused
    assert frame_done(m)[6] == 3



# --- page tags ---------------------------------------------------------------------------------------

def tagged(data, tag, kind=0x5a):
    reports = list(packets(data, kind))
    start = bytearray(reports[0].ljust(64, bytes(1))); start[8] = tag
    return [bytes(start)] + reports[1:]


def send(m, reports):
    for r in reports: m.hid(r); m.gui()
    for _ in range(4): m.gui()


def test_a_frame_tagged_with_the_shown_page_is_shown():
    m = Machine(); full_canvas(m); set_pages(m, 3); m.sent.clear()
    send(m, tagged(region_stream(0, 0, 16, 16), 1))           # page 0 + 1
    assert done_statuses(m) == [1] and m.invalidated[-1][1] == (0, 0, 15, 15)


def test_a_frame_of_a_page_the_knob_left_is_dropped():
    m = Machine(); full_canvas(m); set_pages(m, 3)
    knob(m, 1); assert m.state().page == 1
    before = bytes(m.uc.mem_read(RGB_BASE, 480 * 360 * 2)); m.sent.clear(); n = len(m.invalidated)
    send(m, tagged(region_stream(0, 0, 16, 16), 1))           # drawn for page 0
    assert done_statuses(m) == [7] and len(m.invalidated) == n
    assert bytes(m.uc.mem_read(RGB_BASE, 480 * 360 * 2)) == before
    send(m, tagged(jpeg_stream(480, 360), 1))                 # a full frame too
    assert done_statuses(m) == [7, 7] and m.state().canvas == 1
    send(m, tagged(region_stream(0, 0, 16, 16), 2))           # drawn for page 1: shown
    assert done_statuses(m) == [7, 7, 1]
    send(m, list(packets(region_stream(16, 0, 16, 16), 0x5a)))   # untagged: always shown
    assert done_statuses(m) == [7, 7, 1, 1]


def test_cache_uploads_ignore_the_tag():
    m = Machine(); full_canvas(m); set_pages(m, 3)
    send(m, tagged(cache_stream(2), 1))
    assert done_statuses(m)[-1] == 1 and m.state().cache_len[2]
