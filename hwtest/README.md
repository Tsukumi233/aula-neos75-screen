# Neos75 上机测试脚本

本次记录：`20261107_approved_dev_hwtest/`，报告见 `../docs/HWTEST.md`。
运行目录内的大文件和日志已忽略；脚本本身可提交。没有运行 git。

`run.py` 的开发版目标固定为 `firmware/build/neos75_rt_dev.bin`，且完整 SHA-256 必须是
`baee341bd20d76f655764ec10ec44d1a5d7b5d550af596ee3e0299bf32cc8f8f`。
它没有重新编译、选择 release 或任意镜像的入口。`restore` 只核对并写回原厂备份；
不依赖开发版文件仍存在，以免后续离线修复妨碍恢复。

本次已获授权并执行；以下命令用于理解和复现流程，不应重复向同一日志目录刷写。
后续首次刷入其他 SHA 仍需用户单独批准。新运行应使用新的时间戳目录。

```powershell
sdk/.venv/Scripts/python -B hwtest/run.py assets --run-dir hwtest/YYYYMMDD_HHMMSS
firmware/.venv/Scripts/python -B hwtest/run.py preflash --run-dir hwtest/YYYYMMDD_HHMMSS
firmware/.venv/Scripts/python -B hwtest/run.py startup --run-dir hwtest/YYYYMMDD_HHMMSS
firmware/.venv/Scripts/python -B hwtest/run.py controls --run-dir hwtest/YYYYMMDD_HHMMSS
firmware/.venv/Scripts/python -B hwtest/run.py frames --run-dir hwtest/YYYYMMDD_HHMMSS
firmware/.venv/Scripts/python -B hwtest/run.py performance --run-dir hwtest/YYYYMMDD_HHMMSS
firmware/.venv/Scripts/python -B hwtest/run.py faults --run-dir hwtest/YYYYMMDD_HHMMSS
firmware/.venv/Scripts/python -B hwtest/run.py final --run-dir hwtest/YYYYMMDD_HHMMSS
firmware/.venv/Scripts/python -B hwtest/report.py hwtest/YYYYMMDD_HHMMSS
firmware/.venv/Scripts/python -B hwtest/run.py confirm --run-dir hwtest/YYYYMMDD_HHMMSS
```

assets 使用 sdk/.venv 的 numpy；其余阶段使用 firmware/.venv 的 SDK/hidapi/Pillow/PyUSB。
`report.py` 完全只读且不访问设备；`confirm` 只发 HELLO/STATUS，不送新帧或退出实时模式。
测试失败且本轮已经开始写入时，脚本停止后续阶段并调用 `restore`；恢复最多等待 DFU 60 s，
仍未出现时停止并记录需要用户拔插，不重复尝试或结束其他进程。

HID 每次发送均经过 SDK guard；另有测试专用窄白名单。坏流只绕开 SDK 高层的图像内容验证，
使用 SDK 协议分包和传输会话，未绕过传输白名单。DFU 全部通过 `tools/flash.py` 的 Gate/Session，
只读导出后退出前也必须完整比对已批准镜像和 trial flag。

## v3 开发版与旋钮观察

`run_v3.py` 固定 SHA 为 `e8b668629aff05c655dbb763c3cc439ec7e1281f4aa10bf9a8a1a95cfee65e0e`，
没有编译入口。主运行目录为 `20261107_135104_v3_dev/`；首轮脚本同步误判及恢复记录保留于
`20261107_134751_v3_dev/`。详细实测见 HWTEST.md 的 v3 节，不要重复执行刷写阶段。
`report_v3.py` 只读取日志/导出，可生成 audit.json、CSV 并更新报告，不打开设备。

物理旋钮观察由用户转动旋钮，脚本不会用 `A5 39` 模拟，也不会发送亮度或持久壁纸命令：

```powershell
sdk/.venv/Scripts/python -B hwtest/knob_watch.py --seconds 120 --log hwtest/knob_user.jsonl
```

它通过 SDK 默认保活，显示大字 RT 和时间；可见事件到来时重发，记录全部旋钮/可见事件及每 5 s 的 STATUS。
结束时 `close(leave=True)`：实时可见则回入口原厂页，已隐藏则保留用户当前原厂页。
本轮先用 `--seconds 5` 做过不转旋钮的连通性验证，不能据此声称物理旋钮已验证。
`guarded_screen.py` 额外限制所有测试发送，并把 SDK 事件过滤器转发到真实接收线程，以正确记录异步事件。
最终检查图阶段使用 `close(leave=False)`；进程结束后停止保活，约 10 s 自然超时回原厂页。

## v3 修正版 c7d2a4c1 回归

`run_v3b.py` 固定获批 SHA `c7d2a4c10438299f495a3f14989953961a6c9ee59dd6cbf7cec79d92a6bbb406`，
刷前对照上一轮导出的第一版 v3 整区；没有编译入口。会话回归分段导出配置，精简帧测试后仅测 20/45 KiB
各 30 帧和 30 s stream。全程不发送 A5 39；finish 关闭会话后等 12 s，只查询确认自然超时，最终留在原厂页。
`report_v3b.py` 只读审计日志、汇总此前五轮 knob_user 日志并追加 HWTEST.md 的修正版一节。
这轮自动回归不验证物理旋钮中断恢复或动画速度；仍须用户在修正版上手动观察。

## v3 修正版 2 cba6f2c7 回归

`run_v3c.py` 以固定常量绑定获批 SHA `cba6f2c716e184668371f1cdd1e2f618b1851bc1fe565629296b35d210c9cc1c`，
复用 v3b 各阶段；刷前逐字节对照 c7d2a4c1，仍无编译入口和任意镜像参数。
`report_v3c.py` 审计本轮并补记上一轮 knob_user1.jsonl 的过期 04 和 STATUS 超时，保留用户日志。
本轮仍不模拟旋钮；极快转动下的过期事件处理及 SDK 请求重试，需要用户在新构建上继续手测。

## 测试旋钮开发版 f1fefb2e

`run_v3d.py` 固定获批 SHA `f1fefb2e6a5711fe979ee27877862d041e17ebf57846454466248239f2dfe048`，
要求 HELLO flags=1/test_knob=true；`knob_baseline` 和 `knob_audit` 分别只读导出注入前后配置。
两轮按用户指定的 `knob_auto.py --steps 200 --seed 1/2`，仅经 SDK inject_knob 发送 A5 3B 30，仍禁止 A5 39。
`knob_audit` 逐记录检查只改页号 key0F/10、追加条数不超过 settles合计+2，并核对旧记录失效之外无其他改写。
`report_v3d.py` 分别审计回归与种子日志并追加报告。脚本的注入和 show 是顺序执行，不能当作所有上传中途打断时序的压力测试。

硬件阶段运行器使用 `firmware/.venv/Scripts/python`（已有PyUSB/libusb依赖）；`knob_auto.py` 使用用户指定的SDK虚拟环境。
本轮seed 1发现原厂开机门禁窗口与脚本位置模型不符，seed 2通过；配置出现key11追加后已恢复原厂，完整结果见HWTEST.md。
`offline_knob_startup.py` / `offline_knob_status.py` 只使用Unicorn，分别复现冷启动门禁和隐藏原厂页状态变化保存链，
以 `--out hwtest/<目录>/<证据>.json` 保存地址、RAM写入和调用栈；没有设备访问或构建入口。

## f1fefb2e 开机等待20s重测

`run_v3e.py` 仍绑定同一获批SHA，刷前必须为原厂app；每次写入或只读退出DFU均静默20s，再打开SDK。
`sessions` 合并20次控制、12s超时和30s保活，仅最后导出一次 `after_controls`，兼作三轮注入的基线。
三轮由原 `knob_auto.py` 分别运行seed 1/2/3，不改脚本、轮次间不进DFU。
`knob_audit` 逐记录允许key0F/10/11，key11只记录不触发恢复；其他键/地址/格式以及无法还原的擦页回收会拒绝完整审计。
硬件运行器用firmware虚拟环境，注入脚本按任务用SDK虚拟环境；`report_v3e.py` 只读检查三次静默窗口、三轮帧序号与配置记录并追加报告。
