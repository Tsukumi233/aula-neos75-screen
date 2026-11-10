"""v2 geometry, borrowed-pool ownership and original cache-rebuild regressions."""
import struct

import pytest
from unicorn import UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3

from emulator import Machine
from make_patch import BACKUP, BASE, CODE, check_input
from test_runtime import minimal_jpeg, packets, stream, upload

RGB = 0x800fe604
RGB_BYTES = 480 * 360 * 2
POOL_END = RGB + 0x7e900


def jpeg_stream(w, h):
    jpg = minimal_jpeg(w, h)
    return b'UB' + struct.pack('>HHH', w, h, 1) + b'\0' + struct.pack('>I', len(jpg)) + jpg


@pytest.mark.parametrize('variant', ['dev', 'release'])
@pytest.mark.parametrize('size', [(480, 360), (320, 320), (480, 2), (2, 360), (160, 120)])
def test_jpeg_canvas_geometry_and_exact_output_bounds(variant, size):
    m = Machine(variant)
    m.uc.mem_write(RGB - 4, b'HEAD')
    m.uc.mem_write(RGB + RGB_BYTES, b'TAIL')
    m.uc.mem_write(POOL_END, b'GAP!')
    m.control(2)
    root = m.objects[m.state().screen]
    assert root['size'] == (480, 360)
    assert root['padding'] == (0, 0, 0, 0)
    assert root['bg_color'] == (0,) and root['bg_opa'] == (255,)
    assert m.objects[m.state().image]['pos'] == (0, 0)
    assert m.objects[m.state().image]['size'] == (480, 360)
    assert m.objects[m.state().image]['hidden']
    w, h = size
    upload(m, jpeg_stream(w, h), 0x5a)
    st = m.state()
    assert st.ok == 1 and st.errors == 0 and st.rgb == RGB
    obj = m.objects[st.image]
    assert obj['pos'] == ((480 - w) // 2, (360 - h) // 2)
    assert obj['size'] == (w, h) and not obj['hidden']
    assert st.descriptor.header == 4 | w << 10 | h << 21
    assert st.descriptor.size == w * h * 2 and st.descriptor.data == RGB
    assert bytes(m.uc.mem_read(RGB, w*h*2)) == b'\x34\x12' * (w*h)
    assert m.decode_calls[-1] == (0x800d4200, st.jpeg_len, 0, RGB, RGB_BYTES, 1)
    assert bytes(m.uc.mem_read(RGB-4, 4)) == b'HEAD'
    assert bytes(m.uc.mem_read(RGB+RGB_BYTES, 4)) == b'TAIL'
    assert bytes(m.uc.mem_read(POOL_END, 4)) == b'GAP!'
    assert m.state().magic == 0x3154534e
    assert 0x2637c not in m.calls


@pytest.mark.parametrize('size', [(482, 360), (480, 362), (479, 360), (480, 359), (0, 2)])
def test_invalid_canvas_size_rejected_before_pixel_mutation(size):
    m = Machine(); m.control(2)
    m.uc.mem_write(RGB, b'UNCHANGED')
    upload(m, jpeg_stream(*size), 0x5a)
    assert m.state().last_error == 3 and not m.decode_calls
    assert bytes(m.uc.mem_read(RGB, 9)) == b'UNCHANGED'


def test_full_canvas_rgb_exceeds_input_cap_without_decode_or_allocation():
    m = Machine(); m.control(2)
    first = b'\xba\x06\xaa\x5b' + struct.pack('>I', 9 + RGB_BYTES)
    m.hid(first); m.gui()
    assert m.state().last_error == 3 and m.state().state == 0
    assert not m.decode_calls and 0x2637c not in m.calls


def test_rgb_is_centered_and_hidden_for_every_bounded_copy_callback():
    m = Machine(); m.control(2)
    upload(m, stream()); m.gui()
    assert not m.objects[m.state().image]['hidden']
    data = stream(320, 240)
    reports = list(packets(data))
    for p in reports[:-1]:
        m.hid(p); m.gui()
    m.hid(reports[-1]); m.gui()
    assert m.state().state == 3 and m.objects[m.state().image]['hidden']
    writes = []

    def observe(uc, access, address, size, value, unused):
        if RGB <= address < RGB + RGB_BYTES:
            assert m.objects[m.state().image]['hidden']
            writes.append(size)

    m.uc.hook_add(UC_HOOK_MEM_WRITE, observe, begin=RGB, end=RGB+RGB_BYTES-1)
    count = 0
    while m.state().state:
        before = sum(writes)
        m.gui(); count += 1
        assert sum(writes) - before <= 4096
        assert count <= 38
    assert count == 38 and sum(writes) == 320*240*2
    assert bytes(m.uc.mem_read(RGB, len(data)-9)) == data[9:]
    assert m.objects[m.state().image]['pos'] == (80, 60)
    assert m.objects[m.state().image]['size'] == (320, 240)
    assert not m.objects[m.state().image]['hidden'] and m.state().ok == 2


def test_failed_decode_stays_hidden_and_reenter_hides_stale_descriptor():
    m = Machine(); m.control(2)
    upload(m, jpeg_stream(480, 360), 0x5a)
    assert not m.objects[m.state().image]['hidden']
    m.decode_success = False
    upload(m, jpeg_stream(480, 360), 0x5a)
    assert m.state().last_error == 2 and m.objects[m.state().image]['hidden']
    m.decode_success = True
    upload(m, jpeg_stream(16, 16), 0x5a)
    assert m.state().ok == 2 and not m.objects[m.state().image]['hidden']
    m.control(3); m.control(2)
    assert m.objects[m.state().image]['hidden']


class StockResource(Machine):
    """Execute original cache eviction and resource lookup; stub all file/peripheral I/O."""
    def __init__(self):
        self.stock_mode = False
        super().__init__()
        original = BACKUP.read_bytes(); check_input(original)
        self.uc.mem_write(BASE, original[:CODE-BASE])
        self.closed = []
        self.resource_decodes = []
        self.stubs.pop(0x23fa0)
        self.stubs.pop(0x235d4)
        self.stubs.pop(self.symbols['stock_resource'])
        self.stubs.update({0x0906e: self.memzero, 0x26508: lambda: 0,
                           0x7d000: self.close, 0x084a6: lambda: 0,
                           0x2d148: lambda: 0, 0x1cb88: self.resource_decode,
                           0x11dbc: lambda: 0})
        self.symbols['original_resource'] = 0x3230c
        self.stock_mode = True

    def hook(self, uc, address, size, data):
        ranges = ((0x23fa0, 0x23ff6), (0x24814, 0x2483a),
                  (0x235d4, 0x235e2), (0x3230c, 0x3257c))
        if self.stock_mode and any(a <= address < b for a, b in ranges):
            return
        super().hook(uc, address, size, data)

    def memzero(self):
        dst, n = (self.uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1))
        self.uc.mem_write(dst, bytes(n)); return dst

    def close(self):
        self.closed.append(self.uc.reg_read(UC_ARM_REG_R1)); return 0

    def resource_decode(self):
        args = tuple(self.uc.reg_read(r) for r in
                     (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        self.resource_decodes.append(args)
        self.uc.mem_write(RGB, b'STOCK RGB')
        self.uc.mem_write(0x20003b8c, struct.pack('<HH', 480, 360))
        return args[3]


def test_leave_closes_real_lvgl_cache_and_same_resource_is_redecoded():
    m = StockResource()
    cache, decoder, header, name = 0x80004000, 0x80004100, 0x80010000, 0x80004200
    entry = header + 0x50
    for addr, value in ((0x200043c4, cache), (decoder+12, 0x7d001),
                        (0x2000bd10, header), (0x200045f8, header),
                        (header+0x40, 1), (entry-4, 100), (entry-8, 0), (entry-12, 0),
                        (0x20003b78, entry), (0x2000bd94, RGB-4)):
        m.uc.mem_write(addr, struct.pack('<I', value))
    m.uc.mem_write(0x200045de, struct.pack('<H', 1))
    m.uc.mem_write(name, b'S:/stock.bin\0')
    m.uc.mem_write(entry, b'S:/stock.bin\0')
    m.uc.mem_write(cache, struct.pack('<11I', decoder, name, 0, 0, 1, *([0]*6)))
    m.uc.mem_write(RGB-4, struct.pack('<I', 4 | 480 << 10 | 360 << 21))
    m.uc.mem_write(RGB, b'RT PIXELS')
    assert m.call('original_resource', name) == 0 and not m.resource_decodes
    m.control(2)
    assert m.call('rt_resource', name) == 2 and not m.resource_decodes

    def reload():
        assert m.state().active == 0
        assert bytes(m.uc.mem_read(cache, 0x2c)) == bytes(0x2c)
        assert bytes(m.uc.mem_read(0x20003b78, 4)) == bytes(4)
        assert bytes(m.uc.mem_read(0x2000bd94, 4)) == bytes(4)
        return 0

    m.stubs[0x2b120] = reload
    m.control(3)
    assert m.closed == [cache]
    assert 0x17a1c not in m.calls and 0x2e010 not in m.calls
    # Same resource name and pool: the real stock lookup must miss its old cookie.
    assert m.call('rt_resource', name) == 0
    assert m.resource_decodes == [(0x800d4200, 100, 0, RGB)]
    assert bytes(m.uc.mem_read(RGB, 9)) == b'STOCK RGB'
    assert struct.unpack('<I', m.uc.mem_read(0x2000bd94, 4))[0] == RGB-4
