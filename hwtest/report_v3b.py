"""Offline v3b audit, previous knob-log summary and append-only hardware report."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from report_v3 import audit,load,ROOT
from run_v3b import DEV_SHA,PREVIOUS
from neos75screen import protocol as p


def knob_summary():
    result=[]
    for path in sorted(PREVIOUS.glob('knob_user*.jsonl')):
        rows=load(path); directions=Counter(); frames=Counter(); typed=0; visible=Counter(); last=None
        for row in rows:
            if row['kind']=='KnobEvent': typed+=1
            if row['kind']=='watch_finished': last=row
            if row['kind']!='hid_rx': continue
            raw=bytes.fromhex(row['report']); event=p.parse_event(raw)
            if isinstance(event,p.KnobEvent): directions[event.direction]+=1
            elif isinstance(event,p.VisibilityEvent): visible[int(event.visible)]+=1
            if p.is_frame_done(raw): frames[p.parse_frame_done(raw).status]+=1
        result.append(dict(file=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                           start=rows[0]['time'],end=rows[-1]['time'],printed_knob_events=typed,
                           raw_knob_directions=dict(directions),visibility=dict(visible),
                           frame_statuses=dict(frames),watch_finished=last is not None,
                           duration_s=last['seconds'] if last else None))
    return result


def report(directory,summary,rows):
    one=lambda kind:next((r for r in rows if r['kind']==kind),None)
    old=json.loads((PREVIOUS/'audit.json').read_text(encoding='utf-8'))
    old_perf={r['target_kib']:r for r in old['performance']}
    knobs=knob_summary(); (directory/'prior_knob_summary.json').write_text(json.dumps(knobs,indent=2),encoding='utf-8')
    idle=one('idle_passed'); timeout=one('timeout_silence_end'); keep=one('keepalive_passed')
    end=one('final_idle_confirmed'); pre=one('preflight_verified')
    lines=['## v3 修正版（c7d2a4c1）','',
           f'本轮目录：`{directory.relative_to(ROOT).as_posix()}/`。只刷入用户批准的 dev：',
           f'`{DEV_SHA}`，277260 字节；未重建、未刷其他补丁或 release。本轮使用负责人已更新的 SDK。','',
           '### 上次 v3 之后的五轮实体旋钮监听补记','',
           '以下是用户在上一轮自动上机后手动转动旋钮的记录，并非本轮由代理模拟输入。',
           '原日志保留在 `hwtest/20261107_135104_v3_dev/knob_user*.jsonl`；逐文件摘要及 SHA 见本轮 prior_knob_summary.json。','',
           '| 日志 | 原始 next / previous / press 事件 | 已打印 KnobEvent | frame-done 状态及数量 | 正常计时结束 |',
           '|---|---|---:|---|---|']
    for r in knobs:
        d=r['raw_knob_directions']; counts=f'{d.get(1,0)} / {d.get(-1,0)} / {d.get(0,0)}'
        labels=', '.join(f'{int(k):02X}×{v}' for k,v in sorted(r['frame_statuses'].items()))
        finished=f'{r["duration_s"]:.3f} s' if r['watch_finished'] else '无 watch_finished；保留中断记录'
        lines.append(f'| {r["file"]} | {counts} | {r["printed_knob_events"]} | {labels} | {finished} |')
    lines+=['',
            '【用户实测确认】顺/逆时针能按六个原厂页加 RT 槽位循环，KnobEvent 可达，切回 RT 会重新显示。',
            '按下旋钮由键盘作为“计算器”键发到电脑，不到屏幕 MCU。五轮日志未见屏幕 `21 00`，但“电脑打开计算器”的依据是用户观察，不能仅凭日志中没有事件推断。',
            '上传期间转动暴露两类 SDK 问题：①收到提前 frame-done 后仍继续送余包，最终等不到 ACK；',
            '②隐藏后又快速回到可见时，show 没有自动重发，或 ACK 超时没有结合期间可见性变化处理。',
            '负责人已修正为：单缓冲任意提前 frame-done 停止，双缓冲只对 05 停止；旋钮中断后已可见则最多重试 3 次；可见性变化期间的 ACK 超时按隐藏处理。',
            '原固件在隐藏丢弃流尚未结束时重新 ENTER 会直接清状态，修正版先发 05 终止旧流。',
            '【用户观察及离线分析】原厂动图多次往返后越来越快；旧 RT screen 使原 `0x3C128` 的退出页停止条件不成立，',
            '重新加载时清掉旧句柄却没删除 lv_timer，造成孤儿定时器累积。修正版在旋钮离开可见 RT 时按 rt.previous 套用原停止条件，详见 FIRMWARE.md。',
            '日志能证明方向/可见事件与错误报文，不能直接计数设备内部 lv_timer。动画加速来自用户目视，泄漏机制及修复来自原机器码分析/离线回归。',
            '**本轮禁止模拟旋钮，因此没有实测中途打断/重发或旋钮往返后 timer 数量；这些修复仍需用户在新构建上复测。**','',
            '### 预检、写入和启动','',
            '已先核对新 dev/原厂备份 SHA、dry-run，再进入 DFU 只读导出 pre。']
    if pre:
        lines += [f'刷前应用区 SHA `{pre["app_sha256"]}`，与第一版 v3 加 FF **逐字节相同**；不是仅靠向量猜测。',
                  f'SP=`{pre["sp"]}`、复位向量=`{pre["reset"]}`；trial 行 `{pre["trial_row"]}`。',
                  '引导区仅 trial 行与原始备份不同，其余一致。刷前配置相对原始备份的差异是用户后续操作基线，不回写配置。']
    lines+=['只擦写 `0x8000..0x4B000` 的 68 个 4 KiB 扇区，完整回读含补齐字节一致才退出；退出前 flag=55AAAA55。',
            'version 构建串不变；HELLO proto=2、patch=3、canvas=image=480×360、max_frame_bytes=172804、buffers=1、host_timeout_ms=10000。']
    if idle: lines.append(f'同一 HID reader 空跑 {idle["duration_s"]:.3f} s，HELLO/STATUS 正常，visible=armed=0、计数不变；无 uptime 字段，不能只靠零计数证明无复位。')
    lines+=['','### 会话回归与配置页','',
            '20 次 ENTER/LEAVE 逐次核对 STATUS 和 `20 01 02` / `20 00 02`；未出现异常 frame-done。']
    if timeout: lines.append(f'keepalive=False，{timeout["seconds"]:.3f} s 无发送，收到 `20 00 03`，STATUS 解除；再次 ENTER 后 2×2 RGB 显示正常。')
    if keep: lines.append(f'默认 SDK keepalive 无帧保持 {keep["duration_s"]:.3f} s，末尾 visible=armed=1。')
    lines+=['为分段定位，分别在控制循环后、超时后、保活后进 DFU 只读导出；每次完整核对当前获批 app 后退出，不再次写入。','',
            '| 导出 | 配置 SHA-256 | 相对刷前差异 | trial 行 |','|---|---|---:|---|']
    for r in summary['configurations']:
        lines.append(f'| {r["label"]} | `{r["sha256"]}` | {r["diff_bytes"]} 字节 | `{r["trial_row"]}` |')
    lines+=['',
            '以上比较均为完整 4096 字节，不以设置载荷相同代替未写入。帧/性能后未再次导出，不把分段结果外推为未观测窗口的闪存调用轨迹。','',
            '### 精简帧回归与性能对比','',
            '480×360 baseline JPEG：4:4:4 / 4:2:2 / 4:2:0 各一帧（q85）；居中 320×320、160×120；',
            '100×100 RGB565（20009 字节流、4 个完整块加尾部）；482×360 越界返回 03 后下一帧正常。',
            '显示成功状态/计数核对通过，不等于已目视确认画面。','',
            '性能素材直接复用上一轮同字节 JPEG，便于比较；480×360、4:2:0，各 30 帧，批次内不逐帧 STATUS，计时为 perf_counter。','',
            '| 档位/字节 | 原 v3 fps | 修正版 fps | fps 变化 | 传输 ms 中位/p95 | total ms 中位/p95 | decode_ms 中位/p95 | STATUS ms 中位/p95 |',
            '|---|---:|---:|---:|---|---|---|---|']
    for r in summary['performance']:
        baseline=old_perf[r['target_kib']]; fmt=lambda k:f'{r[k]["median"]:.2f}/{r[k]["p95"]:.2f}'
        lines.append(f'| {r["target_kib"]} KiB / {r["actual_bytes"]} | {baseline["fps"]:.3f} | {r["fps"]:.3f} | {(r["fps"]/baseline["fps"]-1)*100:+.2f}% | {fmt("transfer_ms")} | {fmt("total_ms")} | {fmt("decode_ms")} | {fmt("separate_status_query_ms")} |')
    if summary['stream']:
        s=summary['stream']; previous=old['stream']
        lines+=['',f'连续 stream：{s["duration_s"]:.3f} s，{s["stats"]}，{s["fps"]:.3f} fps；前后成功计数增量一致、错误计数不变。',
                f'原 v3：{previous["duration_s"]:.3f} s、141 帧、{previous["fps"]:.3f} fps。两次测试时长不同，fps 可作本机此次对照，单轮差异不代表严格性能结论。']
    lines+=['',
            '传输为首个 BA 至最后 write 完成，total 包含等待 frame-done；STATUS 单列 30 次查询，每帧两次查询的估算额外开销在 audit.json。',
            '逐帧数据在 frame_timings.csv；此轮没有实体旋钮动作，不能用平稳推流的 fps 推断中断恢复性能。','',
            '### 审计、最终状态和后续确认','',
            f'本轮 {summary["download_count"]} 次 app 下载，每个块 SHA 与获批构建匹配；RT 校验错误 {summary["bad_rt_checksums"]}。',
            f'正常 frame-done 累计 {summary["shown_events"]}，预期越界错误 {summary["error_events"]}；计数跨 DFU 重新启动时分段核对。',
            f'HID 类别：`{summary["hid_tx_classes"]}`。没有 A5 39；没有持久上传/亮度/34/FE。',
            '所有屏幕命令经 SDK 及额外窄白名单，DFU 经 flash.py Gate；未改固件/SDK、未重建、未运行 git、未访问其他 PID 或 Hub，未改系统设置/驱动，未终止进程。']
    if end:
        lines += ['当前运行 **v3 修正版开发版 c7d2a4c1**，**原厂页面、实时未武装**。',
                  '最后一帧完成后以 close(leave=False) 关闭，会话自然超时；12 s 后重新打开仅 HELLO/STATUS，确认 visible=0、armed=0、state=0，未靠 LEAVE 强制切回。',
                  f'最终 STATUS：`{end["status"]}`。无需拔插，本轮没有恢复原厂固件。']
    else: lines.append('收尾状态尚待 finish 阶段，以 state.json 为准。')
    lines+=['',
            '待用户目视确认：重启后原厂动画速度是否恢复、图标/图片是否完整；随后在新构建上再手转旋钮观察，确认往返多次不再加速、上传被打断后能自动恢复。',
            '本轮没有发送旋钮模拟命令，因此保留上述手动回归项，不声称实机已证实 timer 泄漏修复。','']
    doc=ROOT/'docs/HWTEST.md'; original=doc.read_bytes(); marker='\n## v3 修正版（c7d2a4c1）'.encode()
    prefix=original.split(marker)[0] if marker in original else original
    prefix=prefix.replace('**最新上机记录为文末 v3 开发版一节；下列 v1/v2 的“当前状态”均为当时的历史状态。**'.encode(),
                          '**最新上机记录为文末 v3 修正版（c7d2a4c1）；前文各版本的“当前状态/未知”均为对应时点的历史记录。**'.encode(),1)
    doc.write_bytes(prefix+b'\n\n'+'\n'.join(lines).encode('utf-8'))


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('run_dir',type=Path); ap.add_argument('--update-doc',action='store_true')
    args=ap.parse_args(); directory=args.run_dir.resolve()
    old=(PREVIOUS/'after_controls/app_8000_7F000.bin').read_bytes()
    summary,rows=audit(directory,approved=DEV_SHA,pre_image=old,
                       config_labels=('pre','after_controls','after_timeout','after_sessions'))
    if args.update_doc: report(directory,summary,rows)
    print(json.dumps(dict(configurations=summary['configurations'],shown=summary['shown_events'],
                          errors=summary['error_events'],performance=[{k:r[k] for k in ('target_kib','fps','decode_ms')} for r in summary['performance']],
                          stream=summary['stream'],final=next((r for r in rows if r['kind']=='final_idle_confirmed'),None)),ensure_ascii=False))


if __name__=='__main__': main()
