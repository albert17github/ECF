# ECFH743 ArduCopter 4.7.2 · ECF r4

调试 ELF 随同版本完整源码包提供，位于包内 `firmware/debug/`。

## ECF r4 · 2026-10-28

修订内容：更新开源交付的验证索引和接口说明。运行源码、引导程序及出厂配置保持不变；完整重建产物与上一发行修订逐字节一致。

CH1～4、CH5～6、CH7～8 分别共用 TIM2、TIM3、TIM4，同组输出频率和协议需兼容。源代码中的固定依赖、DSP 对应源码和第三方许可随工程提供。

官方基线为 `Copter-4.7.2`／`fb773567896014da007bca58358704d4a0d8516f`。适用硬件为 ECF H743 核心模块：STM32H743VIH6、8MHz HSE、ICM-45686、BMP581、MMC5983MA、SDMMC2 四位存储和 PB7／TIM17_CH1N 状态灯。本修订的板级软件、界面与独立重建结果见对应验收记录；发行状态以 `release.json` 和 `verification/` 为准。

## 接口与安装

CH1～8 依次为 PA0、PA1、PA2、PA3、PB1、PB0、PD14、PD15。默认 Quad X、前四路 DShot300，后四路未分配功能；第九路为板载状态灯。TIM1 预留给 IMU 参考时钟，默认不输出。USB 为 SERIAL0，UART1／3／4／5／6／7／8 保留对应 SERIALn 编号，SERIAL2 为空位。硬件串口使用下述出厂用途；其他协议与端口可在地面站修改。

SPI4 可接第二颗 ICM-45686，PE3 为片选、PE4 为中断；`INS_ENABLE_MASK=1` 只启用板载 IMU，3 允许探测第二颗。默认前方为板载 IMU 原生 +X，板载 IMU、磁力计安装补偿为 `ROTATION_ROLL_180`；整板安装方向使用 `AHRS_ORIENTATION`。每台设备须完成实际安装方向确认及原生传感器校准，通用镜像不带单台设备校准值。

VIN 接外部稳压 5V。CAN1／2 为逻辑接口，收发器、终端和外部保护由底板提供。

## 出厂接口配置

板边编号按 USB-C 朝上的正面视图：左侧 1～20，右侧 21～40。通用镜像不包含单台设备的校准值、无线绑定或个人设置。

- 15／16：ADC 模拟电压／电流，PC5／PC4；UART8 关闭。
- 25／26：UART3，保留串口资源，不分配外设协议；共享 I²C4 关闭。
- 23／24：I²C3，连接受支持的外置罗盘。
- UART1：ELRS 接收机，CRSF 双线串口，非反相。
- UART6：GPS1；UART4：GPS2，自动探测受支持协议。
- UART5：MAVLink2 数据链，115200bit/s。
- UART7：独立串口电调遥测输入，115200bit/s。此接口与通过电机信号线返回数据的双向 DShot 分别配置。
- 外接 SPI4 默认不启用外部传感器；板载 SPI1 IMU 正常工作。
- CAN：CAN1／CAN2 默认分别启用 DroneCAN 驱动 1／2，节点 10、1Mbit/s。CANH／CANL 由底板收发器提供，核心板引脚为 3.3V 逻辑 TX／RX。

SERIALn 保持物理 UARTn 编号。GPS 查找次序按 ECF 板级定义优先 UART6、再 UART4，确保 GPS1／GPS2 对应这两个连接器。ELRS 使用 `SERIAL1_PROTOCOL=23`、`RC_PROTOCOLS=512`、`RC_OPTIONS=8192`。UART7 使用 `SERIAL7_PROTOCOL=16`。外置 I²C3 罗盘按原生驱动探测，板载罗盘保留。

接口迁移预设为 `presets/default-interfaces.parm`，只修改所列接口与采样参数；安装校准单独保留。新出厂默认以完整出厂镜像和原生恢复默认流程为准。

## 功能复用与电源监测

出厂组合统一为 **ADC 电压／电流＋UART3**。15／AI1／PC5 与 UART8_TX／PE1 并接，16／AI2／PC4 与 UART8_RX／PE0 并接；25／PD8、26／PD9 为 UART3，与 I²C4 的 PD12／PD13 共用焊盘。

模拟参考为 **11∶1 分压、20mV/A、零偏 0**，对应 100kΩ／10kΩ 电压分压及 50A/V 电流换算。参数为 `BATT_MONITOR=4`、`BATT_VOLT_PIN=8`、`BATT_CURR_PIN=4`、`BATT_VOLT_MULT=11`、`BATT_AMP_PERVLT=50`、`BATT_AMP_OFFSET=0`。ADC 输入须处于 0～3.3V，电池电压必须先经过底板分压。换算范围不代表底板持续电流额定值。

`BRD_ALT_CONFIG=0/1/2/3` 分别选择 ADC＋I²C4、ADC＋UART3、UART8＋I²C4、UART8＋UART3，默认 1。四种预设位于 `presets/shared-*.parm`；`default-power.parm` 恢复出厂电源组合。

I²C4 为可选数字电源监测总线，启用后占用 25／26，须停用同组 UART3。切到模式 0 或 2，设置 `BATT_MONITOR=21` 并重启，再按 `presets/power-ina238.parm` 配置 INA2xx 后端。`BATT_I2C_BUS=2` 对应物理 I²C4。切回模拟监测设置 `BATT_MONITOR=4` 并重启，再应用模拟标定参数。 INA238 的参考为 7 位地址 0x45、0.5mΩ 分流器、120A 换算量程，实际装配地址与电阻必须匹配。

切换需要保存并正常重启，重启后读回模式与采样来源。未选 MCU 引脚保持模拟高阻、无内部上下拉；外部电路也须互斥连接。15／16 不能同时接模拟前端和 UART8，25／26 不能同时接 I²C4 模块和 UART3 外设。接口为 3.3V 逻辑，核心 VIN 仅接外部稳压 5V。

更换底板后先核对实际分压、灵敏度和零点，再用万用表校准电压，用零电流和已知负载进行电流至少两点标定。外置罗盘须确认实际型号、地址、安装方向并重新校准；自动探测不替代装机验收。

## 构建

入口 `build.sh` 编译应用与匹配引导程序，生成出厂 HEX、原生 APJ、调试 ELF 和校验清单。声明环境为 Linux／WSL x86_64、ARM GCC 10.2.1（10-2020-q4-major）、Python 3.12.3、GNU Make 和宿主 GCC；Waf 与固定源码依赖随工程提供。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/requirements-build.txt
ECF_PYTHON="$PWD/.venv/bin/python" bash build.sh --arm-bin /path/to/gcc-arm-none-eabi-10-2020-q4-major/bin
```

工具链为 `gcc-arm-none-eabi-10-2020-q4-major-x86_64-linux.tar.bz2`，SHA-256 为 `21134caa478bbf5352e239fbc6e2da3038f8d2207e089efc96c3b55f1edcd618`，来源为 [ArduPilot STM32 工具目录](https://firmware.ardupilot.org/Tools/STM32-tools/)。也可通过 `ECF_ARM_GCC_BIN` 指定工具目录。源码必须与清单一致；开发修改使用 `--allow-modified-source`。路径可含空格，内部 ChibiOS 构建使用新的无空格临时目录。产物位于 `.build/`，最新位置记录于 `.build/LATEST.txt`。

编译保持原生优化和解锁检查，使用 `--debug-symbols` 提供符号，未启用调试构建。符号路径统一为 `/ecf/source`，GDB 可使用 `set substitute-path /ecf/source /实际工程路径/source`。DSP 库与 ARM CMSIS 5.6.0 LFS 对象一致，对应 DSP、Core 源码、构建材料及许可位于 `tools/third-party-sources/`。

## 烧录与恢复

首次安装使用出厂 HEX，含匹配引导程序：引导程序 0x08000000、应用 0x08020000，参数区为 0x081C0000～0x08200000。HEX 自带地址，通过 ST-Link／STM32CubeProgrammer 校验下载并硬件复位；出厂镜像覆盖完整 2MiB 并清除参数，不格式化 SD。烧录前保存配置和日志。板型 ID 65001 为本地实验编号，尚未获上游正式分配。

已安装匹配引导程序后，升级使用对应 `.apj`，按原生引导程序流程加载应用，保留引导程序及参数区；升级后核对板型、版本与配置。要加载本修订的完整出厂默认配置，备份后重新刷出厂 HEX。不能把 APJ 当作 BIN 或直接写入 0x08000000。

应用的原生重启命令可进入匹配引导程序，或进入 STM32 ROM DFU。ROM DFU 恢复应通过匹配 UID 的 STM32CubeProgrammer 选择正确设备；回到应用使用 NRST。SWD 恢复使用 ST-Link 与已接 NRST 的 under-reset 连接。正常 `BRD_OPTIONS=1` 保留看门狗并关闭运行期 SWD；临时台架回读使用 9，验证结束恢复 1。无需修改 Option Bytes。

板载存储卷约 478.5MiB，`LOG_FILE_MB_FREE=16`；空间不足时使用原生旧日志清理策略。跨固件刷写前备份并核对现有文件，AFatFS 的 FREESPAC.E 分配池属于可变空间管理文件，需要按目标文件系统交接；不得用格式化或删除历史日志代替交接。

## 自动验证

入口为 Windows PowerShell 的 `verify.ps1`。环境为 Windows Python 3.11、`tools/requirements-test.txt` 中固定依赖及 STM32CubeProgrammer 2.21；ST-Link 须连接 SWD 和 NRST。先断开接收机、ESC、舵机及其他执行器，保持未解锁、无桨台架。程序按 UID／USB 身份自动查找串口并在重启后重新识别。

```powershell
python -m pip install -r tools/requirements-test.txt
./verify.ps1 -Uid '<12字节MCU UID十六进制>' -StlinkSerial '<ST-Link序号>' -UnloadedBench
```

可用 `-Python`、`-Cube` 指定已安装运行环境；`-FlashFactoryImage` 先备份全片再按 UID 和 SHA 校验刷入出厂 HEX，清除参数。验证要求本修订出厂控制配置；自行配置的设备应先备份，按烧录规则恢复默认后验证。

自动项目包括完整源码清单、非参数区镜像回读、全部原生串口配置、四种共享模式与两种电池后端、九组 CAN 配置、四组输出模式及原生双向 DShot 捕获状态、三轮独立文件写读、原生 DataFlash 和缺失完整数据包的负向校验、三次普通重启、三轮引导程序 CRC 与 ROM DFU／NRST 恢复、120 秒传感器采样。旧 SD 载荷前后核对 SHA；原生参数备份和 LASTLOG.TXT 另行记录。原始数据写入 `.build/verification/`，不进入发行包。

台架软件验证不等同于物理输出波形、外部串口／CAN／ESC／电源监测设备互通、整机校准或飞行验收。状态灯实物颜色与地面站界面在独立验收记录中标明。

地面站重连若提示参数列表不完整，可在 Parameters → 工具 → 刷新重新获取参数；QGroundControl 5.1.0 台架检查已验证此恢复方式。状态灯颜色复核入口为 `tools/verify_arducopter_rgb.py`，每次临时覆盖后自动恢复正常显示。

状态灯：保留原生通知灯语，初始化为红／蓝交替；运行状态见 [状态灯说明](LED-STATUS.md)。
