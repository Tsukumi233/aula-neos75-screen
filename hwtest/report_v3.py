"""Audit v3 logs and append the hardware report; never accesses a device."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sdk/src'))
from neos75screen import protocol as p

APPROVED='e8b668629aff05c655dbb763c3cc439ec7e1281f4aa10bf9a8a1a95cfee65e0e'
# The stock-firmware backup of this keyboard: the folder in NEOS75_BACKUP, else ~/Tools/aula_neos75_backup.
BACKUP=Path(os.environ.get('NEOS75_BACKUP') or Path.home() / 'Tools' / 'aula_neos75_backup')
def sha(b): return hashlib.sha256(b).hexdigest()
def load(path): return [json.loads(line) for line in path.open(encoding='utf-8')] if path.exists() else []


def audit(directory,approved=APPROVED,pre_image=None,config_labels=('pre','after_controls','after_timeout')):
    rows=load(directory/'events.jsonl')+load(directory/'knob_watch_5s.jsonl')
    rows.sort(key=lambda r:r['perf_counter'])
    dev=(ROOT/'firmware/build/neos75_rt_dev.bin').read_bytes(); assert sha(dev)==approved
    stock=(BACKUP/'app_8000_7F000.bin').read_bytes()
    current=dev; erases=[]; downloads=[]; classes=Counter(); frame=[]; visibility=[]; bad=0; last_seq=0
    boot_ok=boot_err=0
    for r in rows:
        kind=r['kind']
        if kind=='automatic_recovery' or (kind=='stage_start' and r['stage']=='restore'): current=stock
        if kind in ('approved_dev_written','readonly_exit_verified'): last_seq=boot_ok=boot_err=0
        if kind=='dfu_ctrl' and not r['incoming'] and r['request']==1:
            if r['value']==0 and r['length']==5 and r.get('hex','').startswith('41'):
                address=struct.unpack('<I',bytes.fromhex(r['hex'])[1:])[0]
                assert address in range(0x8000,0x8000+len(current+b'\xff'*(-len(current)%4096)),4096)
                erases.append(address)
            if r['value']==2:
                offset=r['address']-0x8000; padded=current+b'\xff'*(-len(current)%4096)
                assert 0<=offset<len(padded) and offset%4096==0
                assert r['sha256']==sha(padded[offset:offset+4096])
                downloads.append(r['address'])
        if kind=='hid_tx':
            raw=bytes.fromhex(r['report']); assert raw[0]==0xba or raw[:2] in (b'\xa5\x3b',b'\x11\x01',b'\x11\x02')
            if p.looks_like_ba_start(raw): assert raw[3] in (0x5a,0x5b)
            classes[f'{raw[0]:02X}'+(f' {raw[1]:02X}' if raw[0]!=0xba else '')]+=1
        if kind=='hid_rx':
            raw=bytes.fromhex(r['report'])
            if raw[:2]==b'\xa5\x3b': bad+=not p.a5_checksum_ok(raw)
            if p.is_frame_done(raw):
                e=p.parse_frame_done(raw); assert e.seq==(last_seq+1)&65535
                last_seq=e.seq; frame.append(vars(e))
                if e.status==1: boot_ok+=1
                else: boot_err+=1
            event=p.parse_event(raw)
            if event is not None: visibility.append(dict(time=r['time'],**vars(event)))
    assert erases==downloads and bad==0
    final=next((r for r in rows if r['kind']=='final_realtime_check'),None)
    if final:
        assert (final['status']['frames_ok'],final['status']['frames_err'])==(boot_ok,boot_err)
    configs=[]
    if (directory/'pre').exists():
        baseline=(directory/'pre/config_7F000_80000.bin').read_bytes()
        refboot=(BACKUP/'bootloader_0000_8000.bin').read_bytes()
        for name in config_labels:
            path=directory/name
            if not path.exists(): continue
            boot=(path/'bootloader_0000_8000.bin').read_bytes()
            assert boot[:0x7f00]==refboot[:0x7f00] and boot[0x7f10:]==refboot[0x7f10:]
            app=(path/'app_8000_7F000.bin').read_bytes()
            expected=(stock if pre_image is None else pre_image) if name=='pre' else dev+b'\xff'*(len(stock)-len(dev))
            assert app==expected
            config=(path/'config_7F000_80000.bin').read_bytes()
            configs.append(dict(label=name,sha256=sha(config),diff_bytes=sum(a!=b for a,b in zip(baseline,config)),
                                app_sha256=sha(app),trial_row=boot[0x7f00:0x7f10].hex()))
    a=dict(approved_sha256=approved,erase_sectors=erases,download_count=len(downloads),
           hid_tx_classes=dict(classes),bad_rt_checksums=bad,frame_events=len(frame),
           shown_events=sum(e['status']==1 for e in frame),error_events=[e for e in frame if e['status']!=1],
           visibility=visibility,configurations=configs,stages=json.loads((directory/'state.json').read_text())['stages'],
           metrics=[r for r in rows if r['kind']=='frame_result'],samples=[r for r in rows if r['kind']=='sample'],
           performance=[r for r in rows if r['kind']=='performance_summary'],
           stream=next((r for r in rows if r['kind']=='stream_summary'),None),
           final=final,current_boot_ok=boot_ok,current_boot_err=boot_err,
           failures=[r for r in rows if r['kind']=='stage_failed'])
    (directory/'audit.json').write_text(json.dumps(a,indent=2,ensure_ascii=False),encoding='utf-8')
    if a['metrics']:
        with (directory/'frame_timings.csv').open('w',encoding='utf-8',newline='') as f:
            names=['seq','total_ms','transfer_ms','transfer_start_window_ms','decode_ms']
            w=csv.DictWriter(f,fieldnames=names); w.writeheader()
            w.writerows({k:r[k] for k in names} for r in a['metrics'])
    return a,rows


def report(directory,a,rows,related):
    def one(kind): return next((r for r in rows if r['kind']==kind),None)
    idle=one('idle_passed'); silence=one('timeout_silence_end'); keep=one('keepalive_passed')
    lines=['## v3 开发版（2026-11-07，proto2 / patch3）','',
           f'主记录目录：`{directory.relative_to(ROOT).as_posix()}/`；使用且仅使用获批 dev SHA',
           f'`{APPROVED}`，277160 字节。本轮没有重建固件，没有刷 release 或其他补丁。',
           '隐藏状态 LEAVE 按负责人已更新的规格只解除武装、保留当前原厂页；本次没有操作实体旋钮，未实测此分支。','',
           '### 预检、刷写、启动与配置','',
           '刷前 app 与原厂备份逐字节相同；引导区除允许的 trial 行外与备份相同。',
           '先 dry-run 再 dump；只擦写 68 个 4 KiB app 扇区 `0x8000..0x4B000`，含补齐字节完整回读后退出。',
           '构建串仍为 `V1.0.3 Sep  6 2026 23:18:16`；HELLO 为 proto=2、patch=3、',
           'canvas=image=480×360、formats=3、max_frame_bytes=172804、buffers=1、host_timeout_ms=10000。','',
           '| 导出 | 配置 SHA-256 | 与本轮刷前差异 | trial 行 |','|---|---|---:|---|']
    for r in a['configurations']:
        lines.append(f'| {r["label"]} | `{r["sha256"]}` | {r["diff_bytes"]} 字节 | `{r["trial_row"]}` |')
    lines+=['',f'60 s 空跑实测 {idle["duration_s"]:.3f} s，visible=armed=0、计数不变。' if idle else '空跑尚未完成。',
            '刷前配置与最早原厂备份有 484 字节差异；这是本轮基线，未回写或清除。以上两次检查均相对本轮刷前基线为 0，帧/性能结束后没有再次 dump，不外推为未覆盖窗口的闪存实录。',
            '同一 reader 未报断连是有限稳定性证据；协议无 uptime，不能仅靠零计数证明绝无复位。',
            '20 次 ENTER/LEAVE 逐次检查 STATUS 和 `20 01 02` / `20 00 02`；配置前后整页比较，不以最终设置载荷相同代替无写入。','']
    if silence: lines.append(f'关闭保活后保持 {silence["seconds"]:.3f} s 无主机发送，收到 `20 00 03`；STATUS 解除，重新 ENTER 并成功显示 2×2 RGB。随后再次进 DFU 比较配置。')
    if keep: lines.append(f'默认 SDK keepalive 无帧保持 {keep["duration_s"]:.3f} s，末尾 visible=armed=1；期间共 {keep["transmitted_reports"]} 条报告（包含末尾 STATUS）。')
    lines+=['','### 图像与性能','',
            '帧测试：480×360 JPEG 的 4:4:4/4:2:2/4:2:0 × q50/85/95；320×320、160×120、480×2、2×360 居中；',
            'RGB565 2×2、16×16、64×64、100×100、320×240，含跨多个 4096 字节块。',
            '482×360、480×362 两个越界 JPEG 均返回 03，各自下一正常帧均返回 01；正常帧、计数及 last_error 检查均通过。',
            '协议成功只证明交给 GUI；居中、颜色、全屏和原厂资源恢复仍须用户目视。','',
            '高分辨率 `perf_counter`（分辨率 100 ns）；以下批次均 30 帧、批次内没有逐帧 STATUS。',
            '传输时间包括首个 BA 发出到末包 write 完成；total 包含等待 frame-done。','',
            '| 目标/真实字节 | fps | 传输 ms 中位数/p95 | 总耗时 ms 中位数/p95 | decode_ms 中位数/p95 | STATUS ms 中位数/p95 |',
            '|---|---:|---|---|---|---|']
    for r in a['performance']:
        fmt=lambda k:f'{r[k]["median"]:.2f}/{r[k]["p95"]:.2f}'
        lines.append(f'| {r["target_kib"]} KiB / {r["actual_bytes"]} | {r["fps"]:.3f} | {fmt("transfer_ms")} | {fmt("total_ms")} | {fmt("decode_ms")} | {fmt("separate_status_query_ms")} |')
    if a['stream']:
        s=a['stream']; lines+=['',f'`stream()` 连续 {s["duration_s"]:.3f} s：{s["stats"]}，实际 {s["fps"]:.3f} fps；前后 STATUS 计数相符。']
    lines+=['','单列 STATUS 为批次外 30 次查询；若每帧前后各查一次，额外开销约为上述 STATUS 中位数的两倍。',
            'frame_timings.csv 保留逐帧数据，audit.json 含每档 min/median/p95/max 和单帧带 STATUS 的样本。','',
            '### 旋钮观察脚本、审计与问题','',
            '`hwtest/knob_watch.py` 只使用 SDK 和双层窄白名单；HELLO→ENTER，显示大字 RT 与当前时间；',
            '记录全部 KnobEvent/VisibilityEvent、可见时重发，每 5 s STATUS，默认保活，退出 `close(leave=True)`。','',
            '```powershell',
            'sdk/.venv/Scripts/python -B hwtest/knob_watch.py --seconds 120 --log hwtest/knob_user.jsonl',
            '```','',
            '本轮实际运行 `--seconds 5`，日志为主目录的 knob_watch_5s.jsonl；未转旋钮、未发 A5 39，因此不能称物理方向/按下已验证。',
            f'审计：本轮 {len(a["erase_sectors"])} 次 app 擦除/下载，每块 SHA 与获批镜像一致；RT checksum 错误 {a["bad_rt_checksums"]}。',
            f'记录正常 frame-done {a["shown_events"]} 个；错误事件 {a["error_events"]}。HID 发送类别 {a["hid_tx_classes"]}。',
            f'审计跨 DFU 启动累计；最近一次启动计数为 ok={a["current_boot_ok"]}、err={a["current_boot_err"]}，与收尾 STATUS 核对。',
            '没有发送翻页/亮度/持久壁纸/34/FE；进入 DFU 只用工具固定的 `13 02`。没有访问其他 PID、运行 Hub、安装驱动、改系统设置、结束进程或运行 git。']
    if related:
        lines+=['',f'首轮记录保留于 `{related.relative_to(ROOT).as_posix()}/`：20 次循环配置差异同样为 0。',
                '超时测试脚本在 ENTER 回复后立即清队列，漏掉稍后到达的 ENTER 可见事件，误把“ENTER + 正确超时”当异常，自动恢复原厂一次。',
                '这是测试同步错误，非固件异常证据。修复为先等 STATUS 回复（它排在 ENTER 事件后）再开始无通信计时；',
                '随后仅重刷同一获批 SHA，完整重跑。两轮合计 v3 写入 2 次、原厂恢复 1 次，未刷其他构建。',
                '恢复工具仅写 app，不恢复配置。首轮日志、错误及恢复回读证据没有删除。']
    lines+=['','### 与 v1/v2 对比、最终状态和目视确认','',
            '| 项目 | v1 | 失败 v2 | v3 本轮 |','|---|---|---|---|',
            '| image 最大尺寸 | 320×320 | 480×360（只验证 HELLO） | 480×360，完成图像协议测试 |',
            '| 20 次控制后配置差异 | 0 字节 | 68 字节 | 实测 0 字节 |',
            '| 硬件性能 | 320×320 已测 | 未执行 | 上表为实测 480×360，尺寸/码率不同不直接比较 fps |',
            '| 旋钮协议 | 无 | 无 | 超时/保活已测，实体旋钮待用户测试 |','']
    lines+=['v1 的 320×320、8/15/25/40 KiB 分别为 4.147/2.832/1.884/1.219 fps，15 KiB stream 为 2.932 fps。',
            'v1 批次包含每帧两次 STATUS 且旧计时量化较粗；v3 分离 STATUS、像素量和文件档位也不同，因此这些数字只作背景对照，不能推导补丁速度提升或下降。','']
    if a['final']:
        lines+=['当前运行获批 **v3 开发版**；已显示 assets/check_v3.png，进程以 `close(leave=False)` 结束。',
                '退出后不再保活，约 10 s 自动解除实时模式、回原厂页面，这是预期行为；结束时不额外发查询延长租期。']
    else: lines+=['最终检查图尚未发送，状态以 state.json 为准。']
    lines+=['',
            '1. 请目视确认整屏检查图：连续 2 像素白边四边均在；左上红 TL、右上绿 TR、左下蓝 BL、右下黄 BR；40 像素网格，黑箭头向上，文字 NEOS75 RT v3 480x360，无裁边/镜像/颜色颠倒。',
            '2. 自动超时或 LEAVE 后，请确认原厂图标/图片与进入前一致，无缺图、实时残影或花屏。',
            '3. 之后由用户实际转旋钮运行 knob_watch：应 RT→相邻原厂页、再绕回 RT（先黑后重发），日志方向/可见事件相符；按下是否到达仍未知。','']
    doc=ROOT/'docs/HWTEST.md'; old=doc.read_bytes(); marker='\n## v3 开发版（'.encode()
    prefix=old.split(marker)[0] if marker in old else old
    note=('<!-- V3_CURRENT_NOTICE -->\n'
          '**最新上机记录为文末 v3 开发版一节；下列 v1/v2 的“当前状态”均为当时的历史状态。**\n'
          '<!-- END_V3_CURRENT_NOTICE -->\n\n').encode('utf-8')
    if b'<!-- V3_CURRENT_NOTICE -->' not in prefix:
        title_end=prefix.index(b'\n')+1
        prefix=prefix[:title_end]+b'\n'+note+prefix[title_end:]
    doc.write_bytes(prefix+b'\n\n'+'\n'.join(lines).encode('utf-8'))


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('run_dir',type=Path)
    ap.add_argument('--related-run',type=Path); ap.add_argument('--update-doc',action='store_true')
    args=ap.parse_args(); directory=args.run_dir.resolve(); a,rows=audit(directory)
    if args.related_run: audit(args.related_run.resolve())
    if args.update_doc: report(directory,a,rows,args.related_run.resolve() if args.related_run else None)
    print(json.dumps({k:a[k] for k in ('download_count','shown_events','error_events','configurations','performance','stream')},ensure_ascii=False))


if __name__=='__main__': main()
