# ECFH743 PX4 v1.17.0 · ECF r3

调试 ELF 随同版本完整源码包提供，位于包内 `firmware/debug/`。

## ECF r3 · 2026-10-28

修订内容：接口清单统一为 `ECF_IO_MODE=1`、ADC＋UART3，UART8／共享 I²C4 停用。DShot 输出修正 ARR 的包含末值计数，并采用就近整数预分频。该修正只对 ECF 板级目标启用。

在本目标 200MHz 定时器时钟下，DShot150／300／600 的理论实际位率分别为约 149.254／303.030／588.235kbit/s；整数分频误差分别约 −0.50%／+1.01%／−1.96%。精简验证记录列明实际波形检查范围。双向 DShot 保持原生仅 TIM2／CH1～4 的限制。

主机回归入口：在有 GCC 的 Linux 环境执行 `python3 tools/test_dshot_timing.py --out /tmp/ecf-px4-dshot-test`，输出目录须为新目录。测试覆盖三个定时器组、三档速率、寄存器计数和非法输入；不代替电调互通验收。

官方基线为 `v1.17.0`／`d6f12ad1c4f70ad3230afd7d86e971421e02fef4`，目标 `ecf_core-h743_default`。适用硬件为 STM32H743VIH6、8MHz HSE、ICM-45686、BMP581、MMC5983MA、SDMMC2 四位存储与 PB7 WS2812 状态灯的 ECFH743 核心板。构建、板级验收及发行状态见 `release.json` 与 `verification/`。

## 接口与安装

CH1～8 依次为 PA0、PA1、PA2、PA3、PB1、PB0、PD14、PD15。默认 Quad X、前四路 DShot300，后四路不分配执行器功能。TIM1 预留给 IMU 参考时钟，默认不输出。核心 VIN 使用外部稳压 5V，CAN 收发器与终端由底板提供。

板载 ICM-45686 使用 SPI1，前方为传感器原生 +X；原生 PX4 驱动完成坐标转换，启动旋转为 0。MMC5983MA 使用原生驱动旋转 4。整板安装方向通过原生安装参数设置，并逐板完成传感器校准。SPI4 可接第二颗 ICM-45686：PE2／PE5／PE6 为时钟／MISO／MOSI，PE3 为片选、PE4 为中断；`ECF_IMU2_EN=1` 启用，`ECF_IMU2_ROT` 设置安装旋转，保存重启后生效。通用镜像不包含单台设备校准值。

硬件串口保留七组映射：UART1＝TEL1／`/dev/ttyS0`，UART3＝TEL2／`/dev/ttyS2`，UART4＝TEL3／`/dev/ttyS3`，UART5＝TEL4／`/dev/ttyS4`，UART6＝GPS1／`/dev/ttyS5`，UART7＝GPS2／`/dev/ttyS6`，UART8＝RC／`/dev/ttyS7`。USART2 未引出，因此节点编号保留空位。硬件串口按下述出厂用途启用；USB MAVLink 自动连接独立于硬件串口配置。

原生 DroneCAN 使用两路 FDCAN 接口，按原生 `UAVCAN_ENABLE` 与 `UAVCAN_BITRATE` 配置，保存重启。两路属于同一原生 DroneCAN 节点的接口，共用比特率；不能据此当作两个独立协议节点。

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
- CAN：两路接口默认启动同一个 DroneCAN 节点 10、1Mbit/s，`UAVCAN_ENABLE=2`，共用比特率。CANH／CANL 由底板收发器提供，核心板引脚为 3.3V 逻辑 TX／RX。

地面站串口标签保留原生映射：UART1＝TEL1、UART3＝TEL2、UART4＝TEL3、UART5＝TEL4、UART6＝GPS1、UART7＝GPS2、UART8＝RC。实际用途由配置分配：`RC_PORT_CONFIG=101`、`RC_INPUT_PROTO=6`；`GPS_1_CONFIG=201`、`GPS_2_CONFIG=103`；`MAV_0_CONFIG=104`；`DSHOT_TEL_CFG=202`。GPS2 接收机应接物理 UART4。I²C3 使用原生外部探测，已包含 HMC5883、QMC5883L／P、IST8310、LIS3MDL、RM3100 驱动。

接口迁移预设为 `presets/default-interfaces.params`，只修改所列接口与采样参数；安装校准单独保留。新出厂默认以完整出厂镜像和原生恢复默认流程为准。

## 功能复用与电源监测

出厂组合统一为 **ADC 电压／电流＋UART3**。15／AI1／PC5 与 UART8_TX／PE1 并接，16／AI2／PC4 与 UART8_RX／PE0 并接；25／PD8、26／PD9 为 UART3，与 I²C4 的 PD12／PD13 共用焊盘。

模拟参考为 **11∶1 分压、20mV/A、零偏 0**，对应 100kΩ／10kΩ 电压分压及 50A/V 电流换算。参数为 `ECF_IO_MODE=1`、`SENS_EN_INA238=0`、`BAT1_V_CHANNEL=8`、`BAT1_I_CHANNEL=4`、`BAT1_V_DIV=11`、`BAT1_A_PER_V=50`、`BAT_V_OFFS_CURR=0`。ADC 输入须处于 0～3.3V，电池电压必须先经过底板分压。换算范围不代表底板持续电流额定值。

`ECF_IO_MODE=0/1/2/3` 分别选择 ADC＋I²C4、ADC＋UART3、UART8＋I²C4、UART8＋UART3，默认 1。四种预设位于 `presets/shared-*.params`；`default-power.params` 恢复出厂电源组合。`ecf_interfaces status` 显示本次启动已锁定的路由。

I²C4 为可选数字电源监测总线，启用后占用 25／26，须停用同组 UART3。切到模式 0 或 2并设置 `SENS_EN_INA238=1`，再应用对应数字监测预设。当前镜像只包含 INA238 电源驱动，未包含 INA226。切回模拟监测需关闭 INA238 驱动并恢复 ADC 参数。 INA238 的参考为 7 位地址 0x45、0.5mΩ 分流器、120A 换算量程，实际装配地址与电阻必须匹配。

切换需要保存并正常重启，重启后读回模式与采样来源。未选 MCU 引脚保持模拟高阻、无内部上下拉；外部电路也须互斥连接。15／16 不能同时接模拟前端和 UART8，25／26 不能同时接 I²C4 模块和 UART3 外设。接口为 3.3V 逻辑，核心 VIN 仅接外部稳压 5V。

更换底板后先核对实际分压、灵敏度和零点，再用万用表校准电压，用零电流和已知负载进行电流至少两点标定。外置罗盘须确认实际型号、地址、安装方向并重新校准；自动探测不替代装机验收。

## 构建

声明环境为 Ubuntu 24.04 x86_64／WSL、Python 3.12、CMake 3.28.3、Ninja 1.11.1、GNU Make 与宿主 GCC。交叉编译器使用 Ubuntu 包 `gcc-arm-none-eabi=15:13.2.rel1-2`（GCC 13.2.1），binutils 包为 `binutils-arm-none-eabi=2.42-1ubuntu1+23`，并需匹配的 newlib／ARM C++ 运行库。编译器文件 SHA 记录在 `release.json`。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/requirements-build.txt
ECF_PYTHON="$PWD/.venv/bin/python" bash build.sh --arm-bin /usr/bin --jobs 8
```

Windows 可使用 PowerShell 7 的 `build.ps1 -WslDistribution 已安装发行版 -LinuxPython /该发行版内虚拟环境/bin/python`。源码与固定依赖随包提供，不需要 Git 或相邻固件工程。`verification/source-layout.json` 保存原生相对链接及可执行文件属性；跨平台源码包用普通文本保存链接目标，构建入口在新的 Linux 临时目录中恢复链接与权限。符号路径统一为 `/ecf/source` 和 `/ecf/build`。

入口严格校验完整源码清单；开发修改可使用 `--allow-modified-source`。产物写入 `.build/`，最新目录记录于 `.build/LATEST.txt`；出厂 HEX 与调试 ELF 由同一编译结果生成。

## 烧录与恢复

出厂 HEX 从 `0x08000000` 启动，覆盖完整 2MiB Flash，不含独立 Bootloader。使用 STM32CubeProgrammer，经 ROM USB DFU 或已接 NRST 的 ST-Link under-reset 连接按 HEX 地址下载、校验并复位。无需修改 Option Bytes；从 ROM 返回应用使用 NRST。

主参数保存于 SD 的 `/ecf-px4/parameters.bson`，原生备份位于 `/parameters_backup.bson`，随参数保存更新；**刷写 Flash 不清除 SD 参数**。首次安装或恢复默认前备份参数与日志，在原生控制台执行 `param reset_all`、`param save`、`reboot`，随后核对默认接口与电源配置。不能通过格式化存储卷来代替参数恢复或文件系统交接。当前刷写包提供完整 HEX，未提供独立引导程序升级容器。

跨 AFatFS 与 FatFS 固件切换前，应备份并逐文件校验 SD。AFatFS 的 `FREESPAC.E` 为可变分配池；进入 PX4 前须释放多余分配空间并保留其他文件，回到 BF／INAV 时让其原生分配器重新建立空间池。保留全部历史日志。

本目标使用 `1209:0001` 作为本地原型测试 USB 标识，未声明正式分配。运行后串口号由系统枚举，重新连接以当前设备为准。

## 自动验证

验证入口使用 Windows PowerShell 7、Windows Python 3.11、`tools/requirements-test.txt` 的固定依赖、STM32CubeProgrammer 2.21 和已接 SWD／NRST 的 ST-Link。保持 USB／调试器台架、未解锁，断开接收机、ESC、舵机及其他执行器。

```powershell
python -m pip install -r tools/requirements-test.txt
./verify.ps1 -Uid '<12字节MCU UID十六进制>' -StlinkSerial '<ST-Link序号>' -UnloadedBench
```

验证输出保存在 `.build/verification/`。实际通过项目与量化结果以 `verification/` 的精简验收记录为准。板级软件、状态灯实物和地面站界面分别记录；外部串口／CAN／电源监测互通、物理输出波形、负载、逐板校准和飞行分别验收。
`DSHOT_BIDIR_EN` 沿用本基线原生的单定时器限制：仅 TIM2／CH1～4 可用双向 DShot，后四路可配置 PWM。八路普通 DShot 与八路 PWM 分别配置；不能将八路普通 DShot 的可用性当作八路双向反馈支持。
串口接收机驱动随工程编译，出厂 `RC_INPUT_PROTO=6`，按实际设备选择协议和 `RC_PORT_CONFIG`。串口枚举值为 TEL1～TEL4＝101～104、GPS1／GPS2＝201／202、RC＝300；UART8 对应 RC。GPS 与 RC 端口的波特率原生默认 AUTO，改用于 MAVLink 时须将相应 `SER_GPS1_BAUD`、`SER_GPS2_BAUD` 或 `SER_RC_BAUD` 设置为外设的固定速率。

原始 FIFO 高速日志使用 256KiB 缓冲及陀螺仪事件触发：`logger start -m file -f -b 256 -p sensor_gyro_fifo`，停止使用 `logger stop`。对应原生配置为 `SDLOG_PROFILE=833`；验证完成后恢复原有日志参数。加速度与陀螺仪 FIFO 均为四条队列，保留记录任务调度期间的原始批次。出厂 `SDLOG_MODE=-1`，按安装需求选择自动记录策略。

实物灯色复核入口为 `tools/verify_rgb.py`，临时覆盖结束后恢复原生显示。源码包的独立解压构建与最终 HEX／ELF 比对结果随发布附件提供，外置工具链不放入工程目录。

QGroundControl 5.1.0 连接本地原型 USB 时，须在 Settings → Comm Links 建立串口连接并选择系统当前枚举的端口；不要固定假定某个 COM 号。硬件串口的 `SER_*_BAUD` 参数在分配相应用途并重启后才进入原生使用参数列表。七组端口可在 `MAV_0_CONFIG` 的选择列表中查看。启用 UART1 的 MAVLink 用途并重启后，曾出现地面站手动 USB 重连及重启软件仍未显示设备；原生自动验证工具可继续读取心跳和保存值。该地面站组合的重连兼容性尚未确认，不能视为自动重连通过。

状态灯：开机以固件专用颜色短闪三次，运行状态和闪烁节奏见 [状态灯说明](LED-STATUS.md)。
