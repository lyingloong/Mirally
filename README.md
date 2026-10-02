# OpenAssist

OpenAssist 是面向 Windows 的桌面操作智能体。用户通过置顶会话窗口提交任务，智能体观察桌面、操作应用，并在需要补充信息或确认敏感操作时与用户交互。

产品入口、界面和任务调度由 OpenAssist 维护，桌面智能体内核基于 [Microsoft UFO](https://github.com/microsoft/UFO)。当前处于基础框架阶段，采用**每次提交一个独立任务**的运行方式。

OpenAssist 产品层负责窗口、任务会话和用户交互；UFO 内核负责桌面观察、模型决策和 Windows 操作。用户面向产品层配置，UFO 运行时所需的 YAML 统一维护在 `config/openassist/ufo/`，UFO 源码目录不再保留运行时配置副本。

## 当前功能

- 置顶桌面会话窗口：任务输入、状态展示、追问回复和敏感操作确认。
- 空闲或等待用户回复时，窗口可以输入、点击、拖动和调整大小；执行任务时自动半透明并穿透鼠标点击。
- 两种可切换的执行模式：轻量单任务模式、UFO 原生 HostAgent / AppAgent 多智能体模式。
- 每个任务独立运行，完成后可提交下一条任务；运行日志和截图保存在本地。
- 单任务模式增加了字段填写验证、任务内用户答案保留和循环检测。
- 已提供通用语音识别 Provider 配置和调用接口，可接入 OpenAI-compatible 音频转写服务。

目前仅支持 Windows。语音识别的录音按钮和界面流程尚未接入；持续会话、跨任务长期记忆、语音输出及独立前端也尚未实现。

## 快速开始

以下命令均在仓库根目录的 PowerShell 中执行。

### 1. 准备环境

需要 Windows 10 或更高版本、可交互的桌面会话，以及 Python 3.10（64 位，包含 Tkinter）。当前依赖沿用 UFO 的版本约束，建议先使用 Python 3.10。

```powershell
py -3.10 -m venv .\.venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r .\UFO\requirements.txt
```

启动器固定使用根目录的 `.venv/Scripts/python.exe`，无需激活虚拟环境。产品层与 UFO 内核共用该环境，依赖清单目前沿用 `UFO/requirements.txt`。已有根目录环境时可跳过创建步骤。

如果从旧的内核独立环境迁移，请按上述步骤在根目录重新创建环境。虚拟环境中的启动脚本可能包含绝对路径，应重新安装依赖；确认根目录环境可用后，可清理内核目录中的旧环境。

### 2. 配置模型

首次运行时，从产品层模板创建本地配置；已有 `agents.yaml` 时直接编辑，避免覆盖现有凭据。

```powershell
Copy-Item .\config\openassist\ufo\agents.yaml.template .\config\openassist\ufo\agents.yaml
notepad .\config\openassist\ufo\agents.yaml
```

在对应智能体配置中填写 `API_TYPE`、`API_BASE`、`API_KEY`、`API_MODEL` 等字段：

| 执行模式 | 使用的模型配置 |
| --- | --- |
| 单任务模式 | `APP_AGENT` |
| UFO 原生多智能体模式 | `HOST_AGENT` 和 `APP_AGENT`；启用评估或备用模型后还需配置对应智能体 |

默认视觉模式会向模型发送桌面截图，需要支持图像输入的模型。API 地址与模型参数应按照所用服务的要求填写，配置模板中的值仅为示例。

`agents.yaml` 包含本地凭据，已被 Git 忽略。可提交的配置模板为 [agents.yaml.template](config/openassist/ufo/agents.yaml.template)。所有 UFO 运行时 YAML 都只在 `config/openassist/ufo/` 维护；UFO 源码目录中不再有同名配置副本。

如果从旧版 UFO 配置迁移，请手动将需要保留的字段合并到产品层模板中；启动器不会读取或复制旧位置的配置。

### 2.1 语音识别配置（可选）

语音识别属于 OpenAssist 产品层，不与 UFO 的 LLM 智能体配置混用。产品层只约定通用的 OpenAI-compatible 音频转写协议，具体服务地址、模型和凭据由部署方配置。Provider 通过 `POST {base_url}/audio/transcriptions` 上传音频文件，默认支持 `wav` 或 `mp3`。

首次配置时复制产品层模板，并填写服务商提供的地址、模型和 API Key：

```powershell
Copy-Item .\config\openassist\speech.yaml.template .\config\openassist\speech.yaml
$env:OPENASSIST_SPEECH_API_KEY = "服务商提供的 API Key"
```

模板默认 `enabled: false`。启用时至少需要调整以下字段：

```yaml
enabled: true
provider: openai_compatible
base_url: "https://your-provider.example/v1"
model: "your-asr-model"
api_key: "${OPENASSIST_SPEECH_API_KEY}"
language: auto
```

`speech.yaml` 已被 Git 忽略。也可以把 API Key 直接填入该文件的 `api_key`，但推荐使用环境变量。当前代码已完成 Provider 层，录音设备、录音状态和输入框按钮尚未接入；完成后识别结果会进入现有任务输入和安全确认流程。

### 3. 启动

```powershell
.\run.ps1
```

如果 PowerShell 的执行策略阻止脚本，可以直接运行同一入口：

```powershell
.\.venv\Scripts\python.exe -m openassist
```

启动后，在窗口中选择运行模式，输入任务并点击「发送」，或按 `Ctrl+Enter`。需要补充信息时，在同一输入框回复；任务结束后窗口恢复交互，可提交下一条任务。

## 执行模式

### 单任务模式（默认）

由一个智能体执行「观察 → 决策 → 操作 → 再观察」循环，每条请求创建新的任务上下文。实现位于 [UFO/ufo/agents/single_agent/](UFO/ufo/agents/single_agent/)。

当前提供窗口切换、控件与坐标点击、文本输入、按键、滚动和等待等动作，并包含以下扩展：

- **字段填写**：`fill_text` 检查目标与焦点、替换字段内容，并在字段值可读取时验证结果；内容已匹配时跳过输入。对于受保护或不可读取的字段，结果仍需通过后续观察确认。
- **操作后观察**：填写等改变界面的动作会结束当前执行批次，后续提交动作需要重新观察和决策。旧的「点击输入框 + 输入文字」计划仅在确认字段为空时合并为填写动作。
- **执行反馈**：区分已执行、未执行及执行结果不确定的动作，避免把模型计划当成实际执行结果。
- **任务内答案保留**：用户补充的信息在整个当前任务中可用，不依赖最近几步的历史窗口；不作为跨任务长期记忆。
- **循环检测**：结合目标控件的焦点、字段值等状态判断进展，减少无关倒计时变化掩盖重复操作的问题。
- **技能提示**：按配置从 [UFO/ufo/skills/single_agent/](UFO/ufo/skills/single_agent/) 选择相关提示。

这些扩展属于轻量模式，原生多智能体模式使用 UFO 自身的执行路径。

### UFO 原生多智能体模式

通过 [openassist/ufo_full.py](openassist/ufo_full.py) 接入 UFO 原生 `normal` Session：HostAgent 规划并选择应用，AppAgent 执行应用内任务。OpenAssist 将追问、敏感操作确认和 Python 日志接到会话窗口，每次提交仍只执行一个请求。

该模式保留 UFO 原生工作流，可根据配置使用控件识别、GUI / API 操作、MCP 工具、RAG 和经验学习。相关能力需要对应服务、应用或数据支持，接入原生模式并不代表所有功能已启用。

当前产品入口接入的是 `normal` 桌面工作流。UFO 的批量任务、Follower、服务模式及 UFO³ Galaxy 多设备编排尚未接入产品界面。

## 仓库结构

```text
openassist/
├── README.md
├── run.ps1                         # 根目录启动入口
├── .venv/                          # 产品与内核共用的本地环境，Git 忽略
├── config/openassist/              # 产品层配置模板与本地配置
│   ├── speech.yaml.template         # 通用语音识别配置模板
│   └── ufo/                         # OpenAssist 使用的 UFO 配置
│       ├── agents.yaml.template     # 智能体模型配置模板
│       ├── system.yaml              # 执行、安全和运行时配置
│       ├── mcp.yaml                 # MCP 工具配置
│       └── rag.yaml                 # RAG 和经验配置
├── docs/                           # OpenAssist 产品与开发文档
│   └── frontend-experience-goals.md # 前端体验目标与规划建议
├── openassist/                     # 产品层
│   ├── __main__.py                  # 初始化内核路径并启动界面
│   ├── config.py                    # 产品层配置加载
│   ├── desktop.py                   # Tkinter 会话窗口与单任务调度
│   ├── speech.py                    # 通用语音识别 Provider
│   └── ufo_full.py                  # UFO 原生 Session 适配
├── tests/                          # 产品层测试
│   └── test_openassist_speech.py    # 语音配置与 Provider 测试
└── UFO/                            # 纳入根仓库的 UFO 内核源码
    ├── config/                     # UFO 配置加载器与类型定义（不含运行时 YAML）
    ├── ufo/agents/single_agent/     # 轻量执行循环及可靠性扩展
    ├── ufo/skills/single_agent/     # 轻量模式技能提示
    ├── tests/unit/                 # 单元测试
    ├── requirements.txt
    └── logs/                       # 运行日志与截图，Git 忽略
```

根目录维护一个 Git 仓库，`UFO/` 以源码形式随仓库维护。OpenAssist 负责产品交互与调度，内核执行能力在 UFO 中维护。

后续独立前端、录音服务和共享接口可分别放入 `apps/`、`services/`、`packages/contracts/`；这些目录目前尚未建立。语音识别 Provider 已位于 OpenAssist 产品层，录音采集和语音输出仍待实现。`Windows-Use/`、`openclaw/` 仅作为本地评估项目，已被忽略，不属于本仓库内容。

## 配置与运行记录

| 文件或目录 | 用途 |
| --- | --- |
| `config/openassist/ufo/agents.yaml` | 本地模型配置与 API 凭据 |
| `config/openassist/speech.yaml` | 产品层语音 Provider 配置，包含服务地址、模型和 API 凭据 |
| [speech.yaml.template](config/openassist/speech.yaml.template) | 通用语音 Provider 配置模板 |
| [system.yaml](config/openassist/ufo/system.yaml) | 步数上限、安全确认、循环检测、技能提示和原生工作流开关 |
| [mcp.yaml](config/openassist/ufo/mcp.yaml) | 原生模式 MCP 工具配置 |
| [rag.yaml](config/openassist/ufo/rag.yaml) | 原生模式检索与经验库配置 |
| `UFO/logs/openassist-<时间戳>/` | 每次任务的日志目录 |

单任务模式记录 `single_agent.jsonl` 和 `single_step_<序号>.png`；原生模式保存 UFO 自身的运行记录。日志可能包含任务内容、用户回复和桌面截图，请按本地调试数据管理。

## 开发与验证

计划完善前端体验时，请先阅读 [前端体验目标与规划建议](docs/frontend-experience-goals.md)。该文档说明截图与输入隔离、Agent 状态反馈、用户控制、接管视觉效果和验收方向，但不限定具体界面形态或技术栈。

从根目录运行轻量内核的回归测试：

```powershell
Push-Location .\UFO
try {
    ..\.venv\Scripts\python.exe -m unittest tests.unit.test_ufo_single_agent tests.unit.test_ufo_single_agent_extensions tests.unit.test_single_agent_form_entry
} finally {
    Pop-Location
}
```

运行产品层语音 Provider 测试：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_openassist_speech
```

这些测试覆盖动作处理、填写验证、循环检测和任务内答案保留，不代表真实桌面任务的端到端验证。修改界面或内核后需要重启桌面程序。

### 常见问题

- **提示找不到 OpenAssist Python 环境**：按照环境准备步骤在仓库根目录创建 `.venv/`。
- **启动时报模块缺失**：使用 `.venv/Scripts/python.exe` 安装 `UFO/requirements.txt`，确认安装过程完成。
- **模型鉴权或请求失败**：检查 `config/openassist/ufo/agents.yaml` 中对应智能体的地址、凭据、模型名称及图像输入支持。
- **语音配置无法加载**：确认 `config/openassist/speech.yaml` 已从模板复制，并在启用语音时填写 `base_url`、`model` 和 `OPENASSIST_SPEECH_API_KEY`。
- **语音服务请求失败**：确认服务实现了 OpenAI-compatible 的 `/audio/transcriptions` 接口，并检查音频格式、模型名称、网络和 API Key。
- **运行时无法点击 OpenAssist 窗口**：执行过程中窗口会穿透点击；空闲、等待回复或确认时恢复交互。
- **任务反复操作同一控件或提前结束**：查看该任务目录中的动作结果、循环检测反馈和截图，确认实际执行情况。

## UFO 基线

本项目基于 Microsoft UFO 2026 年 9 月版本的桌面 AgentOS 代码进行二次开发。当前仓库纳入的是 UFO 源码快照，未保留独立的上游 Git commit/tag 元数据，因此不将本项目宣称为某个具体上游 commit 的直接构建。

本项目在该基线上将产品入口、单任务调度、桌面会话交互、可靠性扩展和产品层配置组织方式进行了较大改动。UFO 的配置加载器代码仍位于内核中，但运行时 YAML 的唯一来源是 `config/openassist/ufo/`。

## 上游与许可证

UFO 内核来自 Microsoft UFO，包含本项目维护的修改。其 MIT 许可证及版权声明保留在 [UFO/LICENSE](UFO/LICENSE)。

进一步了解内核可参考 [UFO² 中文说明](UFO/ufo/README_ZH.md) 和 [UFO 本地文档](UFO/documents/docs/)。
