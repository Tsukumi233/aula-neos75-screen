"""Offline regression, knob-seed and configuration audit for f1fefb2e."""
import argparse
from collections import Counter
import json
from pathlib import Path

from report_v3 import audit,load,ROOT
from run_v3d import APPROVED_SHA,PREVIOUS
from neos75screen import protocol as p


def seed_audits(directory):
    results=[]; last=0
    for seed in (1,2):
        path=directory/f'knob_auto_s{seed}.jsonl'
        if not path.exists(): continue
        rows=load(path); summary=next((r for r in reversed(rows) if r['kind']=='summary'),None)
        steps=[r for r in rows if r['kind']=='step']; frames=Counter(); directions=Counter(); tx=0; bad=0
        for r in rows:
            if r['kind'] not in ('hid_tx','hid_rx'): continue
            raw=bytes.fromhex(r['report'])
            if r['kind']=='hid_tx':
                assert raw[0]==0xba or raw[:2] in (b'\xa5\x3b',b'\x11\x01',b'\x11\x02')
                if raw[:2]==b'\xa5\x3b' and raw[5]==0x30:
                    assert raw[6] in (1,255) and p.a5_checksum_ok(raw); tx+=1
                if p.looks_like_ba_start(raw): assert raw[3] in (0x5a,0x5b)
            else:
                if raw[:2]==b'\xa5\x3b': bad+=not p.a5_checksum_ok(raw)
                event=p.parse_event(raw)
                if isinstance(event,p.KnobEvent): directions[event.direction]+=1
                if p.is_frame_done(raw):
                    e=p.parse_frame_done(raw); assert e.seq==(last+1)&65535
                    last=e.seq; frames[e.status]+=1
        assert bad==0
        results.append(dict(seed=seed,file=str(path.relative_to(ROOT)),steps_recorded=len(steps),
                            summary=summary,injection_requests=tx,directions=dict(directions),
                            frame_statuses=dict(frames),bad_checksums=bad))
    (directory/'knob_seed_audit.json').write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')
    return results


def report(directory,a,rows):
    old=json.loads((PREVIOUS/'audit.json').read_text(encoding='utf-8'))
    baseline={r['target_kib']:r for r in old['performance']}; seeds=seed_audits(directory)
    config_path=directory/'knob_config_audit.json'
    config=json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else None
    one=lambda kind:next((r for r in rows if r['kind']==kind),None)
    idle=one('idle_passed'); end=one('final_idle_confirmed'); restore=one('restore_boot_verified')
    lines=['## v3 开发版 + 测试旋钮注入（f1fefb2e）','',
           f'运行目录 `{directory.relative_to(ROOT).as_posix()}/`；只刷获批 dev',
           f'`{APPROVED_SHA}`（277324 字节），未重建、未刷 release 或其他补丁。',
           '开发版测试命令为 SDK inject_knob → A5 3B 30 01/FF → 原厂 producer 0x1CD4C → GUI 按键回调。',
           '不使用原厂 A5 39，不测试电脑端 capture_knob 或实体按下。','',
           '### 常规回归与 cba6f2c7 对比','',
           '预检核对 SHA、dry-run、只读 pre dump；刷前 app 与 cba6f2c7 加 FF 逐字节相同，SP/复位向量合法。',
           '引导区除 trial 行与原备份相同；只写 68 个 app 扇区，完整回读后退出，flag 仍为55AAAA55。',
           '构建串不变；HELLO proto=2/patch=3、canvas=image=480×360、max_frame_bytes=172804、timeout=10000、flags=1、test_knob=true。']
    if idle: lines.append(f'空跑 {idle["duration_s"]:.3f} s 正常；20次 ENTER/LEAVE、12s无发送超时、30s默认保活均通过。')
    lines+=['','| 配置检查点 | SHA-256 | 相对刷前差异 |','|---|---|---:|']
    for r in a['configurations']:
        lines.append(f'| {r["label"]} | `{r["sha256"]}` | {r["diff_bytes"]} 字节 |')
    lines+=['',
            '注入前静默回归要求0差异；注入后的页号追加另按下节审计，不将允许的用户选页保存当作回归失败。',
            '与cba6相同的空跑/控制/超时/保活窗口均为0差异；cba6那轮未做开发命令注入，不能仅凭本次新覆盖窗口出现写入就断定是版本引入的回归。',
            '精简帧回归：480×360 JPEG 三种采样各一帧，320×320/160×120居中，100×100 RGB565跨块；越界482×360返回03后正常恢复。',
            '性能素材与 cba6f2c7 相同，480×360 4:2:0，每档30帧；批次内不逐帧STATUS，查询开销单列。','',
            '| 档位/字节 | cba6 fps | 本轮 fps | 变化 | 传输ms 中位/p95 | decode_ms 中位/p95 | STATUS ms中位/p95 |',
            '|---|---:|---:|---:|---|---|---|']
    for r in a['performance']:
        prev=baseline[r['target_kib']]; fmt=lambda k:f'{r[k]["median"]:.2f}/{r[k]["p95"]:.2f}'
        lines.append(f'| {r["target_kib"]}/{r["actual_bytes"]} | {prev["fps"]:.3f} | {r["fps"]:.3f} | {(r["fps"]/prev["fps"]-1)*100:+.2f}% | {fmt("transfer_ms")} | {fmt("decode_ms")} | {fmt("separate_status_query_ms")} |')
    if a['stream']:
        s=a['stream']; prev=old['stream']
        lines+=['',f'连续流 {s["duration_s"]:.3f} s，{s["stats"]}，{s["fps"]:.3f} fps；cba6 为{prev["fps"]:.3f} fps。',
                '前后计数一致；单次小差异不作严格性能结论。audit.json/CSV保存完整耗时分布。']
    lines+=['','### 两轮无人值守注入','',
            '先只读导出 pre_knob；按指定命令使用默认间隔0.15–0.6s，steps=200，seed=1、2分别运行。',
            '脚本检查每步方向、七位置循环、可见事件、Screen.visible及每10步STATUS；槽位可见时发送帧。',
            '**覆盖边界：当前脚本顺序执行“注入→等事件→必要时show”，并没有独立线程在一个上传中途注入；即使单轮通过，也不能等同于极快实体旋钮打断所有上传时序都已验证。**','',
            '| seed | 已记录步数 | 失败项 | settles | 注入请求数 | frame-done | 日志 |',
            '|---:|---:|---|---:|---:|---|---|']
    for r in seeds:
        s=r['summary'] or {}; failures=s.get('failures',['Missing summary'])
        detail='0' if not failures else f'{len(failures)}（首项：{failures[0]}；全部见日志 summary）'
        lines.append(f'| {r["seed"]} | {r["steps_recorded"]} | {detail} | {s.get("stock_page_settles","未知")} | {r["injection_requests"]} | {r["frame_statuses"]} | `{r["file"]}` |')
    lines+=['','### 注入后的配置记录审计','']
    if config:
        lines += [f'基线 SHA `{config["before_sha256"]}`；回读 SHA `{config["after_sha256"]}`，差异{config["diff_bytes"]}字节。',
                  f'追加记录 {config["appended_records"]} 条；settles合计{config["settles"]}，允许上限{config["record_limit"]}（settles+2）。',
                  f'记录变化：`{config["transitions"]}`。审计问题：`{config["issues"]}`。',
                  '逐个比较新增32字节记录的payload，只允许key0F/10；同时检查旧有效记录失效标记、其余地址不变、最终仅一条有效记录且页号<6。',
                  '旧记录失效会清前16字节，本轮基线低12个配置字节为0，允许逐记录比较；若不是0，脚本会拒绝给出完整通过结论。',
                  '原始导出和完整分析在 pre_knob/、after_knob/、knob_config_audit.json，未清除/回写配置。']
    else: lines.append('配置追加审计尚未完成。')
    lines+=['','### 失败复现与离线定位','',
            '**观察：seed 1 失败，seed 2 通过，不能将整轮评为通过。** seed 1 的153项是断言失败项数，并非153次独立故障；起始位置失步会使后续位置、可见性及STATUS断言连带失败。',
            '复现条件：退出DFU后立即运行原命令 `sdk/.venv/Scripts/python -B hwtest/knob_auto.py --steps 200 --seed 1 --log hwtest/<目录>/knob_auto_s1.jsonl`，保持默认间隔。',
            '本轮DFU退出18:38:28.520；首个注入18:38:31.099，方向21事件18:38:31.118，第一步没有隐藏20事件；第二次注入18:38:33.094后才在18:38:33.327收到隐藏事件。',
            '两轮均200条注入请求，方向事件各200条，未发现注入重发/方向事件丢失。seed 1后续SDK认为隐藏时直接不发帧；不能把这些hidden结果计为成功显示。',
            '**离线观察：** `hwtest/offline_knob_startup.py` 只在Unicorn中把现有模拟器显式设置的快速启动RAM标志0x5A5A5A5A改成0，随后执行获批镜像的原厂启动/动画/输入/切页代码。',
            '原0xECC0→0x37EF4创建开机页面，0xEE00与动画开始回调0x3967C写0到0x2000BF77；原动画结束回调0x395F8在0x39614写1并调0x3C128。',
            '在门禁0时ENTER和帧成功，第一次next只发21、active仍1；原LVGL timer执行到动画结束后门禁1，下一次next才切原厂0页并发20 00 01。结果见offline_startup_probe.json，关键原函数未打桩返回。',
            '**推断：** seed 1第一步遇到原厂开机门禁，但脚本仍无条件把逻辑位置加一，后续断言持续错位；与原始时间线及seed 2启动稳定后0失败一致。未在硬件读取该RAM，故不能把门禁值称作硬件实测。',
            '建议后续测试在每次DFU退出后等待原厂启动动画完成（约5s并留余量），再ENTER；并在脚本中明确门禁下的事件与位置模型。不得通过绕过原厂锁来让测试通过。现有模拟器的StockGUI.bootstrap采用快速启动，未覆盖这条启动窗口。',
            '',
            '**配置写入观察：** 新增记录0x7FEC0、0x7FEE0分别把key11从03改02再改03，违反本轮仅允许key0F/10的验收；最终载荷相同不代表没有写入。',
            '**离线观察：** `hwtest/offline_knob_status.py` 在实时已隐藏、恢复保护窗口已结束时，向Unicorn原0x35220分发器喂A5 35状态输入02→03，执行原GUI timer/0x3401C→0x3370C→3379C补丁包装→0x179D8→0x1788C/0x178B4。',
            '两次均真实调用内部写包装，分别追加一条key11记录；完全未武装的对照也产生同样链和配置变化。外设/真正编程仅在既有StockGUI边界建模，链上原逻辑未打桩；完整栈、RAM写入与参数见offline_status_probe.json。',
            '**推断与未知：** 这提供了“隐藏原厂页期间外部状态变化导致原有被动保存”的具体机制，不能证明硬件两条记录恰来自该路径；本轮未捕获键盘UART或保存调用栈，也不能把两次写入分配给某个seed/step。不能仅凭此全局屏蔽key11保存，破坏原厂行为。',
            '建议后续先离线增加冷启动/原厂页遥测变化对照，明确允许的原厂状态保存与实时恢复保护窗口；硬件进一步定位须单独设计可观测证据，本轮不增刷构建。',
            '',
            '测试工具插曲：首次配置审计误用SDK虚拟环境，缺少PyUSB，在任何设备操作前失败；自动恢复入口同样因导入失败未访问设备。随后改用已有firmware虚拟环境完成只读审计。保留两次stdout/stderr与stage_failed，最终恢复由第二次真实配置审计失败触发。',
            '', '### 结果、问题与最终状态','',
            f'主流程RT校验错误{a["bad_rt_checksums"]}，app下载{a["download_count"]}个扇区，每个块SHA已核对；种子流量另见knob_seed_audit.json。',
            '所有注入仅通过原脚本调用SDK inject_knob，白名单未放宽；未发送A5 39/34/FE、持久壁纸或亮度。进入DFU仅专用工具固定13 02。',
            '未改SDK/固件、未重建、未运行git、未访问其他PID/Hub、未改系统/驱动或结束进程。']
    if restore: lines += ['配置审计失败后停止后续收尾RT帧，已按授权 `tools/flash.py --restore` 恢复原厂。',
                          '恢复前dry-run；仅写119个app扇区0x8000..0x7E000，回读SHA为原备份 `66679ff2cb06ab5a5e47673f679e14e68bf2082fb6401b3808ca58d7f2e1a40c` 后才退出DFU。',
                          '开发版写入1次（68扇区），原厂恢复1次（119扇区）；主日志总187个下载扇区，未写引导区或配置页。',
                          f'恢复启动证据：`{restore}`。',
                          '**设备最终运行原厂应用、原厂页面，无实时会话，无需拔插。** 恢复仅回写app，不回滚本轮已追加的配置记录。',
                          '本轮只做离线定位，未改固件/SDK、未重建或再刷补丁。用户可目视确认原厂图标/图片和动画正常；未完成新的整屏RT检查图收尾。']
    elif end: lines += ['设备最终运行 **f1fefb2e 测试旋钮开发版，原厂页面、实时未武装**。',
                        '会话close(leave=False)自然结束，12s后只查询确认visible=0/armed=0，不靠LEAVE强制返回。',
                        f'最终STATUS `{end["status"]}`，无需拔插。']
    else: lines.append('最终状态以state.json为准；收尾尚未确认。')
    lines += ['', '失败字符串保留具体step和复现种子；后续上机复测需重新获得批准。本轮保留原日志，仅做离线分析，未刷新新构建。','']
    doc=ROOT/'docs/HWTEST.md'; data=doc.read_bytes(); marker='\n## v3 开发版 + 测试旋钮注入（f1fefb2e）'.encode()
    prefix=data.split(marker)[0] if marker in data else data
    prefix=prefix.replace('**最新上机记录为文末 v3 修正版 2（cba6f2c7）；前文各版本的“当前状态/未知”均为对应时点历史。**'.encode(),
                          '**最新上机记录为文末测试旋钮开发版（f1fefb2e）；前文状态为对应时点历史。**'.encode(),1)
    doc.write_bytes(prefix+b'\n\n'+'\n'.join(lines).encode('utf-8'))


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('run_dir',type=Path); ap.add_argument('--update-doc',action='store_true')
    args=ap.parse_args(); directory=args.run_dir.resolve()
    a,rows=audit(directory,approved=APPROVED_SHA,pre_image=(PREVIOUS/'after_controls/app_8000_7F000.bin').read_bytes(),
                 config_labels=('pre','after_controls','after_timeout','after_sessions','pre_knob','after_knob'))
    if args.update_doc: report(directory,a,rows)
    seeds=seed_audits(directory)
    print(json.dumps(dict(configs={r['label']:r['diff_bytes'] for r in a['configurations']},
                          seeds=[dict(seed=r['seed'],steps=r['steps_recorded'],
                                      failures=len((r['summary'] or {}).get('failures',[])),
                                      settles=(r['summary'] or {}).get('stock_page_settles')) for r in seeds],
                          stream_fps=a['stream']['fps'] if a['stream'] else None,
                          stage_failures=[r['error'] for r in a['failures']]),ensure_ascii=False))


if __name__=='__main__': main()
