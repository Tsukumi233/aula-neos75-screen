import json
import struct
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
import make_patch


def test_displaced_prologues_and_absolute_thumb_returns():
    cs=Cs(CS_ARCH_ARM,CS_MODE_THUMB)
    for variant in ('dev','release'):
        path=make_patch.ROOT/'build'/f'neos75_rt_{variant}'
        meta=json.loads(path.with_suffix('.json').read_text())
        image=path.with_suffix('.bin').read_bytes()
        for name,address,old,target,trampoline in make_patch.HOOKS:
            entry=int(meta['symbols'][trampoline],16)
            data=image[entry-make_patch.BASE:entry-make_patch.BASE+12]
            assert data[:4].hex()==old
            original=list(cs.disasm(bytes.fromhex(old),address))
            assert sum(i.size for i in original)==4
            assert all(i.mnemonic in ('push','push.w','sub') and 'pc' not in i.op_str for i in original)
            jump=list(cs.disasm(data[4:8],entry+4))[0]
            assert jump.mnemonic=='ldr.w' and jump.op_str.startswith('pc, [pc,')
            assert struct.unpack_from('<I',data,8)[0]==address+5
