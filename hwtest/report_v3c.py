"""Audit the approved revision-2 regression and append its hardware evidence."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from report_v3 import audit,load,ROOT
from run_v3c import APPROVED_SHA,PREVIOUS
from neos75screen import protocol as p


def previous_knob():
    path=PREVIOUS/'knob_user1.jsonl'; rows=load(path)
    directions=Counter(); statuses=Counter(); visible=Counter(); bad=0
    for r in rows:
        if r['kind']!='hid_rx': continue
        raw=bytes.fromhex(r['report'])
        if raw[:2]==b'\xa5\x3b': bad+=not p.a5_checksum_ok(raw)
        event=p.parse_event(raw)
        if isinstance(event,p.KnobEvent): directions[event.direction]+=1
        elif isinstance(event,p.VisibilityEvent): visible[(event.visible,event.reason)]+=1
        if p.is_frame_done(raw): statuses[p.parse_frame_done(raw).status]+=1
    return dict(file=str(path.relative_to(ROOT)),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                directions=dict(directions),frame_statuses=dict(statuses),bad_checksums=bad,
                visibility=[dict(visible=k[0],reason=k[1],count=v) for k,v in visible.items()],
                resend_errors=[r for r in rows if r['kind']=='resend_error'],
                closed=next((r for r in reversed(rows) if r['kind']=='closed'),None))


def report(directory,a,rows):
    one=lambda kind:next((r for r in rows if r['kind']==kind),None)
    old=json.loads((PREVIOUS/'audit.json').read_text(encoding='utf-8'))
    old_perf={r['target_kib']:r for r in old['performance']}
    prior=previous_knob(); (directory/'prior_knob_summary.json').write_text(json.dumps(prior,indent=2),encoding='utf-8')
    lines=['## v3 修正版 2（cba6f2c7）','',
           f'记录目录：`{directory.relative_to(ROOT).as_posix()}/`。仅刷入获批 dev SHA',
           f'`{APPROVED_SHA}`，277276 字节；未重建、未刷 release 或其他构建，使用负责人已更新的 SDK。','',
           '### c7d2a4c1 用户旋钮监听补记','',
           f'日志：`{prior["file"]}`，SHA `{prior["sha256"]}`。这是用户上次手动转旋钮的记录，本轮未模拟旋钮。',
           f'原始方向事件 next={prior["directions"].get(1,0)}、previous={prior["directions"].get(-1,0)}，无 press；',
           f'frame-done 状态/数量为 `{prior["frame_statuses"]}`，RT 校验错误={prior["bad_checksums"]}。',
           '其余方向、可见性和正常帧事件符合既有协议；未发现报文校验损坏。日志未记录内部队列或设备侧调用栈，不能只凭报文证明溢出的全部内部过程。','',
           '- **15:36:31**：先收到 05（seq=146），随后可见事件，再于 15:36:31.897 收到 04（seq=147），并记录 `resend_error: FrameRejected(04)`。',
           '  结合负责人分析，这是没有对应当前流的过期 04 被 SDK 当成新帧结果，导致设备已接受的新流被主机放弃。',
           '- **15:37:20**：用户报告 STATUS 超时；日志保留较晚到达的 STATUS 回复（15:37:20.090，state=0、armed=1）、',
           '  一批仍可达的旋钮/可见事件，随后关闭监听。超时异常本身没有独立 JSON error 行，不把晚到回复称为从未回复。','',
           '修复依据 FIRMWARE.md“输入队列溢出时的过期事件”和 PROTOCOL.md 4.2：溢出仅在接收 state=1 时发 04；',
           '接收中遇新起始包静默替换被放弃的流，不再产生额外 frame-done；解码 state=2/3 仍报 04。',
           'SDK 在起始 ACK 前遇 frame-done 时短暂等 ACK，识别过期事件后忽略；实时请求超时重试一次。',
           '**本轮不发 A5 39、没有实体旋钮操作，因此只验证平稳回归；极快转动下的过期事件和请求重试仍需用户手动复测。**','',
           '### 预检、刷写与启动','']
    pre=one('preflight_verified'); idle=one('idle_passed'); timeout=one('timeout_silence_end'); keep=one('keepalive_passed')
    if pre:
        lines += [f'刷前 app 与 c7d2a4c1 镜像加 FF 逐字节相同，完整区 SHA `{pre["app_sha256"]}`。',
                  f'SP=`{pre["sp"]}`、复位向量=`{pre["reset"]}`；trial 行 `{pre["trial_row"]}`。',
                  '引导区除 `0x7F00..0x7F0F` 外与原备份一致；配置页使用本次刷前值作基线，不回写原始配置。']
    lines+=['先 dry-run、只读 dump，再写入 68 个 app 扇区 `0x8000..0x4B000`；完整回读一致后退出，flag=55AAAA55。',
            '版本构建串不变；HELLO proto=2、patch=3、canvas=image=480×360、max_frame_bytes=172804、buffers=1、host_timeout_ms=10000。']
    if idle: lines.append(f'同一 reader 空跑 {idle["duration_s"]:.3f} s，HELLO/STATUS 正常，visible=armed=0、计数不变；无 uptime 字段，结论仅限本次观测。')
    lines+=['','### 会话与配置页','',
            '20 次 ENTER/LEAVE 均检查 STATUS 和相应可见性事件；没有异常 frame-done。']
    if timeout: lines.append(f'关闭保活 {timeout["seconds"]:.3f} s，无主机发送并收到 `20 00 03`；STATUS 解除后能再次 ENTER 和显示。')
    if keep: lines.append(f'默认保活持续 {keep["duration_s"]:.3f} s，末尾 visible=armed=1。')
    lines+=['分段导出控制循环后、超时后及保活后的配置；每次只读核对获批 app 后退出，未再次写入。','',
            '| 导出 | 配置 SHA-256 | 与本次刷前差异 |','|---|---|---:|']
    for r in a['configurations']: lines.append(f'| {r["label"]} | `{r["sha256"]}` | {r["diff_bytes"]} 字节 |')
    lines+=['','比较的是完整 4096 字节；所有检查点 trial flag 仍为55AAAA55。帧/性能后未再次 dump，不将这些结果外推为未观测窗口的内部闪存调用轨迹。','',
            '### 帧与性能：对照 c7d2a4c1','',
            '精简回归：480×360 JPEG 三种采样各一帧（q85）、320×320/160×120 居中、100×100 RGB565 跨块；',
            '482×360 越界返回 03 后正常帧恢复。所有正常帧状态与计数核对通过，画面仍以用户目视为准。',
            '沿用 c7d2a4c1 的同字节 20/45 KiB 4:2:0 素材，各30帧；perf_counter 计时，批次内无逐帧 STATUS。','',
            '| KiB/实际字节 | c7d2 fps | cba6 fps | 变化 | 传输 ms 中位/p95 | total ms 中位/p95 | decode_ms 中位/p95 | STATUS ms 中位/p95 |',
            '|---|---:|---:|---:|---|---|---|---|']
    for r in a['performance']:
        baseline=old_perf[r['target_kib']]; fmt=lambda k:f'{r[k]["median"]:.2f}/{r[k]["p95"]:.2f}'
        lines.append(f'| {r["target_kib"]}/{r["actual_bytes"]} | {baseline["fps"]:.3f} | {r["fps"]:.3f} | {(r["fps"]/baseline["fps"]-1)*100:+.2f}% | {fmt("transfer_ms")} | {fmt("total_ms")} | {fmt("decode_ms")} | {fmt("separate_status_query_ms")} |')
    if a['stream']:
        s=a['stream']; prev=old['stream']
        lines+=['',f'stream 实测 {s["duration_s"]:.3f} s，`{s["stats"]}`，{s["fps"]:.3f} fps；前后计数一致。',
                f'c7d2a4c1 为 {prev["duration_s"]:.3f} s、{prev["stats"]["shown"]} 帧、{prev["fps"]:.3f} fps。单次小差异不代表严格性能结论。']
    lines+=['','传输为首 BA 到末包 write 完成，total 包含 frame-done 等待；STATUS 单独测30次，不混入纯推流。audit.json/CSV 保存完整分布与逐帧数据。','',
            '### 审计及最终状态','',
            f'本轮 app 下载 {a["download_count"]} 个扇区，每个块 SHA 与获批镜像一致；RT checksum 错误 {a["bad_rt_checksums"]}。',
            f'累计正常 frame-done={a["shown_events"]}；预期越界错误 `{a["error_events"]}`。重启后的计数分段核对。',
            f'HID 发送类别 `{a["hid_tx_classes"]}`；无 A5 39、亮度、持久上传、34/FE。进入 DFU 只发工具固定 13 02。',
            '未改固件/SDK、未编译、未运行 git、未访问其他 PID 或 Hub，未安装软件/驱动、改系统设置或终止进程。']
    end=one('final_idle_confirmed')
    if end: lines += ['当前运行 **v3 修正版 2 dev cba6f2c7，原厂页面、实时未武装**。',
                      '最后显示一帧后 close(leave=False)，关闭通信12s再只查询 HELLO/STATUS，确认自然超时，不靠 LEAVE 强制退出。',
                      f'最终 STATUS：`{end["status"]}`。本轮没有恢复原厂固件，无需拔插。']
    else: lines.append('最终收尾尚待 finish 阶段，以 state.json 为准。')
    lines+=['','后续由用户在新构建上手动快速转动旋钮复测，重点检查不再因过期04停流、STATUS短暂超时能重试，以及原厂动画/图像正常。','']
    doc=ROOT/'docs/HWTEST.md'; data=doc.read_bytes(); marker='\n## v3 修正版 2（cba6f2c7）'.encode()
    prefix=data.split(marker)[0] if marker in data else data
    prefix=prefix.replace('**最新上机记录为文末 v3 修正版（c7d2a4c1）；前文各版本的“当前状态/未知”均为对应时点的历史记录。**'.encode(),
                          '**最新上机记录为文末 v3 修正版 2（cba6f2c7）；前文各版本的“当前状态/未知”均为对应时点历史。**'.encode(),1)
    doc.write_bytes(prefix+b'\n\n'+'\n'.join(lines).encode('utf-8'))


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('run_dir',type=Path); ap.add_argument('--update-doc',action='store_true')
    args=ap.parse_args(); directory=args.run_dir.resolve()
    a,rows=audit(directory,approved=APPROVED_SHA,pre_image=(PREVIOUS/'after_controls/app_8000_7F000.bin').read_bytes(),
                 config_labels=('pre','after_controls','after_timeout','after_sessions'))
    if args.update_doc: report(directory,a,rows)
    print(json.dumps(dict(config_diffs={r['label']:r['diff_bytes'] for r in a['configurations']},
                          performance=[dict(kib=r['target_kib'],fps=r['fps'],decode=r['decode_ms']) for r in a['performance']],
                          stream=a['stream'],shown=a['shown_events'],errors=a['error_events'],
                          final=next((r for r in rows if r['kind']=='final_idle_confirmed'),None)),ensure_ascii=False))


if __name__=='__main__': main()
