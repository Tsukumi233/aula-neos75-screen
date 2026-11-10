import hashlib
import json
from pathlib import Path
import pytest
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
import make_patch as patch


@pytest.mark.parametrize('variant',['dev','release'])
def test_reproducible_byte_exact_diff_and_hooks(variant):
    original=patch.BACKUP.read_bytes()
    expected=(patch.ROOT/'build'/f'neos75_rt_{variant}.bin').read_bytes()
    directory=patch.ROOT/'build'/'repro'/variant
    actual,meta=patch.build(original,variant,directory)
    assert actual==expected
    assert hashlib.sha256(actual).hexdigest()==meta['sha256']
    allowed={a+i for h in meta['hooks'] for a in [int(h['address'],16)] for i in range(4)}
    assert all(original[i]==b or i+patch.BASE in allowed or i+patch.BASE>=patch.CODE
               for i,b in enumerate(actual))
    cs=Cs(CS_ARCH_ARM,CS_MODE_THUMB)
    for h in meta['hooks']:
        address=int(h['address'],16)
        assert original[address-patch.BASE:address-patch.BASE+4].hex()==h['original']
        if h['name']!='init_table':
            inst=list(cs.disasm(bytes.fromhex(h['new']),address))
            assert len(inst)==1 and inst[0].mnemonic in ('b.w','bl')
            assert patch.CODE<=int(inst[0].op_str.lstrip('#'),16)<patch.BASE+len(actual)
    lines=(directory/f'neos75_rt_{variant}.diff.tsv').read_text().splitlines()[1:]
    expected_diff={i+patch.BASE:(old,new) for i,(old,new) in enumerate(zip(original,actual)) if old!=new}
    recorded={int(a,16):(int(old,16),int(new,16)) for a,off,old,new in (line.split('\t') for line in lines)}
    assert recorded==expected_diff


def test_wrong_backup_and_independent_original_bytes_assertions(monkeypatch):
    original=patch.BACKUP.read_bytes()
    with pytest.raises(ValueError,match='SHA-256'): patch.check_input(original[:-1])
    extra=(('init_table',0x48248,'11e00200'),('heap_reserve_call',0x26552,'06f09bf9'),
           ('static_save_call',0x11ab2,'05f0b3ff'),('status_save_call',0x3379c,'e4f71cf9'),
           ('key_gui_call',0x1d268,'fff714ff'))
    for name,address,expected,*_ in (*patch.HOOKS,*extra):
        modified=bytearray(original); modified[address-patch.BASE]^=1
        with pytest.raises(ValueError,match='SHA-256'): patch.check_input(modified)
        # Bypass only the digest to demonstrate that the byte assertions are independent.
        monkeypatch.setattr(patch,'SHA256',hashlib.sha256(modified).hexdigest())
        with pytest.raises(ValueError,match='original bytes mismatch'): patch.check_input(modified)
        monkeypatch.setattr(patch,'SHA256',hashlib.sha256(original).hexdigest())


def test_protected_regions_and_vectors_unchanged():
    original=patch.BACKUP.read_bytes()
    for variant in ('dev','release'):
        actual=(patch.ROOT/'build'/f'neos75_rt_{variant}.bin').read_bytes()
        assert len(actual)<=patch.APP_SIZE
        assert actual[:0x1b0]==original[:0x1b0]
        assert actual[patch.CONTENT_END-patch.BASE:patch.CODE-patch.BASE]==b'\xff'*(patch.CODE-patch.CONTENT_END)
