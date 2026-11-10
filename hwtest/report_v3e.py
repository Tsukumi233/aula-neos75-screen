"""Offline audit of the 20-second startup wait retest; never opens a device."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path

from report_v3 import audit,load,ROOT
from run_v3e import APPROVED_SHA, audit_records
from neos75screen import protocol as p


def build_report(directory, update_doc=False):
    result, rows=audit(directory,approved=APPROVED_SHA,
                       config_labels=('pre','after_controls','after_knob'))
    assert not any(r['kind']=='screen_close_error' for r in rows)
    seeds=[]; raw_rows=list(rows); last=0
    for seed in (1,2,3):
        file=directory/f'knob_auto_s{seed}.jsonl'; data=load(file)
        if not data: continue
        raw_rows.extend(data)
        summary=next((r for r in data if r['kind']=='summary'),None)
        requests=0; turns=Counter(); frames=Counter(); bad=0
        for r in data:
            if r['kind'] not in ('hid_tx','hid_rx'): continue
            packet=bytes.fromhex(r['report'])
            if r['kind']=='hid_tx':
                assert packet[0]==0xba or packet[:2] in (b'\xa5\x3b',b'\x11\x01',b'\x11\x02')
                if packet[0]==0xba and p.looks_like_ba_start(packet): assert packet[3] in (0x5a,0x5b)
                if packet[:2]==b'\xa5\x3b' and packet[5]==0x30:
                    assert packet[6] in (1,255) and p.a5_checksum_ok(packet)
                    requests+=1
            else:
                if packet[:2]==b'\xa5\x3b': bad+=not p.a5_checksum_ok(packet)
                e=p.parse_event(packet)
                if isinstance(e,p.KnobEvent): turns[e.direction]+=1
                if p.is_frame_done(packet):
                    e=p.parse_frame_done(packet)
                    assert e.seq==(last+1)&65535
                    last=e.seq; frames[e.status]+=1
        assert bad==0
        seeds.append(dict(seed=seed,steps=len([r for r in data if r['kind']=='step']),
                          summary=summary,inject_requests=requests,turns=dict(turns),
                          frame_statuses=dict(frames),bad_checksums=bad,
                          sync=[r for r in data if r['kind'] in ('sync','sync_back')],
                          path=file.relative_to(ROOT).as_posix()))
    quiet=[]; start=None
    for r in rows:
        if r['kind']=='dfu_silence_begin':
            assert start is None; start=r
        elif r['kind']=='dfu_silence_end':
            assert start is not None and r['elapsed_s']>=20
            lo=datetime.fromisoformat(start['time']); hi=datetime.fromisoformat(r['time'])
            traffic=[v for v in raw_rows if v['kind']=='hid_tx' and lo<=datetime.fromisoformat(v['time'])<=hi]
            assert not traffic
            quiet.append(dict(start=start['time'],end=r['time'],elapsed_s=r['elapsed_s'],hid_reports=len(traffic)))
            start=None
    assert start is None
    config_path=directory/'knob_config_audit.json'
    config=json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else None
    if config:
        independent=audit_records((directory/'after_controls/config_7F000_80000.bin').read_bytes(),
                                  (directory/'after_knob/config_7F000_80000.bin').read_bytes())
        assert all(config[k]==v for k,v in independent.items())
    output=dict(seeds=seeds,quiet_windows=quiet,config=config,
                history_complete=bool(config is not None and not config['issues']),
                key11_count=len(config['key11_positions']) if config is not None and not config['issues'] else None,
                dev_sha256=APPROVED_SHA,script_sha256=hashlib.sha256((ROOT/'hwtest/knob_auto.py').read_bytes()).hexdigest())
    (directory/'retest_audit.json').write_text(json.dumps(output,indent=2,ensure_ascii=False),encoding='utf-8')
    if update_doc:
        one=lambda k:next((r for r in rows if r['kind']==k),None)
        restore=one('restore_boot_verified'); final=one('final_idle_confirmed')
        lines=['## f1fefb2e 重测（开机等待 20 s）','',
               f'目录 `{directory.relative_to(ROOT).as_posix()}/`。获批开发版 SHA-256：',
               f'`{APPROVED_SHA}`，277324字节；未重建、未刷release或其他补丁。',
               '本轮使用负责人更新后的knob_auto解锁同步：先next，看到隐藏事件后previous回槽位，然后开始200步随机测试。',
               f'实际脚本SHA `{output["script_sha256"]}`；没有修改该脚本、SDK或固件。','',
               '### 刷写、静默等待和精简回归','',
               '刷前dry-run，pre只读导出；app与原厂备份逐字节相同，引导区除trial行外与备份相同。',
               '开发版刷写覆盖68个app扇区0x8000..0x4B000，逐块与获批镜像核对并完整回读后退出DFU；主机刷写不涉及配置页，下文审计原厂自行保存。',
               '每次退出DFU后先关闭所有本轮SDK会话，静默等待20s，再打开HID、HELLO。等待覆盖日志如下；该表审计本轮所有发送日志，不声称排除了其他未知软件。','',
               '| 开始 | 结束 | 时长s | 本轮HID报告数 |','|---|---|---:|---:|']
        for q in quiet: lines.append(f'| {q["start"]} | {q["end"]} | {q["elapsed_s"]:.6f} | {q["hid_reports"]} |')
        startup=one('startup_verified')
        if startup: lines+=['',f'启动证据：`{startup}`。HELLO proto2/patch3，canvas=image480×360，max172804，host_timeout10000，flags1/test_knob=true。']
        lines+=['',f'完成控制循环{sum(r["kind"]=="control_cycle" for r in rows)}次；每次检查visible/armed和20 01 02/20 00 02事件。',
                '12s停止主机发送后检查20 00 03及解除武装，再ENTER发送2×2 RGB；默认keepalive无帧保持30s。',
                '合并以上测试后才导出after_controls，避免额外启动周期；它同时作为pre_knob基线，不重复导出。','',
                '| 配置检查点 | SHA-256 | 相对刷前差异 |','|---|---|---:|']
        for c in result['configurations']: lines.append(f'| {c["label"]} | `{c["sha256"]}` | {c["diff_bytes"]}字节 |')
        lines+=['','### 三轮旋钮注入','',
                '三轮之间没有进入DFU或重启；使用指定SDK虚拟环境、默认间隔0.15–0.6s、steps200，seed依次1/2/3。同步阶段的额外next/previous不算在200步内，但计入所有方向请求与事件的审计。',
                '本轮不测capture_knob，不发送A5 39/34/FE或持久壁纸；注入仅经SDK A5 3B 30，进入DFU仅固定13 02工具。','',
                '| seed | 随机步数 | 失败数 | stock_page_settles | 注入请求/方向事件（含同步） | frame-done状态统计 | 日志 |',
                '|---:|---:|---:|---:|---|---|---|']
        for s in seeds:
            summary=s['summary'] or {}; count=len(summary.get('failures',['Missing summary']))
            lines.append(f'| {s["seed"]} | {s["steps"]} | {count} | {summary.get("stock_page_settles","未知")} | {s["inject_requests"]}/{sum(s["turns"].values())} | `{s["frame_statuses"]}` | `{s["path"]}` |')
        lines+=['','完整sync/sync_back和summary.failures保留在各JSONL；retest_audit.json检查跨三轮frame-done序号连续，证明计数未重置，属于有限稳定性证据，不等于协议提供了uptime。',
                '各轮沿用原knob_auto的finally close(leave=True)；轮次间不修改脚本、不通过DFU重启。最后只读确认会话则使用close(leave=False)。',
                'settles沿用原脚本统计，是基于循环耗时的估计；本次恢复条件按新授权的键/地址/格式，不再以key11或settles+2单独触发。','',
                '### 配置记录逐条审计','']
        if config:
            lines += [f'前后差异{config["diff_bytes"]}字节，新增{config["appended_records"]}条；key11记录位置（从1开始）：`{config["key11_positions"]}`。',
                      f'格式/其他键/地址审计问题：`{config["issues"]}`。','',
                      '| 序号 | 地址 | 变化key及旧→新（十进制） | 页号 | key10 | key11 |',
                      '|---:|---|---|---:|---:|---:|']
            for t in config['transitions']:
                lines.append(f'| {t["number"]} | {t["address"]} | `{t["changes"]}` | {t["page"]} | {t["overlay"]} | {t["mode"]} |')
            if not config['transitions']: lines.append('| — | — | 无新增记录 | — | — | — |')
            lines += ['', '审计允许key0F/10/11，仅允许新记录与旧有效记录前16字节失效标记变化；逐payload比较其余key不变，并确认最后仅一条有效记录、持久页号<6。',
                      '随机循环的settles不计开头同步阶段，也不是原厂pending页号保存的精确次数；pending计时会在转回实时槽位后继续完成。因此4条页号保存与settles=0并不矛盾，本次按新授权逐键审计，不使用旧的settles+2上界。',
                      '前低12个配置字节均为0，因此旧记录失效的清零可完整重建；若发生擦页重组或其他格式变化，审计会拒绝。原始记录hex在knob_config_audit.json。']
        else: lines.append('尚未完成注入后审计。')
        lines+=['','### 与上次对比、结论和最终状态','',
                '上次未等待20s，seed1为153个断言失败、seed2为0；配置新增9条，其中第7、8条为key11的03→02→03，最终恢复原厂。',
                '本次同时增加20s静默与解锁同步，是两个变化；因此不能把随机循环改进单独归因于其中一个。']
        pre=(directory/'pre/config_7F000_80000.bin').read_bytes()
        history_note='本轮保留完整追加历史。' if config is not None and not config['issues'] else '完整历史尚不可确认。'
        lines.append(f'本轮刷前配置有{sum(pre[i:i+32]==bytes([255])*32 for i in range(0,4096,32))}个空记录槽；{history_note}若发生原厂擦页回收，一次结束快照无法还原已擦除的历史写入。')
        if config:
            n=len(config['key11_positions'])
            if config['issues']:
                lines.append('**完整历史审计失败，无法判断key11是否曾在已擦除的序列中再现。** 空的位置列表不能解释为0次写入。')
            else:
                lines.append(f'**本次key11{"再现" if n else "未再现"}：{n}条。**')
                lines.append('【推断】20s等待后未再现，与“启动期键盘状态短暂变化”相容；没有捕获UART状态或硬件保存栈，仍不能证明因果。' if not n else
                             '【推断】20s等待后仍再现，说明本次等待不足以排除该保存路径；没有UART/硬件保存栈，不能确定具体触发源。')
        related_file=directory/'related_attempt.txt'
        if related_file.exists():
            related=ROOT/related_file.read_text(encoding='utf-8').strip()
            earlier=json.loads((related/'knob_config_audit.json').read_text(encoding='utf-8'))
            lines+=['','### 同次授权的首次尝试与恢复','',
                    f'保留目录 `{related.relative_to(ROOT).as_posix()}/`。首次三轮也是各200步、0失败、settles0，控制前后配置0差异。',
                    f'但刷前只有3个空槽，原厂后续保存使配置页正常回收；结束快照与基线差异{earlier["diff_bytes"]}字节，只剩0x7F000一条有效记录（页4、key11=03），旧序列已擦除。',
                    '这是历史可观测性不足，不能称作配置损坏，也不能声称key11未再现。审计因旧地址被擦除而拒绝完整通过，脚本按保守恢复路径写回原厂；不是因发现key11而恢复。',
                    '恢复app完整回读原备份SHA，退出后同样20s无HID，再验证原厂启动。未手工擦除/写回配置；127个空槽来自原厂自行回收。',
                    '在现有同一SHA授权内再次刷入获批镜像，并从空间充足的真实配置基线完整重测；增加刷前至少16个空槽检查，避免重演该审计限制。',
                    '本次授权合计开发版写入2次、原厂恢复1次，没有刷入其他补丁；首次retest_audit.json保留原始失败与全部事件。']
        if restore: lines+=['',f'异常后已恢复原厂，证据 `{restore}`；恢复仅写app，未回滚配置记录。']
        elif final:
            assert len(quiet)==3
            lines+=['',f'最终 `{final}`。设备保持获批f1fefb2e开发版、原厂页面、visible=0/armed=0；关闭会话不发送LEAVE或额外命令。',
                            '最后一次DFU退出后已等待20s，HELLO/STATUS确认；没有启动新的实时会话，无需再等待其超时，无需拔插。']
        else: lines.append('最终状态尚未确认，以state.json及事件日志为准。')
        lines+=['','未运行git、未重建固件、未访问键盘主控/Hub、未改驱动或系统设置、未结束进程。','']
        doc=ROOT/'docs/HWTEST.md'; data=doc.read_bytes(); marker='\n## f1fefb2e 重测（开机等待 20 s）'.encode()
        prefix=data.split(marker)[0]
        prefix=prefix.replace('**最新上机记录为文末测试旋钮开发版（f1fefb2e）；前文状态为对应时点历史。**'.encode(),
                              '**最新上机记录为文末 f1fefb2e 重测（开机等待20s）；前文状态为对应时点历史。**'.encode(),1)
        doc.write_bytes(prefix+b'\n\n'+'\n'.join(lines).encode('utf-8'))
    print(json.dumps(dict(seeds=[dict(seed=s['seed'],steps=s['steps'],failures=len((s['summary'] or {}).get('failures',[]))) for s in seeds],
                          quiet_windows=quiet,history_complete=output['history_complete'],
                          key11_count=output['key11_count'],key11_positions=config['key11_positions'] if config else None),ensure_ascii=False))


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('run_dir',type=Path);ap.add_argument('--update-doc',action='store_true')
    a=ap.parse_args();build_report(a.run_dir.resolve(),a.update_doc)


if __name__=='__main__': main()
