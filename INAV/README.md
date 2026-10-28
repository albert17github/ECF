# ECFH743 INAV 9.1.0 · ECF r5

## ECF r5 · 2026-10-28

修订内容：来源清单与完整源码快照同步，包含共享接口头文件；运行源码保持不变。重编译镜像与上一发行修订逐字节一致。

当前目标具备普通 DShot150／300／600 输出和独立串口 ESC 遥测；没有双向 DShot 接收实现。CH1～4、CH5～6、CH7～8 分别共用 TIM2、TIM3、TIM4，同组时基与协议需兼容。

适用配置：STM32H743VIH6、8MHz HSE、ICM-45686、BMP581、MMC5983MA、SDMMC2 四位存储。上游固定基线为 `9.1.0`／`e519b69b02e27c8bdc03b4a0889f1baaae211a54`。源码、固定依赖、构建入口和第三方许可随工程提供；当前验收状态见 `verification/acceptance.json`。

## 固件与构建

`firmware/` 保存对应出厂 HEX、SHA-256 与调试 ELF。HEX 起始地址为 `0x08000000`，无独立引导程序。出厂镜像清除 `0x08020000–0x08040000` 参数区；烧录前备份配置，使用 STM32CubeProgrammer 经 ST-Link 或 ROM USB DFU 烧录、校验并复位。SD 日志不在 Flash 擦除范围。

构建环境：Windows／PowerShell 7、ARM GCC 13.3.1、CMake 3.31.10、Ruby 3.3.7、Ninja、Python。工具链及构建工具通过 PATH 或构建入口参数指定。将工程解压到较短本地路径，例如 `D:/ECF/INAV/`，避免 Windows 工具的路径长度限制。

```powershell
.\build.ps1 -Python python
```

构建前校验源码清单；无需 Git 元数据或相邻固件工程。新结果输出到 `.build/`，开发修改可使用 `-AllowModifiedSource`。编译缓存、工具链安装包和原始台架记录不属于交付内容。

## 出厂接口配置

板边编号按 USB-C 朝上的正面视图：左侧 1～20，右侧 21～40。通用镜像不包含单台设备的校准值、无线绑定或个人设置。

- 15／16：ADC 模拟电压／电流，PC5／PC4；UART8 关闭。
- 25／26：UART3，保留串口资源，不分配外设协议；共享 I²C4 关闭。
- 23／24：I²C3，连接受支持的外置罗盘。
- UART1：ELRS 接收机，CRSF 双线串口，非反相。
- UART6：GPS1；UART4：不分配用途，本基线只支持一个 GPS。
- UART5：MSP 双向配置／数据链，115200bit/s。
- UART7：原生 ESCSERIAL 电调遥测输入，115200bit/s；原生驱动在启用 PWM 输出且混控包含电机时启动。首次配置保留原生输出启用流程。此接口与通过电机信号线返回数据的双向 DShot 分别配置。
- 外接 SPI4 默认不启用外部传感器；板载 SPI1 IMU 正常工作。
- CAN：CAN1 默认启用 DroneCAN，节点 10、1Mbit/s；CAN2 可通过 `dronecan_device=2` 切换，当前协议栈一次使用一路控制器。CANH／CANL 由底板收发器提供，核心板引脚为 3.3V 逻辑 TX／RX。

GPS1 默认 UBX、自动波特率，当前基线不解析 NMEA。I²C3 优先探测 HMC5883／QMC5883／QMC5883P／IST8310／LIS3MDL 外置罗盘，未发现时回退板载 MMC5983MA；当前目标给这些外置驱动配置的地址分别为 0x1E／0x0D／0x2C／0x0E／0x1E，其他地址须调整板级描述。USB 与 UART5 使用 MSP。

接口迁移预设为 `presets/default-interfaces.cli`，只修改所列接口与采样参数；安装校准单独保留。新出厂默认以完整出厂镜像和原生恢复默认流程为准。

## 功能复用与电源监测

出厂组合统一为 **ADC 电压／电流＋UART3**。15／AI1／PC5 与 UART8_TX／PE1 并接，16／AI2／PC4 与 UART8_RX／PE0 并接；25／PD8、26／PD9 为 UART3，与 I²C4 的 PD12／PD13 共用焊盘。

模拟参考为 **11∶1 分压、20mV/A、零偏 0**，对应 100kΩ／10kΩ 电压分压及 50A/V 电流换算。参数为 `vbat_scale=1100`、`current_meter_scale=200`、`current_meter_offset=0`，电压／电流通道分别为 ADC 1／2。ADC 输入须处于 0～3.3V，电池电压必须先经过底板分压。换算范围不代表底板持续电流额定值。

`ecf_io_mode=0/1/2/3` 分别选择 ADC＋I²C4、ADC＋UART3、UART8＋I²C4、UART8＋UART3，默认 1。模式与串口协议分别配置，UART3 空闲时仍保留串口侧路由。`presets/shared-*.cli` 提供四种组合，`defaults.cli` 恢复完整出厂配置。原生最多同时使用三个 MSP 端口，USB 与默认 UART5 已占两个；要同时在 UART3／UART8 使用 MSP，须先停用额外 MSP 端口。

I²C4 为可选数字电源监测总线，启用后占用 25／26，须停用同组 UART3。本固件没有原生 INA238／INA226 电池监测后端，开启 I²C4 资源不会自动产生数字电源读数。 INA238 的参考为 7 位地址 0x45、0.5mΩ 分流器、120A 换算量程，实际装配地址与电阻必须匹配。

切换需要保存并正常重启，重启后读回模式与采样来源。未选 MCU 引脚保持模拟高阻、无内部上下拉；外部电路也须互斥连接。15／16 不能同时接模拟前端和 UART8，25／26 不能同时接 I²C4 模块和 UART3 外设。接口为 3.3V 逻辑，核心 VIN 仅接外部稳压 5V。

更换底板后先核对实际分压、灵敏度和零点，再用万用表校准电压，用零电流和已知负载进行电流至少两点标定。外置罗盘须确认实际型号、地址、安装方向并重新校准；自动探测不替代装机验收。

## 其他接口与验证

CH1～8 对应 PA0、PA1、PA2、PA3、PB1、PB0、PD14、PD15。默认 Quad X、前四路 DShot300、后四路预留，TIM1 保留给 IMU 时钟。各固件的电机位置与旋向按原生混控核对。PB7／TIM17_CH1N 驱动一颗状态灯；SPI4 支持 PE3 片选、PE4 中断的第二 IMU，默认使用板载 IMU。板载传感器使用 I²C2，扩展 I²C3 为 PA8／PC9。

通用参数不含逐板加速度计、磁力计或整机校准。台架验证仅连接 USB 和 ST-Link，未接执行器；安装测试依次检查接口、校准、输出顺序和实际外设。外部互通、电气波形、采样精度、负载及飞行分别验收。

```powershell
python -m pip install -r tools/requirements.txt
.\verify.ps1 -Port COMx -Uid '<24位MCU UID>' -StlinkSerial '<调试器序列号>' -UnloadedBench
```

验证环境为 Windows Python 3.11、STM32CubeProgrammer 2.21，以及已安装的 WSL Linux x86_64／Python 3；官方日志解析器随工程提供。多发行版环境可通过 `-WslDistribution` 指定已安装发行版。

自动验证保存当前配置，检查资源互斥、保存重启、输出、传感器和日志，并恢复出厂默认。端口号以系统枚举为准。许可证为 GPL-3.0-or-later；上游与第三方声明随源码保留。

首次在配置器中核对机型与 Mixer，并在 Outputs 页面显式启用所需输出、完成校准。固件包含 Quad X 基础混控，但配置器的首次机型向导及输出关闭提示仍保留。Save and Reboot 后自动重连若超时，再点击 Connect；当前 9.1.1 配置器已验证此恢复方式。

跨 AFatFS／FatFS 固件交接时，`tools/msc_roundtrip.py --release-afatfs-pool` 完整备份后将分配池保留为一个簇；返回本固件可用 `--rebuild-afatfs-pool` 让原生分配器重建空间池。两者均核对 UID、其他文件 SHA，并安全弹出／NRST，不格式化或删除旧日志。使用时同时提供端口、UID、调试器序号及新的输出记录路径。灯色复核入口为 `tools/verify_rgb.py`，测试后恢复原有 LED 设置。

状态灯：开机以固件专用颜色短闪三次，运行状态和闪烁节奏见 [状态灯说明](LED-STATUS.md)。
