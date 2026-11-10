"""Watch physical knob events through the SDK. Never sends stock page commands."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sdk/src'))
from neos75screen import Screen
from neos75screen.errors import FrameRejected, Timeout
from neos75screen.protocol import VisibilityEvent
from guarded_screen import GuardedTransport


def image(path=None):
    if path:
        from PIL import Image
        return Image.open(path).convert('RGB')
    from PIL import Image, ImageDraw, ImageFont
    pic=Image.new('RGB',(480,360),(20,35,50)); draw=ImageDraw.Draw(pic)
    font=ImageFont.truetype(r'C:\Windows\Fonts\arialbd.ttf',112)
    small=ImageFont.truetype(r'C:\Windows\Fonts\arial.ttf',26)
    draw.text((240,135),'RT',font=font,anchor='mm',fill='white')
    draw.text((240,255),datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S'),font=small,anchor='mm',fill='white')
    draw.rectangle((0,0,479,359),outline='white',width=2)
    return pic


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=float,default=120)
    parser.add_argument('--knob-press',action='store_true',help='also capture knob presses (Calculator key) via the SDK hook')
    parser.add_argument('--knob',action='store_true',help='capture the screen knob press and the volume knob on the PC (Neos75 keys only)')
    parser.add_argument('--image',type=Path,help='show this picture instead of the generated RT page')
    parser.add_argument('--log',type=Path,default=ROOT/'hwtest/knob_watch.jsonl')
    args=parser.parse_args()
    if args.seconds<=0: parser.error('--seconds must be positive')
    logfile=args.log.resolve()
    if not logfile.is_relative_to(ROOT/'hwtest'): parser.error('--log must be inside hwtest/')
    logfile.parent.mkdir(parents=True,exist_ok=True)
    with logfile.open('a',encoding='utf-8',buffering=1) as output:
        def record(kind,**fields):
            row=dict(time=datetime.now().astimezone().isoformat(),perf_counter=time.perf_counter(),kind=kind,**fields)
            text=json.dumps(row,ensure_ascii=False,default=str); output.write(text+'\n')
            if kind not in ('hid_tx','hid_rx'): print(text,flush=True)
        transport=GuardedTransport(record); screen=Screen(transport); entered=False
        try:
            hello=screen.hello(); record('hello',**asdict(hello))
            if not (hello.realtime and hello.proto_version==2 and hello.patch_version==3):
                raise RuntimeError('Expected proto2 patch3 screen')
            if args.knob: screen.capture_knob(volume_knob=True); record('knob_capture',screen_press=True,volume_knob=True)
            elif args.knob_press: screen.capture_knob_press(); record('knob_press_capture',block=True)
            screen.enter(); entered=True
            result=screen.show(image(args.image)); record('frame',**asdict(result))
            if not result.shown: raise RuntimeError('RT image failed')
            start=time.perf_counter(); next_status=start+5
            def status_if_due():
                nonlocal next_status
                if time.perf_counter()>=next_status:
                    try: record('status',**asdict(screen.status()))
                    except Timeout as exc: record('status_error',error=repr(exc))
                    next_status=time.perf_counter()+5
            while time.perf_counter()-start<args.seconds:
                for event in screen.events(timeout=.1):
                    record(type(event).__name__,**asdict(event))
                    if isinstance(event,VisibilityEvent) and event.visible:
                        try:
                            result=screen.show(image(args.image)); record('visible_resend',**asdict(result))
                        except (FrameRejected,Timeout) as exc:
                            record('resend_error',error=repr(exc)); continue
                        if not result.shown and not result.hidden: record('resend_error',error='not shown')
                    status_if_due()
                    if time.perf_counter()-start>=args.seconds: break
                status_if_due()
            record('watch_finished',seconds=time.perf_counter()-start,status=asdict(screen.status()))
        finally:
            try:
                if entered:
                    screen.leave()
                    for event in screen.events(timeout=.15): record(type(event).__name__,**asdict(event))
            finally:
                screen.close(leave=True); record('closed',leave=True)
    return 0


if __name__=='__main__': raise SystemExit(main())
