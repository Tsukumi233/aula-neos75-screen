"""Build deterministic patches for the exact installed image, without USB access."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parent
# The stock-firmware backup of this keyboard: the folder in NEOS75_BACKUP, else ~/Tools/aula_neos75_backup.
BACKUP = Path(os.environ.get('NEOS75_BACKUP') or Path.home() / 'Tools' / 'aula_neos75_backup') / 'app_8000_7F000.bin'
SHA256 = '66679ff2cb06ab5a5e47673f679e14e68bf2082fb6401b3808ca58d7f2e1a40c'
BASE, APP_SIZE, CONTENT_END, CODE = 0x8000, 0x77000, 0x4aacc, 0x4ab00
# Each displaced instruction is position independent and replayed by a trampoline.
HOOKS = (
    ('ba', 0x31e6c, 'b0b582b0', 'rt_ba', 'stock_ba'),
    ('usb', 0x1cd9c, '2de9f041', 'rt_usb', 'stock_usb'),
    ('gui_timer', 0x3423c, 'f0b581b0', 'rt_gui', 'stock_poll'),
    ('static_image', 0x11914, '2de9f041', 'rt_static', 'stock_static'),
    ('animation', 0x338bc, '2de9f047', 'rt_anim', 'stock_anim'),
    ('resource_jpeg', 0x3230c, '2de9f04f', 'rt_resource', 'stock_resource'),
    ('page_switch', 0x3c128, '2de9f047', 'rt_page', 'stock_page'),
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_input(data):
    require(len(data) == APP_SIZE and digest(data) == SHA256, 'Installed backup SHA-256 mismatch')
    require(all(x == 255 for x in data[CONTENT_END-BASE:]), 'Unexpected data after installed image')
    for name, address, expected, *_ in HOOKS:
        check_bytes(data, address, bytes.fromhex(expected), name)
    check_bytes(data, 0x48248, bytes.fromhex('11e00200'), 'init_table')
    check_bytes(data, 0x26552, bytes.fromhex('06f09bf9'), 'heap_reserve_call')
    check_bytes(data, 0x11ab2, bytes.fromhex('05f0b3ff'), 'static_save_call')
    check_bytes(data, 0x3379c, bytes.fromhex('e4f71cf9'), 'status_save_call')
    check_bytes(data, 0x1d268, bytes.fromhex('fff714ff'), 'key_gui_call')


def check_bytes(data, address, expected, name):
    got = data[address-BASE:address-BASE+len(expected)]
    require(got == expected, f'{name} original bytes mismatch at 0x{address:x}: {got.hex()} != {expected.hex()}')


def branch(source, target, link=False):
    from keystone import Ks, KS_ARCH_ARM, KS_MODE_THUMB, KS_MODE_LITTLE_ENDIAN
    op='bl' if link else 'b.w'
    encoded, _ = Ks(KS_ARCH_ARM, KS_MODE_THUMB | KS_MODE_LITTLE_ENDIAN).asm(f'{op} #{target & ~1}', source)
    require(len(encoded) == 4, 'Hook branch must be four bytes')
    return bytes(encoded)


def compiler():
    path = ROOT / 'toolchain/bin/arm-none-eabi-gcc.exe'
    require(path.is_file(), 'Extract official Arm GNU 14.3.Rel1 toolchain under firmware/toolchain')
    return path


def build(data, variant, outdir):
    check_input(data)
    require(variant in ('dev', 'release'), 'Unknown variant')
    outdir.mkdir(parents=True, exist_ok=True)
    gcc = compiler()
    env = dict(os.environ, TEMP=str(ROOT/'.tmp'), TMP=str(ROOT/'.tmp'), SOURCE_DATE_EPOCH='0')
    (ROOT/'.tmp').mkdir(exist_ok=True)
    # Generate trampoline symbols before linking; all code is after the suffix.
    asm = ['.syntax unified', '.thumb', '.section .text.trampolines,"ax",%progbits', '.balign 4']
    for _, address, expected, _, symbol in HOOKS:
        asm += [f'.global {symbol}', '.thumb_func', f'{symbol}:',
                '.byte ' + ','.join('0x'+expected[i:i+2] for i in range(0,len(expected),2)),
                'ldr.w pc, 1f', '.balign 4', f'1: .word 0x{address+5:x}']
    tramp = outdir/'trampolines.S'
    tramp.write_text('\n'.join(asm)+'\n', encoding='ascii')
    elf = outdir/f'rt_{variant}.elf'
    command = [str(gcc), '-mcpu=cortex-m3', '-mthumb', '-Os', '-g0', '-ffreestanding',
               '-fno-builtin', '-fno-common', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
               '-fno-stack-protector', '-fstack-usage', '-Wall', '-Wextra', '-Werror',
               '-nostdlib', '-Wl,--build-id=none', '-Wl,-T,runtime.ld',
               '-Wl,-Map,'+str(outdir/f'rt_{variant}.map'),
               '-o', str(elf), 'runtime.c', str(tramp)]
    if variant=='release': command.insert(1,'-DRELEASE=1')
    subprocess.run(command, cwd=ROOT, env=env, check=True, capture_output=True)
    raw_symbols = subprocess.check_output([str(gcc.parent/'arm-none-eabi-nm.exe'), '-n', str(elf)], text=True)
    symbols = {parts[2]: int(parts[0],16) for line in raw_symbols.splitlines()
               if len(parts := line.split()) == 3 and parts[1].upper() in ('T','B','R')}
    binary = outdir/f'rt_{variant}_code.bin'
    subprocess.run([str(gcc.parent/'arm-none-eabi-objcopy.exe'), '-O','binary',
                    '-j','.text',str(elf),str(binary)], check=True, capture_output=True)
    code=binary.read_bytes()
    require(CODE+len(code)<=0x7f000, 'Patch exceeds app region')
    image=bytearray(data[:CODE-BASE]+code)
    changes=[]
    def patch(name, address, expected, new):
        check_bytes(image,address,expected,name)
        require(len(expected)==len(new),'Hook cannot move existing addresses')
        image[address-BASE:address-BASE+len(new)]=new
        changes.append(dict(name=name,address=f'0x{address:08x}',original=expected.hex(),new=new.hex()))
    for name,address,expected,target,_ in HOOKS:
        patch(name,address,bytes.fromhex(expected),branch(address,symbols[target]))
    patch('init_table',0x48248,bytes.fromhex('11e00200'),struct.pack('<I',symbols['rt_init']|1))
    patch('heap_reserve_call',0x26552,bytes.fromhex('06f09bf9'),branch(0x26552,symbols['rt_heap'],True))
    patch('static_save_call',0x11ab2,bytes.fromhex('05f0b3ff'),branch(0x11ab2,symbols['rt_static_save'],True))
    patch('status_save_call',0x3379c,bytes.fromhex('e4f71cf9'),branch(0x3379c,symbols['rt_status_save'],True))
    patch('key_gui_call',0x1d268,bytes.fromhex('fff714ff'),branch(0x1d268,symbols['rt_key'],True))
    product=outdir/f'neos75_rt_{variant}.bin'
    product.write_bytes(image)
    # Every changed byte has its absolute address, source byte and result byte.
    diff=outdir/f'neos75_rt_{variant}.diff.tsv'
    with diff.open('w',encoding='ascii',newline='\n') as f:
        f.write('address\toffset\toriginal\tpatched\n')
        for offset,new in enumerate(image):
            if data[offset]!=new:
                f.write(f'{BASE+offset:08X}\t{offset:06X}\t{data[offset]:02X}\t{new:02X}\n')
    metadata=dict(variant=variant,proto_version=2,patch_version=3,
                  sha256=digest(image),input_sha256=SHA256,bytes=len(image),
                  appended_start=f'0x{CODE:08x}',appended_bytes=len(code),hooks=changes,
                  symbols={k:f'0x{v:08x}' for k,v in symbols.items()},
                  qualification='OFFLINE EXPERIMENTAL; NOT HARDWARE VALIDATED')
    (outdir/f'neos75_rt_{variant}.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    (outdir/f'neos75_rt_{variant}.sha256').write_text(f'{digest(image)}  {product.name}\n',encoding='ascii')
    return bytes(image), metadata


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,default=BACKUP)
    p.add_argument('--output',type=Path,default=ROOT/'build')
    args=p.parse_args()
    data=args.input.read_bytes()
    check_input(data)
    for variant in ('dev','release'):
        try:
            _,meta=build(data,variant,args.output.resolve())
        except subprocess.CalledProcessError as e:
            raise SystemExit(e.stderr.decode() if isinstance(e.stderr,bytes) else e.stderr) from e
        print(f'{variant}: {meta["sha256"]} ({meta["bytes"]} bytes)')


if __name__=='__main__':
    main()
