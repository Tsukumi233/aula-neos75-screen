"""SDK transport wrapper with hardware-test allow-list and reader-side audit."""
from contextlib import ExitStack
from dataclasses import asdict
import time

from neos75screen import protocol as p
from neos75screen.screen import Screen
from neos75screen.transport import Transport, open_transport


class GuardedTransport(Transport):
    def __init__(self, event):
        super().__init__()
        self.event=event; self.inner=open_transport()
        self.first_tx=self.last_tx=self.last_write_end=None
        self.frames_received=0; self.tx_count=0

    def set_report_filter(self, callback):
        # Forward on the actual reader thread, including unsolicited reports.
        self._filter=callback
        self.inner.set_report_filter(self.receive)

    def receive(self, report):
        self.event('hid_rx',report=report.hex())
        if p.is_frame_done(report): self.frames_received+=1
        return self._filter(report) if self._filter else False

    def _write_report(self, report):
        if not (report[0]==0xba or report[:2] in (b'\xa5\x3b',b'\x11\x01',b'\x11\x02')):
            raise ValueError('Hardware-test command denied')
        if p.looks_like_ba_start(report) and report[3] not in (0x5a,0x5b):
            raise ValueError('Persistent stream denied')
        if report[0]==0xba:
            if self.first_tx is None: self.first_tx=time.perf_counter()
            self.last_tx=time.perf_counter()
        self.inner.write(report)
        if report[0]==0xba: self.last_write_end=time.perf_counter()
        self.tx_count+=1; self.event('hid_tx',report=report.hex())

    def read(self, timeout): return self.inner.read(timeout)
    def read_nowait(self): return self.inner.read_nowait()

    def stream_session(self, persistent=False):
        if persistent: raise ValueError('Persistent stream denied')
        stack=ExitStack(); stack.enter_context(super().stream_session(False))
        stack.enter_context(self.inner.stream_session(False)); return stack

    def close(self): self.inner.close()


class TimedScreen(Screen):
    def __init__(self, transport, sample, keepalive=True):
        super().__init__(transport,keepalive=keepalive)
        self.sample=sample

    def _send_stream(self, *args, **kwargs):
        self._t.first_tx=self._t.last_tx=self._t.last_write_end=None
        self.frame_start=time.perf_counter()
        return super()._send_stream(*args,**kwargs)

    def _collect(self):
        result=super()._collect()
        self.sample(dict(result=asdict(result),total_ms=(time.perf_counter()-self.frame_start)*1000,
                         transfer_ms=(self._t.last_write_end-self._t.first_tx)*1000,
                         transfer_start_window_ms=(self._t.last_tx-self._t.first_tx)*1000,
                         decode_ms=result.decode_ms,seq=result.seq))
        return result
