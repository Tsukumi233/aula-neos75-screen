"""Refresh the build-specific evidence tables in docs/FIRMWARE.md offline."""
import ctypes
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'tests'))
from emulator import Runtime


def main():
    metas={v:json.loads((ROOT/'build'/f'neos75_rt_{v}.json').read_text()) for v in ('dev','release')}
    lines=['', '| 产物 | 字节数 | SHA-256 |', '|---|---:|---|']
    for v,m in metas.items():
        lines.append(f'| `firmware/build/neos75_rt_{v}.bin` | {m["bytes"]} | `{m["sha256"]}` |')
    lines+=['', '以下均为【观察】：从最终生成器元数据、最终机器码及 GCC 栈报告提取。', '',
            '| 钩子 | VA | 原字节 | dev 新字节 | release 新字节 |', '|---|---|---|---|---|']
    for a,b in zip(metas['dev']['hooks'],metas['release']['hooks']):
        assert a['name']==b['name'] and a['original']==b['original'] and a['address']==b['address']
        spaced=lambda x:bytes.fromhex(x).hex(' ').upper()
        lines.append(f'| {a["name"]} | `{a["address"]}` | `{spaced(a["original"])}` | `{spaced(a["new"])}` | `{spaced(b["new"])}` |')
    stacks={}
    for v in metas:
        stacks[v]={}
        for line in (ROOT/'build'/f'rt_{v}.elf-runtime.su').read_text().splitlines():
            a,n,*_=line.split('\t'); stacks[v][a.split(':')[-1]]=n
    lines+=['', '| 新代码符号 | dev VA | release VA | 栈帧字节（dev / release） |', '|---|---|---|---|']
    for n,a in metas['dev']['symbols'].items():
        if n=='rt': continue
        # GCC's stack report can omit the final clone number shown by nm.
        stack_name=lambda v: stacks[v].get(n,stacks[v].get(n.rsplit('.',1)[0],'原序言'))
        lines.append(f'| `{n}` | `{a}` | `{metas["release"]["symbols"][n]}` | {stack_name("dev")} / {stack_name("release")} |')
    base=int(metas['dev']['symbols']['rt'],16)
    assert base==int(metas['release']['symbols']['rt'],16)
    lines+=['', f'Runtime 起点 `0x{base:08X}`，大小 `0x{ctypes.sizeof(Runtime):X}`；两构建布局相同。', '',
            '| Runtime 字段 | VA | 字节数 |', '|---|---|---:|']
    for n,t in Runtime._fields_:
        lines.append(f'| `{n}` | `0x{base+getattr(Runtime,n).offset:08X}` | {ctypes.sizeof(t)} |')
    lines+=['', 'reports/tx 均为 32×64 字节，槽 i 的地址为表内基址 + `(i & 31) * 64`。',
            'descriptor 为 12 字节 `{header,size,data}`；header=`4 | width<<10 | height<<21`，',
            '像素为 RGB565 little-endian，descriptor 和 RGB 在活动期间不释放。', '']
    doc=ROOT.parent/'docs/FIRMWARE.md'
    text=doc.read_text(encoding='utf-8')
    start='<!-- BUILD_TABLES_START -->'; end='<!-- BUILD_TABLES_END -->'
    prefix,rest=text.split(start); _,suffix=rest.split(end)
    doc.write_text(prefix+start+'\n'+'\n'.join(lines)+'\n'+end+suffix,encoding='utf-8')
    print('Updated offline build evidence tables.')


if __name__=='__main__': main()
