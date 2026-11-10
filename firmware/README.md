# Neos75 firmware — offline build

源规格为 `../docs/DESIGN.md` 和 `../docs/PROTOCOL.md`；本机地址、限制及上机顺序见
`../docs/FIRMWARE.md`。这两个镜像均为未上机验证的候选补丁，文件名 release 仅表示试运行标志策略。

在仓库根目录使用 PowerShell：

```powershell
New-Item -ItemType Directory -Force firmware/.tmp | Out-Null
$env:TEMP = (Resolve-Path firmware/.tmp).Path
$env:TMP = $env:TEMP
python -m venv firmware/.venv
firmware/.venv/Scripts/python -m pip --isolated install --index-url https://pypi.org/simple --no-cache-dir -r firmware/requirements.txt
firmware/.venv/Scripts/python -m pip --isolated install --index-url https://pypi.org/simple --no-cache-dir -r tools/requirements-flash.txt
```

编译器使用官方 Arm GNU Toolchain **14.3.Rel1 / GCC 14.3.1 20250623** Windows 便携版：

<https://developer.arm.com/-/media/Files/downloads/gnu/14.3.rel1/binrel/arm-gnu-toolchain-14.3.rel1-mingw-w64-x86_64-arm-none-eabi.zip>

将包内容解压至 `firmware/toolchain/`，使 `firmware/toolchain/bin/arm-none-eabi-gcc.exe` 存在。
本次下载的 ZIP SHA-256：`864c0c8815857d68a1bbba2e5e2782255bb922845c71c97636004a3d74f60986`。
该目录、虚拟环境、临时目录及所有生成的固件均已忽略；没有安装系统软件。

```powershell
firmware/.venv/Scripts/python -B firmware/make_patch.py
firmware/.venv/Scripts/python -B -m pytest -c firmware/pytest.ini firmware/tests -q
firmware/.venv/Scripts/python -B tools/flash.py --image firmware/build/neos75_rt_dev.bin --dry-run
firmware/.venv/Scripts/python -B tools/flash.py --restore --dry-run
```

原厂固件备份所在的文件夹：环境变量 `NEOS75_BACKUP`，没设则是 `~/Tools/aula_neos75_backup`
（`tools/flash.py` 和 `tools/dfu_restore.py` 也可用 `--backup <文件夹>` 指定）。
换一把键盘要用它自己的备份：`hwtest` 刷前预检会把键盘上的各区和备份逐字节比对。
生成器只读取指定 SHA-256 的备份。`build/neos75_rt_{dev,release}.json` 记录所有钩子和符号；
`.diff.tsv` 是相对备份的逐字节差异（含 app 偏移和绝对 VA），`.sha256` 是镜像摘要。
输出保留原内容及 DFU suffix，新增代码从 `0x4AB00` 开始，不重定位原代码。
输出在新增代码末尾截断；备份剩余的 FF 尾区未包含在 bin 中，也不在此 bin 的擦写范围内。
`firmware/write_build_tables.py` 可根据最终 build 元数据刷新报告中的钩子、符号与 Runtime 字段表。

Unicorn 执行最终 ELF 对应的 Thumb 机器码。纯逻辑完整模拟；GUI、JPEG 硬件启动和 USB 发送用
显式桩替代，不能据此声称硬件显示已成功。Windows 版 Unicorn 在保留模拟 RAM 时使用已处理的
原生异常；pytest 的 faulthandler 插件会把这些异常打印为 fatal，因此配置禁用了这个插件。
Unicorn 的执行异常、指令预算、状态、字节和栈平衡断言仍全部生效。

测试几乎全是 CPU 密集的 Unicorn 模拟，所以 `pytest.ini` 用 pytest-xdist（在 `requirements.txt` 里）
以 `-n auto --dist worksteal` 把测试分到所有核心；上面那条命令就是并行的。
在 i9-13900HX 上，单进程约 820 s，并行约 140 s。剩下的时间主要花在最长的几个旋钮测试上
（单独跑每个约 50 s，全核满载时约 90 s）。调试单个测试时加 `-n 0` 可在一个进程里跑。

`tools/flash.py` 的非 dry-run 路径要求设备已经处于 DFU，且只打开 `5542:1000` 的接口 0。
它不会调用 HID 进入 DFU。导出不会退出 DFU；写入只有完整回读比较成功才发退出命令。
本阶段只运行以上 dry-run 命令和假后端测试。
