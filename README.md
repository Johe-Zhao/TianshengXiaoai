# 天声小爱 TianshengXiaoai

把小米小爱音箱变成你的 AI 语音助手入口。说一句「天声小爱 XXX」，回答由电脑上的 AI（CodeBuddy / 任意 OpenAI 兼容后端）生成，从小爱音箱播出来；同时**不破坏小爱原有的家电控制能力**。

> English: Turn your Xiaomi XiaoAI speaker into an AI voice assistant entrance. Wake phrases are intercepted and answered by a local AI backend (CodeBuddy CLI or any OpenAI-compatible endpoint), spoken through the speaker via TTS — while all native XiaoAI features (IoT control, music) keep working untouched.

## 功能特性

- **零破坏接入**：只拦截包含唤醒词的指令，其余语音指令全部放行给小爱原生处理
- **桌面控制台**：pywebview 原生窗口，账号登录状态实时可视（小米扫码 / CodeBuddy 授权，均可弹窗登录、一键退出）
- **真托盘常驻**：点 X 最小化到托盘，托盘右键才是真退出，含系统关机检测
- **语音播报**：edge-tts 11 种音色（晓晓/云希/方言等）或小爱原声，流式逐句播报，支持打断
- **小米盒子支持**：ADB 桥接盒子，遥控器语音键直接唤醒，回答统一从音箱播出
- **开箱即用打包**：`pack_portable.py` 一键打包绿色便携版（自动脱敏配置），拷到新电脑解压即用
- **凭据守护**：拦截误触发的密码登录（避免小米风控短信骚扰），登录态掉线不丢凭据

## 架构

```
你："小爱同学" → 小爱回复"在呢" → "天声小爱，今天天气怎么样？"
   │
小米云 ASR（原厂链路）
   │
xiaogpt（监听云端对话记录，拦截含唤醒词的指令）
   │
gateway.py（:8008，OpenAI 兼容 API 门面）
   │
AI 后端（CodeBuddy CLI 无窗口运行 / 任意 OpenAI 兼容服务）
   │
回答（≤80 字口语化）→ edge-tts 合成 → 音箱逐句播报
```

## 目录结构

```
├── tiansheng-app/     桌面控制台（pywebview + Flask + pystray）
│   ├── app.py         主程序：窗口/托盘/生命周期/各服务启停
│   ├── core/          托盘、小米扫码登录、AI 后端登录弹窗、进程监督
│   └── web/           控制台前端（原生 HTML/JS）
├── xiaoai-bridge/     语音桥接后端
│   ├── run_xiaogpt.py xiaogpt 启动器（SSL 补丁 + 设备自动发现 + 凭据守护）
│   ├── gateway.py     OpenAI 兼容网关（对接 CodeBuddy 内置 REST API）
│   ├── codebuddy_hidden.py  无窗口运行 CodeBuddy CLI（winpty）
│   ├── mi_guard.py    小米凭据守护（拦截危险登录路径）
│   ├── mijia.py       米家设备发现/状态/控制
│   ├── box_bridge.py  小米盒子 ADB 语音桥接
│   └── history.py     语音对话记录页面生成
├── patches/           对 xiaogpt 3.24 的增强补丁（含自动应用脚本）
├── make_icon.py       用代码重绘全套图标（PNG/ICO，无需素材文件）
├── pack_portable.py   一键打包 Windows 便携版
└── requirements.txt
```

## 环境要求

- Windows 10/11
- Python 3.11+（3.13 验证通过）
- 一台小爱音箱（LX05 验证通过，其他型号理论上可用）+ 绑定它的小米账号
- AI 后端二选一：
  - [CodeBuddy CLI](https://www.codebuddy.cn/)（腾讯，免费额度）+ Node.js
  - 任意 OpenAI 兼容 API（改 `config.json` 的 `api_base`/`api_key`/`model`）
- 可选：小米盒子（需开启 ADB 调试）

## 快速开始

```bat
:: 1. 安装依赖
pip install -r requirements.txt

:: 2. 生成图标资源（纯代码绘制，生成 app.ico / icon.png 等）
python make_icon.py

:: 3. 给 xiaogpt 3.24 应用增强补丁（无条件打断 / 快速轮询 / 同音词容错）
python patches\apply_patch.py

:: 4. 创建配置
copy xiaoai-bridge\config.example.json xiaoai-bridge\config.json
::    编辑 config.json 填入小米账号密码；CodeBuddy 用户无需改 api_base

:: 5. 启动桌面控制台
cd tiansheng-app
start.bat
```

控制台里：① 小米账号点「登录」扫码 → ② AI 后端点「登录」完成授权 → ③ 点「启动」，看到「小米云监听：监听中」即就绪。

**语音用法**：先喊「小爱同学」等它回「在呢」，再说「天声小爱 + 你想说的话」；盒子入口按住遥控器语音键直接说即可。

## 打包便携版（可选）

```bat
python pack_portable.py
```

生成 `天声小爱-便携版.zip`：自带 Python/Node 运行时，配置自动脱敏，拷到新电脑解压双击 `TianshengXiaoai.exe` 即用。

## 声明 / Disclaimer

- 本项目通过**非官方接口**与小米云服务交互，接口随时可能变更；请勿高频滥用，**小米账号存在被风控的风险，请自行评估**
- 请遵守所使用 AI 服务的用户协议（CodeBuddy 等）；因使用本项目产生的账号、数据、费用风险由使用者自行承担
- 仅供学习研究，勿用于商业用途

## 致谢

- [xiaogpt](https://github.com/yihong0618/xiaogpt)（MIT）— 小爱音箱 × LLM 的核心思路，本项目在其 3.24 基础上打补丁增强
- [miservice](https://github.com/yihong0618/MiService)（MIT）— 小米账号/音箱/米家协议
- [tetos](https://github.com/hidden-bomb/tetos)（Apache-2.0）— 多供应商 TTS 统一封装

## License

[MIT](LICENSE) © Johe-Zhao
