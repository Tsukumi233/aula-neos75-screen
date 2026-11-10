"""Record real stock GUI/config execution without opening USB, HID or a device."""
import argparse
import json
import hashlib
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'tests'))
from stock_gui import StockGUI, SAVE_CHAIN
from test_runtime import upload, stream


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',choices=('v1','v2_failed','v2','v2_configfix','v3'),default='v3')
    p.add_argument('--telemetry',action='store_true')
    p.add_argument('--captured-config',type=Path)
    p.add_argument('--expected-config',type=Path)
    args=p.parse_args()
    historical='v2_configfix' if args.baseline=='v2' else args.baseline
    directory=ROOT/'build'/historical if historical!='v3' else ROOT/'build'
    m=StockGUI(build_dir=directory,captured_config=args.captured_config)
    boot_start=len(m.trace)
    m.bootstrap(); start=len(m.trace)
    initial=bytes(m.uc.mem_read(0x7f000,4096))
    for tick in range(10,60001,10):
        m.uc.mem_write(0x20003a54,tick.to_bytes(4,'little'))
        m.uc.mem_write(0x20003a58,tick.to_bytes(4,'little'))
        m.gui()
        if tick%500==0: m.tick(tick)
    idle_end=len(m.trace)
    for i in range(20):
        m.control(2)
        if args.telemetry and i<2: m.telemetry((2,3)[i])
        if i in (0,10,19):
            upload(m,stream(16,16)); m.gui()
        m.control(3); m.tick(60000+(i+1)*500)
    stable=m.trace[start:]
    final=bytes(m.uc.mem_read(0x7f000,4096))
    result=dict(baseline=args.baseline,
                image_sha256=hashlib.sha256((directory/'neos75_rt_dev.bin').read_bytes()).hexdigest(),
                boot_trace=m.trace[boot_start:start],stable_trace=stable,trace=m.trace[start:],
                saves=[r for r in stable if r['kind']=='config_call'],
                idle_save_calls=sum(r['kind']=='config_call' for r in m.trace[start:idle_end]),
                restore_poll_calls=sum(r['kind']=='config_poll' for r in stable),
                config_diff_bytes=sum(a!=b for a,b in zip(initial,final)),
                config_before_sha256=hashlib.sha256(initial).hexdigest(),
                config_after_sha256=hashlib.sha256(final).hexdigest(),
                covered_functions=[hex(a) for a in m.entries if a in m.covered],
                peripheral_stubs=[hex(a) for a in m.stubs],
                state=dict(active=m.state().active,ok=m.state().ok,errors=m.state().errors))
    if args.expected_config:
        result['equals_expected_config']=final==args.expected_config.read_bytes()
        assert result['equals_expected_config'],'Emulated config does not match the supplied expected dump'
    out=ROOT/'build'/f'config_trace_{args.baseline}{"_telemetry" if args.telemetry else ""}.json'
    out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(out),stable_saves=len(result['saves']),
                         total_trace=len(result['trace']),state=result['state']),indent=2))


if __name__=='__main__': main()
