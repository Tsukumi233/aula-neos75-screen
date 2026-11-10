"""AULA screen original-image restore. Offline, exact-address/data allow-list.
Run with the supplied venv Python -B. Never use python -O (assertions required).
All explicit USB control transfers go through Session.ctrl; no set_configuration,
set_interface_altsetting, reset, detach, endpoint writes, or vendor requests.
Windows WinUSB's default interface alternate setting is 0; claim interface 0 only.
"""
import argparse
import array
import collections
import datetime
import hashlib
import json
import os
import struct
import sys
import time
import traceback
from pathlib import Path

sys.dont_write_bytecode = True
import enter_dfu as hid
import libusb_package
import usb.backend.libusb1
import usb.core

ROOT = Path(__file__).resolve().parent
# The stock-firmware backup of this keyboard: the folder in NEOS75_BACKUP, else ~/Tools/aula_neos75_backup.
BACKUP = Path(os.environ.get('NEOS75_BACKUP') or Path.home() / 'Tools' / 'aula_neos75_backup')
BUILD = 'V1.0.3 Sep  6 2026 23:18:16'
MEMORY_NAME = '@Internal Flash  /0x00000000/8*4Ka,120*4Kg,3583*512Ka,4096*4Kg'
FUNC = bytes.fromhex('09 21 0b ff 00 00 10 1a 01')
REGIONS = [
    ('bootloader', 0, 0x8000, 'bootloader_0000_8000.bin', '3392e7e17bbb1ec414c586d1d0e82c892deaf4fd2ae227686bd207bb5d58fc40'),
    ('app', 0x8000, 0x77000, 'app_8000_7F000.bin', '66679ff2cb06ab5a5e47673f679e14e68bf2082fb6401b3808ca58d7f2e1a40c'),
    ('config', 0x7F000, 0x1000, 'config_7F000_80000.bin', 'd56749fcb8522fc391514f99a748b08f61348c2c38445f7be802ee0c297c7a11'),
]
START = time.monotonic()
PHASE = 'initializing'
STATE = {}
IMAGES = {}
BE = None
ACTIVE = None


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(name, data):
    target = (ROOT / name).resolve()
    assert target.is_relative_to(ROOT) and target != ROOT
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        target.write_bytes(data)
    else:
        target.write_text(data, encoding='utf-8')


def event(kind, **fields):
    rec = dict(time=datetime.datetime.now().astimezone().isoformat(),
               elapsed=round(time.monotonic() - START, 4), phase=PHASE, kind=kind)
    rec.update(fields)
    with (ROOT / 'commands.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return rec


def persist():
    save('result.json', json.dumps(STATE, indent=2, ensure_ascii=False))


def phase(name):
    global PHASE
    PHASE = name
    event('phase_start')
    print(name, flush=True)


def backups():
    for key, addr, length, name, expected in REGIONS:
        data = (BACKUP / name).read_bytes()
        actual = sha(data)
        event('backup_hash', file=name, length=len(data), sha256=actual)
        STATE.setdefault('backup_hashes', {})[name] = actual
        assert len(data) == length and actual == expected, 'Backup mismatch: ' + name
        IMAGES[key] = data
    assert len(range(0x8000, 0x4B000, 4096)) == 67
    persist()


def hid_action(enter=False):
    """Same transport and exact packets as enter_dfu.py --version / --enter."""
    for path in hid.paths():
        assert 'VID_5542&PID_0001' in path.upper()
        handle = hid.k32.CreateFileW(path, hid.GENERIC_RW, hid.FILE_SHARE_RW, None,
                                    hid.OPEN_EXISTING, hid.FILE_FLAG_OVERLAPPED, None)
        if handle in (None, hid.INVALID):
            continue
        try:
            results = {}
            for cls, sub in ([(0x13, 2)] if enter else [(0x11, 1), (0x11, 2)]):
                pkt = hid.build(cls, sub)
                assert pkt in hid.ALLOWED and len(pkt) == 64
                event('hid_attempt', category=f'HID {cls:02X} {sub:02X}', packet=pkt.hex())
                reply = hid.send(handle, pkt, expect_reply=not enter)
                event('hid_complete', category=f'HID {cls:02X} {sub:02X}',
                      reply=reply.hex() if reply else None, text=hid.text(reply))
                if not enter:
                    assert reply and reply[:2] == pkt[:2], 'Unexpected HID reply'
                    results[sub] = hid.text(reply)
            if not enter:
                assert results[2] == BUILD, 'Unexpected build: ' + repr(results)
                STATE.setdefault('versions', []).append(dict(phase=PHASE, **{'model': results[1], 'build': results[2]}))
                persist()
            return
        finally:
            hid.k32.CloseHandle(handle)
    raise RuntimeError('5542:0001 screen HID not found')


def devices():
    # libusb cached enumeration only; do not open any other PID.
    return list(usb.core.find(find_all=True, idVendor=0x5542, backend=BE))


def wait_pid(pid, timeout=25):
    deadline = time.monotonic() + timeout
    while True:
        found = [d for d in devices() if d.idProduct == pid]
        if found:
            assert len(found) == 1, 'Multiple matching screen devices'
            event('enumeration', vid='5542', pid=f'{pid:04X}', address=found[0].address)
            return found[0]
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Timeout waiting for 5542:{pid:04X}')
        time.sleep(0.25)


class Session:
    def __init__(self, dev, connect=True):
        assert dev is None or (dev.idVendor, dev.idProduct) == (0x5542, 0x1000)
        self.dev, self.handle = dev, None
        self.address = None
        self.upload_next = None
        self.can_exit = False
        self.claimed = False
        if connect:
            self.handle = BE.open_device(dev._ctx.dev)
            BE.claim_interface(self.handle, 0)
            self.claimed = True
            event('host_interface_claim', interface=0, alternate=0,
                  note='WinUSB default alt 0; no explicit SET_CONFIGURATION or SET_INTERFACE')

    def ctrl(self, direction_in, req, value, payload, descriptor=False):
        # These assertions execute BEFORE any backend control transfer.
        assert __debug__, 'Optimized Python is forbidden'
        assert self.dev is None or (self.dev.idVendor, self.dev.idProduct) == (0x5542, 0x1000)
        assert isinstance(value, int) and 0 <= value <= 0xFFFF
        idx = 0
        meta = {}
        if descriptor:
            assert direction_in and req == 6 and isinstance(payload, int)
            assert value >> 8 in (1, 2, 3) and 0 < payload <= 4096
            idx = 0x0409 if value >> 8 == 3 else 0
            request_type, category = 0x80, 'GET_DESCRIPTOR'
        elif direction_in:
            assert req in (2, 3, 5) and isinstance(payload, int)
            request_type = 0xA1
            category = {2: 'UPLOAD', 3: 'GETSTATUS', 5: 'GETSTATE'}[req]
            if req == 2:
                assert self.address is not None and value >= 2 and self.upload_next == value
                start_addr = self.address + (value - 2) * 4096
                assert 0 < payload <= 4096 and 0 <= start_addr < start_addr + payload <= 0x80000
                meta['address'] = start_addr
            else:
                assert value == 0 and payload == (6 if req == 3 else 1)
        else:
            assert req in (1, 4, 6)
            request_type = 0x21
            data = bytes(payload or b'')
            if req in (4, 6):
                assert value == 0 and data == b''
                category = {4: 'CLRSTATUS', 6: 'ABORT'}[req]
            elif len(data) == 0:
                assert value == 0 and self.can_exit, 'Exit requires successful full readback comparison'
                category = 'DNLOAD EXIT'
            elif value == 0 and len(data) == 5 and data[0] == 0x21:
                addr = struct.unpack('<I', data[1:])[0]
                assert 0 <= addr < 0x80000
                category, meta['address'] = 'DNLOAD SET_ADDRESS', addr
            elif value == 0 and len(data) == 5 and data[0] == 0x41:
                assert PHASE in ('write', 'retry')
                addr = struct.unpack('<I', data[1:])[0]
                assert 0x8000 <= addr < 0x4B000 and addr % 4096 == 0
                category, meta['address'] = 'DNLOAD ERASE_SECTOR', addr
            else:
                assert PHASE in ('write', 'retry')
                assert value == 2 and len(data) == 4096 and self.address is not None
                assert 0x8000 <= self.address < 0x4B000 and self.address % 4096 == 0
                offset = self.address - 0x8000
                assert data == IMAGES['app'][offset:offset + 4096], 'Non-backup write blocked'
                category, meta['address'] = 'DNLOAD DATA', self.address
                meta['sha256'] = sha(data)
            payload = data
        buf = array.array('B', [0] * payload) if direction_in else array.array('B', payload)
        event('usb_attempt', category=category, bmRequestType=request_type,
              request=req, value=value, index=idx, length=len(buf), **meta)
        count = BE.ctrl_transfer(self.handle, request_type, req, value, idx, buf, 5000)
        result = bytes(buf[:count]) if direction_in else count
        event('usb_complete', category=category, length=count,
              response=result.hex() if direction_in and (len(result) <= 32 or descriptor) else None, **meta)
        assert count == len(buf), f'Short control transfer {category}: {count}/{len(buf)}'
        if category == 'DNLOAD SET_ADDRESS':
            self.address, self.upload_next = meta['address'], 2
        elif category in ('DNLOAD DATA', 'DNLOAD ERASE_SECTOR'):
            self.can_exit = False
            self.upload_next = None
            if category == 'DNLOAD ERASE_SECTOR':
                self.address = None
        elif category == 'UPLOAD':
            self.upload_next += 1
        return result

    def descriptor(self, kind, index=0, length=255):
        # Descriptor reads may be shorter than requested: request the header first.
        head_len = {1: 18, 2: 9, 3: 2}[kind]
        head = self.ctrl(True, 6, kind << 8 | index, head_len, descriptor=True)
        size = struct.unpack('<H', head[2:4])[0] if kind == 2 else head[0]
        assert head_len <= size <= 4096
        return head if size == head_len else self.ctrl(True, 6, kind << 8 | index, size, descriptor=True)

    def string(self, index):
        assert index > 0
        raw = self.descriptor(3, index)
        assert raw[1] == 3 and raw[0] == len(raw)
        return raw[2:].decode('utf-16-le')

    def validate_descriptors(self):
        raw = self.descriptor(1)
        assert len(raw) == 18 and raw[1] == 1
        assert struct.unpack('<HH', raw[8:12]) == (0x5542, 0x1000)
        assert raw[17] == 1
        product = self.string(raw[15])
        assert product == 'UBEST DFU Screen Firmware Update', product
        config = self.descriptor(2)
        assert config[1] == 2 and config[4] == 1
        entries, current, functions = [], None, []
        pos = 0
        while pos < len(config):
            length, kind = config[pos:pos + 2]
            assert length >= 2 and pos + length <= len(config)
            part = config[pos:pos + length]
            if kind == 4:
                assert length == 9 and part[2] == 0 and part[5:7] == b'\xfe\x01'
                current = dict(interface=part[2], alt=part[3], protocol=part[7], name=self.string(part[8]))
                assert current['name'] == MEMORY_NAME, current
                entries.append(current)
            elif kind == 0x21:
                assert current is not None and part == FUNC, part.hex()
                current['functional'] = part.hex(' ')
                functions.append(part)
            pos += length
        assert [e['alt'] for e in entries] == [0, 1]
        # The interface's shared functional descriptor follows both alternates.
        assert functions and all(f == FUNC for f in functions), 'Missing/mismatched DFU functional descriptor'
        record = dict(phase=PHASE, device=raw.hex(' '), config=config.hex(' '), product=product, alts=entries,
                      functional=FUNC.hex(' '), selected_alt=0)
        STATE.setdefault('descriptors', []).append(record)
        event('descriptors_validated', **record)
        persist()

    def status(self):
        raw = self.ctrl(True, 3, 0, 6)
        status, poll, state = raw[0], int.from_bytes(raw[1:4], 'little'), raw[4]
        assert status == 0 and state != 10, f'Abnormal DFU status={status} state={state}'
        return poll, state

    def idle(self):
        poll, state = self.status()
        assert state in (2, 5, 9), f'Unexpected idle-entry state={state}'
        if state != 2:
            self.ctrl(False, 6, 0, b'')
            poll, state = self.status()
            assert state == 2, f'ABORT failed state={state}'

    def poll_download(self):
        deadline = time.monotonic() + 60
        while True:
            poll, state = self.status()
            if state == 5:
                return
            assert state in (3, 4), f'Unexpected download state={state}'
            assert time.monotonic() + poll / 1000 <= deadline, 'Download polling timeout'
            event('poll_wait', milliseconds=poll)
            time.sleep(poll / 1000)

    def set_address(self, addr):
        self.idle()
        self.ctrl(False, 1, 0, b'\x21' + struct.pack('<I', addr))
        self.poll_download()

    def read(self, addr, length):
        assert length > 0 and 0 <= addr < addr + length <= 0x80000
        self.set_address(addr)
        self.idle()
        out = bytearray()
        block = 2
        while len(out) < length:
            out += self.ctrl(True, 2, block, min(4096, length - len(out)))
            block += 1
        self.idle()
        return bytes(out)

    def flag(self, label, expected):
        value = struct.unpack('<I', self.read(0x7F00, 4))[0]
        STATE.setdefault('flags', []).append(dict(phase=PHASE, label=label, value=f'0x{value:08X}'))
        event('flag', label=label, value=f'0x{value:08X}')
        persist()
        assert value == expected, f'Flag mismatch: {value:#010x} expected {expected:#010x}'
        return value

    def sector(self, addr):
        assert PHASE in ('write', 'retry')
        assert 0x8000 <= addr < 0x4B000 and addr % 4096 == 0
        self.idle()
        self.ctrl(False, 1, 0, b'\x41' + struct.pack('<I', addr))
        self.poll_download()
        self.set_address(addr)
        data = IMAGES['app'][addr - 0x8000:addr - 0x8000 + 4096]
        self.ctrl(False, 1, 2, data)
        self.poll_download()
        event('sector_done', address=addr, sha256=sha(data))

    def exit(self):
        assert self.can_exit
        self.idle()
        self.ctrl(False, 1, 0, b'')
        # Per known behavior: one GETSTATUS drives manifestation, then USB disconnects.
        poll, state = self.status()
        assert state in (6, 7, 8), f'Unexpected manifestation state {state}'
        event('manifestation', state=state, poll_ms=poll)
        if state == 6:
            time.sleep(poll / 1000)
            poll, state = self.status()
            assert state in (7, 8), f'Manifestation did not advance: {state}'
        self.close()
        wait_pid(0x0001)

    def close(self):
        if self.handle is not None:
            # Closing a host handle does not issue USB DFU exit/reset.
            if self.claimed:
                BE.release_interface(self.handle, 0)
                self.claimed = False
            BE.close_device(self.handle)
            self.handle = None
            event('host_interface_closed')


def open_dfu():
    global ACTIVE
    assert not any(d.idProduct == 0x1000 for d in devices()), 'Already in DFU; unexpected starting mode'
    hid_action(enter=True)
    ACTIVE = Session(wait_pid(0x1000))
    ACTIVE.validate_descriptors()
    ACTIVE.idle()
    return ACTIVE


def difference(data, reference, base):
    assert len(data) == len(reference)
    addresses = [base + i for i, (a, b) in enumerate(zip(data, reference)) if a != b]
    ranges = []
    for addr in addresses:
        if ranges and addr == ranges[-1][1] + 1:
            ranges[-1][1] = addr
        else:
            ranges.append([addr, addr])
    return dict(count=len(addresses), ranges=[[f'0x{a:X}', f'0x{b:X}'] for a, b in ranges],
                sectors=sorted({a & ~0xFFF for a in addresses}))


def compare(data, key, label):
    item = next(r for r in REGIONS if r[0] == key)
    diff = difference(data, IMAGES[key], item[1])
    record = dict(phase=PHASE, label=label, region=key, sha256=sha(data),
                  backup_sha256=item[4], equal=data == IMAGES[key], **diff)
    STATE.setdefault('comparisons', []).append(record)
    event('comparison', **record)
    persist()
    return record


def dump_full(session, label):
    results = {}
    for key, addr, length, name, expected in REGIONS:
        data = session.read(addr, length)
        save(f'{label}/{name}', data)
        results[key] = compare(data, key, label)
    assert results['bootloader']['equal'], 'Bootloader differs from backup'
    assert results['app']['equal'], 'Application differs from backup'
    session.flag(label, 0xFFFFFFFF)
    session.can_exit = True
    return results


def preflight(resume=False):
    global ACTIVE
    if not resume:
        phase('preflight_version')
        hid_action()
    phase('preflight_read')
    if resume and any(d.idProduct == 0x1000 for d in devices()):
        ACTIVE = session = Session(wait_pid(0x1000, timeout=1))
        session.validate_descriptors()
        session.idle()
    else:
        if resume:
            event('known_idle_timeout', note='DFU idle may auto-return after 180000ms')
            wait_pid(0x0001)
            hid_action()
        session = open_dfu()
    dump_full(session, 'preflight')
    phase('preflight_exit')
    session.exit()
    STATE['preflight_passed'] = True
    persist()


def restore():
    assert STATE.get('preflight_passed') and not STATE.get('write_started'), 'Require one successful preflight; no blind reruns'
    phase('before_write_version')
    hid_action()
    phase('write')
    session = open_dfu()
    STATE['write_started'] = True
    persist()
    t0 = time.monotonic()
    for i, addr in enumerate(range(0x8000, 0x4B000, 4096), 1):
        session.sector(addr)
        print(f'sector {i}/67 0x{addr:05X} done; {time.monotonic() - t0:.2f}s', flush=True)
    phase('write_readback')
    for attempt in range(4):
        data = session.read(0x8000, 0x77000)
        save(f'write_readback/app_attempt_{attempt}.bin', data)
        result = compare(data, 'app', f'write_readback_{attempt}')
        if result['equal']:
            break
        bad = result['sectors']
        assert all(0x8000 <= a < 0x4B000 for a in bad), 'Mismatch outside authorized write sectors; stay in DFU'
        assert attempt < 3, 'Three retries exhausted; stay in DFU'
        phase('retry')
        for addr in bad:
            session.sector(addr)
        phase('write_readback')
    session.flag('after_write_before_exit', 0x55AAAA55)
    session.can_exit = True
    STATE['write_readback_passed'] = True
    persist()
    phase('write_exit')
    session.exit()
    phase('boot_version')
    hid_action()
    phase('final_read')
    session = open_dfu()
    dump_full(session, 'final')
    phase('final_exit')
    session.exit()
    phase('final_version')
    hid_action()
    STATE['completed'] = True
    persist()


def inventory_final():
    result = [f'{d.idVendor:04X}:{d.idProduct:04X}' for d in devices() if d.idProduct in (0x0001, 0x1000)]
    STATE['final_device_ids'] = result
    event('final_inventory', devices=result)


def report():
    events = [json.loads(line) for line in (ROOT / 'commands.jsonl').read_text(encoding='utf-8').splitlines()]
    attempts = collections.Counter(e['category'] for e in events if e['kind'] in ('usb_attempt', 'hid_attempt'))
    complete = collections.Counter(e['category'] for e in events if e['kind'] in ('usb_complete', 'hid_complete'))
    timings = []
    for i, e in enumerate(events):
        if e['kind'] == 'phase_start':
            end = next((n for n in events[i + 1:] if n['kind'] in ('phase_start', 'run_end')), events[-1])
            timings.append((e['phase'], e['time'], round(end['elapsed'] - e['elapsed'], 4)))
    lines = ['# AULA Neos75 屏幕原厂应用原样写回报告', '',
             f"结果：{'全部自动验证通过' if STATE.get('completed') else '预检通过，等待写入' if STATE.get('preflight_passed') and not STATE.get('error') else '已停止'}。",
             f"最后阶段：`{PHASE}`；最终枚举：`{', '.join(STATE.get('final_device_ids', [])) or '未检测到目标设备'}`。", '',
             '## 范围与工具', '',
             '目标仅为独立屏幕 5542:0001 / 5542:1000，接口 0、默认 alt 0。',
             '真实擦写仅允许 0x8000–0x4AFFF，67 个 4096 字节扇区（274432 字节），含扇区尾部 FF；数据逐字节取自备份。',
             '未向引导区或配置页发出擦写命令；0x7F00 的变化由设备引导程序/应用自身产生。',
             f'写入工具：`{ROOT / "dfu_restore.py"}`；HID 工具：`{ROOT / "enter_dfu.py"}`。',
             '所有脚本以 Python -B 运行，禁止 -O；没有联网、驱动/系统设置更改、启动 Aula Hub 或结束进程。',
             '显式 USB 传输统一经过断言白名单；直接使用 libusb 后端，未调用 SET_CONFIGURATION、SET_INTERFACE、USB reset 或 DFU DETACH。',
             '接口通过 WinUSB 默认 alt 0 打开；libusb/Windows 的内部枚举及主机栈自动流量不等同于脚本显式命令，本日志不是硬件总线抓包。', '',
             '## 备份校验（SHA-256）', '', '| 文件 | 实测 SHA-256 |', '|---|---|']
    for name, digest in STATE.get('backup_hashes', {}).items():
        lines.append(f'| {name} | `{digest}` |')
    lines += ['', '## 执行步骤与耗时', '', '| 阶段 | 开始时间 | 耗时（秒） |', '|---|---|---:|']
    for name, stamp, duration in timings:
        lines.append(f'| {name} | {stamp} | {duration} |')
    lines += ['', '预检：先查询型号/构建串，进入 DFU 校验产品、两组接口名称、功能描述符，完整回读 0x0–0x80000；引导/应用必须一致，配置差异仅记录。通过后才允许退出。',
              '写入：重新进入 DFU，每扇区 ERASE_SECTOR → GETSTATUS 轮询 → SET_ADDRESS → GETSTATUS 轮询 → block 2 下载 4096 字节 → GETSTATUS 确认 status=0、state=5；按返回 pollTimeout 等待。',
              '写后：完整回读应用区 0x8000–0x7EFFF（487424 字节）逐字节比较；仅允许对授权范围内不一致扇区用原文重试，最多额外 3 轮。校验及标志值通过才允许零长度 DNLOAD。',
              '启动后：确认 5542:0001 和原构建串；再次进入 DFU 完整回读，核对引导区、应用区及标志清除，随后退出并再次查询版本。', '',
              '## 回读与差异', '', '| 阶段/文件集 | 区域 | 实测 SHA-256 | 逐字节一致 | 差异字节数 |', '|---|---|---|---|---:|']
    for r in STATE.get('comparisons', []):
        lines.append(f"| {r['label']} | {r['region']} | `{r['sha256']}` | {r['equal']} | {r['count']} |")
        if r['count']:
            lines.append(f"\n差异（绝对地址，闭区间）：`{json.dumps(r['ranges'])}`。\n")
    lines += ['', '## 0x7F00 标志字', '', '| 阶段 | 实测值 |', '|---|---|']
    for r in STATE.get('flags', []):
        lines.append(f"| {r['label']} | `{r['value']}` |")
    lines += ['', '## 运行态版本', '']
    for r in STATE.get('versions', []):
        lines.append(f"- {r['phase']}：型号 `{r['model']}`；构建串 `{r['build']}`。")
    lines += ['', '## 实际显式发送的命令类别与次数', '', '| 类别 | 调用尝试 | 成功完成 |', '|---|---:|---:|']
    for cat in sorted(attempts):
        lines.append(f'| {cat} | {attempts[cat]} | {complete[cat]} |')
    lines += ['', '零长度 DNLOAD 只在完整回读比较通过后发出。未发送的类别计数为 0；没有整片擦除或白名单外 HID/USB 命令。',
              '逐笔时间、参数、状态响应、pollTimeout、扇区数据哈希见 `commands.jsonl`；完整描述符、差异、所有文件哈希与结果见 `result.json`。', '',
              '## 异常、最终状态及边界', '',
              STATE.get('error', '设备传输、状态及数据验证未遇到异常。'),
              *STATE.get('resolved_tool_errors', []),
              '没有注入断电或故意损坏，未实测断电失败分支。各验证项目是否完成以上述实测记录为准。',
              '**仍需用户目视确认：屏幕显示、画面与操作是否正常。USB 枚举和版本查询无法替代显示效果检查。**', '']
    save('REPORT.md', '\n'.join(lines))


def main():
    global BE, STATE, PHASE, BACKUP
    assert __debug__, 'Run without -O'
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', required=True, choices=['preflight', 'preflight-resume', 'write'])
    parser.add_argument('--backup', type=Path, help='folder of the stock-firmware backup (default: NEOS75_BACKUP, else ~/Tools/aula_neos75_backup)')
    args = parser.parse_args()
    BACKUP = args.backup or BACKUP
    if (ROOT / 'result.json').exists():
        STATE = json.loads((ROOT / 'result.json').read_text(encoding='utf-8'))
    assert not STATE.get('completed'), 'Task already completed; no duplicate write'
    if args.phase == 'preflight-resume':
        assert STATE.get('error') == '停止于 preflight_read：AssertionError: '
        assert not STATE.get('write_started') and not STATE.get('preflight_passed')
        assert (ROOT / 'descriptor_diagnosis.bin').read_bytes() == bytes.fromhex(
            '09 02 24 00 01 01 02 c0 32 09 04 00 00 00 fe 01 02 06 '
            '09 04 00 01 00 fe 01 02 07 09 21 0b ff 00 00 10 1a 01')
        STATE.setdefault('resolved_tool_errors', []).append(
            '首次预检因工具误要求每个 alt 各含一份功能描述符而停止，当时无擦写、无 UPLOAD。'
            '只读诊断取得36字节配置描述符：两个alt共用末尾一份功能描述符，内容与给定事实完全一致。'
            '修正额外断言后继续预检；未放宽地址、数据或命令白名单。')
        del STATE['error']
    assert not STATE.get('error'), 'Previous exception requires review; no automatic continuation'
    event('run_start', action=args.phase)
    try:
        phase('backup_hashes')
        backups()
        BE = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
        assert BE is not None
        if args.phase in ('preflight', 'preflight-resume'):
            assert not STATE.get('preflight_passed'), 'Preflight already done'
            preflight(resume=args.phase == 'preflight-resume')
        else:
            restore()
    except BaseException as exc:
        STATE['error'] = f'停止于 {PHASE}：{type(exc).__name__}: {exc}'
        event('exception', error=STATE['error'], traceback=traceback.format_exc())
        print(STATE['error'], flush=True)
    finally:
        if ACTIVE:
            try:
                ACTIVE.close()
            except Exception as exc:
                event('close_error', error=repr(exc))
                STATE.setdefault('host_close_errors', []).append(repr(exc))
        try:
            if BE:
                inventory_final()
        except Exception as exc:
            STATE['inventory_error'] = repr(exc)
        STATE['last_phase'] = PHASE
        event('run_end')
        persist()
        report()
    return 1 if STATE.get('error') else 0


if __name__ == '__main__':
    sys.exit(main())
