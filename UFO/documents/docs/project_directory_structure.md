# Project Directory Structure

This repository implements **UFO³**, a multi-tier AgentOS architecture spanning from single-device automation (UFO²) to cross-device orchestration (Galaxy). This document provides an overview of the directory structure to help you understand the codebase organization.

> **New to UFO³?** Start with the [Documentation Home](index.md) for an introduction and [Quick Start Guide](getting_started/quick_start_galaxy.md) to get up and running.

**Architecture Overview:**

- **🌌 Galaxy**: Multi-device DAG-based orchestration framework that coordinates agents across different platforms
- **🎯 UFO²**: Single-device Windows desktop agent system that can serve as Galaxy's sub-agent
- **🔌 AIP**: Agent Integration Protocol for cross-device communication
- **⚙️ Modular Configuration**: Type-safe configs in `config/galaxy/` and `config/ufo/`

---

## 📦 Root Directory Structure

```
UFO/
├── galaxy/                 # 🌌 Multi-device orchestration framework
├── ufo/                    # 🎯 Desktop AgentOS (can be Galaxy sub-agent)
├── config/                 # ⚙️ Modular configuration system
├── aip/                    # 🔌 Agent Integration Protocol
├── documents/              # 📖 MkDocs documentation site
├── vectordb/               # 🗄️ Vector database for RAG
├── learner/                # 📚 Help document indexing tools
├── record_processor/       # 🎥 Human demonstration parser
├── dataflow/               # 📊 Data collection pipeline
├── model_worker/           # 🤖 Custom LLM deployment tools
├── logs/                   # 📝 Execution logs (auto-generated)
├── scripts/                # 🛠️ Utility scripts
├── tests/                  # 🧪 Unit and integration tests
└── requirements.txt        # 📦 Python dependencies
```

---

## 🌌 Galaxy Framework (`galaxy/`)

The cross-device orchestration framework that transforms natural language requests into executable DAG workflows distributed across heterogeneous devices.

### Directory Structure

```
galaxy/
├── agents/                 # 🤖 Constellation orchestration agents
│   ├── agent/              # ConstellationAgent and basic agent classes
│   ├── states/             # Agent state machines
│   ├── processors/         # Request/result processing
│   └── presenters/         # Response formatting
│
├── constellation/          # 🌟 Core DAG management system
│   ├── task_constellation.py    # TaskConstellation - DAG container
│   ├── task_star.py        # TaskStar - Task nodes
│   ├── task_star_line.py   # TaskStarLine - Dependency edges
│   ├── enums.py            # Enums for constellation components
│   ├── editor/             # Interactive DAG editing with undo/redo
│   └── orchestrator/       # Event-driven execution coordination
│
├── session/                # 📊 Session lifecycle management
│   ├── galaxy_session.py   # GalaxySession implementation
│   └── observers/          # Event-driven observers
│
├── client/                 # 📡 Device management
│   ├── constellation_client.py              # Device registration interface
│   ├── device_manager.py                    # Device management coordinator
│   ├── config_loader.py                     # Configuration loading
│   ├── components/         # Device registry, connection manager, etc.
│   └── support/            # Client support utilities
│
├── core/                   # ⚡ Foundational components
│   ├── types.py            # Type system (protocols, dataclasses, enums)
│   ├── interfaces.py       # Interface definitions
│   ├── di_container.py     # Dependency injection container
│   └── events.py           # Event system
│
├── visualization/          # 🎨 Rich console visualization
│   ├── dag_visualizer.py   # DAG topology visualization
│   ├── task_display.py     # Task status displays
│   └── components/         # Visualization components
│
├── prompts/                # 💬 Prompt templates
│   ├── constellation_agent/ # ConstellationAgent prompts
│   └── share/              # Shared examples
│
├── trajectory/             # 📈 Execution trajectory parsing
│
├── __main__.py             # 🚀 Entry point: python -m galaxy
├── galaxy.py               # Main Galaxy orchestrator
├── galaxy_client.py        # Galaxy client interface
├── README.md               # Galaxy overview
└── README_ZH.md            # Galaxy overview (Chinese)
```

### Key Components

| Component | Description | Documentation |
|-----------|-------------|---------------|
| **ConstellationAgent** | AI-powered agent that generates and modifies task DAGs | [Galaxy Overview](galaxy/overview.md) |
| **TaskConstellation** | DAG container with validation and state management | [Constellation](galaxy/constellation/overview.md) |
| **TaskOrchestrator** | Event-driven execution coordinator | [Constellation Orchestrator](galaxy/constellation_orchestrator/overview.md) |
| **DeviceManager** | Multi-device coordination and assignment | [Device Manager](galaxy/client/device_manager.md) |
| **Visualization** | Rich console DAG monitoring | [Galaxy Overview](galaxy/overview.md) |

**Galaxy Documentation:**

- [Galaxy Overview](galaxy/overview.md) - Architecture and concepts
- [Quick Start](getting_started/quick_start_galaxy.md) - Get started with Galaxy
- [Constellation Agent](galaxy/constellation_agent/overview.md) - AI-powered task planning
- [Constellation Orchestrator](galaxy/constellation_orchestrator/overview.md) - Event-driven coordination
- [Device Manager](galaxy/client/device_manager.md) - Multi-device management

---

## 🎯 UFO² Desktop AgentOS (`ufo/`)

Single-device desktop automation system implementing a two-tier agent architecture (HostAgent + AppAgent) with hybrid GUI-API automation.

### Directory Structure

```
ufo/
├── agents/                 # Two-tier agent implementation
│   ├── agent/              # Base agent classes (HostAgent, AppAgent)
│   ├── states/             # State machine implementations
│   ├── processors/         # Processing strategy pipelines
│   ├── memory/             # Agent memory and blackboard
│   └── presenters/         # Response presentation logic
│
├── server/                 # Server-client architecture components
│   ├── websocket_server.py # WebSocket server for remote agent control
│   └── handlers/           # Request handlers
│
├── client/                 # MCP client and device management
│   ├── mcp/                # MCP server manager
│   │   ├── local_servers/  # Built-in MCP servers (UI, CLI, Office COM)
│   │   └── http_servers/   # Remote MCP servers (hardware, Linux)
│   ├── ufo_client.py       # UFO² client implementation
│   └── computer.py         # Computer/device abstraction
│
├── automator/              # GUI and API automation layer
│   ├── ui_control/         # GUI automation (inspector, controller)
│   ├── puppeteer/          # Execution orchestration
│   └── *_automator.py      # App-specific automators (Excel, Word, etc.)
│
├── prompter/               # Prompt construction engines
├── prompts/                # Jinja2 prompt templates
│   ├── host_agent/         # HostAgent prompts
│   ├── app_agent/          # AppAgent prompts
│   └── share/              # Shared components
│
├── llm/                    # LLM provider integrations
├── rag/                    # Retrieval-Augmented Generation
├── trajectory/             # Task trajectory parsing
├── experience/             # Self-experience learning
├── module/                 # Core modules (session, round, context)
├── config/                 # Legacy config support
├── logging/                # Logging utilities
├── utils/                  # Utility functions
├── tools/                  # CLI tools (config conversion, etc.)
│
├── __main__.py             # Entry point: python -m ufo
└── ufo.py                  # Main UFO² orchestrator
```

### Key Components

| Component | Description | Documentation |
|-----------|-------------|---------------|
| **HostAgent** | Desktop-level orchestration with 7-state FSM | [HostAgent Overview](ufo2/host_agent/overview.md) |
| **AppAgent** | Application-level execution with 6-state FSM | [AppAgent Overview](ufo2/app_agent/overview.md) |
| **MCP System** | Extensible command execution framework | [MCP Overview](mcp/overview.md) |
| **Automator** | Hybrid GUI-API automation with fallback | [Core Features](ufo2/core_features/hybrid_actions.md) |
| **RAG** | Knowledge retrieval from multiple sources | [Knowledge Substrate](ufo2/core_features/knowledge_substrate/overview.md) |

**UFO² Documentation:**

- [UFO² Overview](ufo2/overview.md) - Architecture and concepts
- [Quick Start](getting_started/quick_start_ufo2.md) - Get started with UFO²
- [HostAgent States](ufo2/host_agent/state.md) - Desktop orchestration states
- [AppAgent States](ufo2/app_agent/state.md) - Application execution states
- [As Galaxy Device](ufo2/as_galaxy_device.md) - Using UFO² as Galaxy sub-agent
- [Creating Custom Agents](tutorials/creating_app_agent/overview.md) - Build your own application agents

---

## 🔌 Agent Integration Protocol (`aip/`)

Standardized message passing protocol for cross-device communication between Galaxy and UFO² agents.

```
aip/
├── messages.py             # Message types (Command, Result, Event, Error)
├── protocol/               # Protocol definitions
├── transport/              # Transport layers (HTTP, WebSocket, MQTT)
├── endpoints/              # API endpoints
├── extensions/             # Protocol extensions
└── resilience/             # Retry and error handling
```

**Purpose**: Enables Galaxy to coordinate UFO² agents running on different devices and platforms through standardized messaging over HTTP/WebSocket.

**Documentation**: See [AIP Overview](aip/overview.md) for protocol details and [Message Types](aip/messages.md) for message specifications.

---

## 🐧 Linux Agent

Lightweight CLI-based agent for Linux devices that integrates with Galaxy as a third-party device agent.

**Key Features**:
- **CLI Execution**: Execute shell commands on Linux systems
- **Galaxy Integration**: Register as device in Galaxy's multi-device orchestration
- **Simple Architecture**: Minimal dependencies, easy deployment
- **Cross-Platform Tasks**: Enable Windows + Linux workflows in Galaxy

**Configuration**: Configured in `config/ufo/third_party.yaml` under `THIRD_PARTY_AGENT_CONFIG.LinuxAgent`

**Linux Agent Documentation:**

- [Linux Agent Overview](linux/overview.md) - Architecture and capabilities
- [Quick Start](getting_started/quick_start_linux.md) - Setup and deployment
- [As Galaxy Device](linux/as_galaxy_device.md) - Integration with Galaxy

---

## 📱 Mobile Agent

Android device automation agent that enables UI automation, app control, and mobile-specific operations through ADB integration.

**Key Features**:
- **UI Automation**: Touch, swipe, and text input via ADB
- **Visual Context**: Screenshot capture and UI hierarchy analysis
- **App Management**: Launch apps, navigate between applications
- **Galaxy Integration**: Serve as mobile device in cross-platform workflows
- **Platform Support**: Android devices (physical and emulators)

**Configuration**: Configured in `config/ufo/third_party.yaml` under `THIRD_PARTY_AGENT_CONFIG.MobileAgent`

**Mobile Agent Documentation:**

- [Mobile Agent Overview](mobile/overview.md) - Architecture and capabilities
- [Quick Start](getting_started/quick_start_mobile.md) - Setup and deployment
- [As Galaxy Device](mobile/as_galaxy_device.md) - Integration with Galaxy

---

## ⚙️ Configuration (`config/`)

Modular configuration system with type-safe schemas and auto-discovery.

```
config/
├── galaxy/                 # Galaxy configuration
│   ├── agent.yaml.template     # ConstellationAgent LLM settings template
│   ├── agent.yaml              # ConstellationAgent LLM settings (active)
│   ├── constellation.yaml      # Constellation orchestration settings
│   ├── devices.yaml            # Multi-device registry
│   └── dag_templates/          # Pre-built DAG templates (future)
│
├── ufo/                    # UFO² configuration
│   ├── agents.yaml.template    # Agent LLM configs template
│   ├── agents.yaml             # Agent LLM configs (active)
│   ├── system.yaml             # System settings
│   ├── rag.yaml                # RAG settings
│   ├── mcp.yaml                # MCP server configs
│   ├── third_party.yaml        # Third-party agent configs (LinuxAgent, etc.)
│   └── prices.yaml             # API pricing data
│
├── config_loader.py        # Auto-discovery config loader
└── config_schemas.py       # Pydantic validation schemas
```

**Configuration Files:**

- Template files (`.yaml.template`) should be copied to `.yaml` and edited
- Active config files (`.yaml`) contain API keys and should NOT be committed
- **Galaxy**: Uses `config/galaxy/agent.yaml` for ConstellationAgent LLM settings
- **UFO²**: Uses `config/ufo/agents.yaml` for HostAgent/AppAgent LLM settings
- **Third-Party**: Configure LinuxAgent and HardwareAgent in `config/ufo/third_party.yaml`
- Use `python -m ufo.tools.convert_config` to migrate from legacy configs

**Configuration Documentation:**

- [Configuration Overview](configuration/system/overview.md) - System architecture
- [Agents Configuration](configuration/system/agents_config.md) - LLM and agent settings
- [System Configuration](configuration/system/system_config.md) - Runtime and execution settings
- [RAG Configuration](configuration/system/rag_config.md) - Knowledge retrieval
- [Third-Party Configuration](configuration/system/third_party_config.md) - LinuxAgent and external agents
- [MCP Configuration](configuration/system/mcp_reference.md) - MCP server setup
- [Model Configuration](configuration/models/overview.md) - LLM provider setup

---

## 📖 Documentation (`documents/`)

MkDocs documentation site with comprehensive guides and API references.

```
documents/
├── docs/                   # Markdown documentation source
│   ├── getting_started/    # Installation and quick starts
│   ├── galaxy/             # Galaxy framework docs
│   ├── ufo2/               # UFO² architecture docs
│   ├── linux/              # Linux agent documentation
│   ├── mcp/                # MCP server documentation
│   ├── aip/                # Agent Interaction Protocol docs
│   ├── configuration/      # Configuration guides
│   ├── infrastructure/     # Core infrastructure (agents, modules)
│   ├── server/             # Server-client architecture docs
│   ├── client/             # Client components docs
│   ├── tutorials/          # Step-by-step tutorials
│   ├── modules/            # Module-specific docs
│   └── about/              # Project information
│
├── mkdocs.yml              # MkDocs configuration
└── site/                   # Generated static site
```

**Documentation Sections**:

| Section | Description |
|---------|-------------|
| **Getting Started** | Installation, quick starts, migration guides |
| **Galaxy** | Multi-device orchestration, DAG workflows, device management |
| **UFO²** | Desktop agents, automation features, benchmarks |
| **Linux** | Linux agent integration, CLI executor for Galaxy |
| **MCP** | Server documentation, custom server development |
| **AIP** | Agent Interaction Protocol, message types, transport layers |
| **Configuration** | System settings, model configs, deployment |
| **Infrastructure** | Core components, agent design, server-client architecture |
| **Tutorials** | Creating agents, custom automators, advanced usage |

---

## 🗄️ Supporting Modules

### VectorDB (`vectordb/`)
Vector database storage for RAG knowledge sources (help documents, execution traces, user demonstrations). See [RAG Configuration](configuration/system/rag_config.md) for setup details.

### Learner (`learner/`)
Tools for indexing help documents into vector database for RAG retrieval. Integrates with the [Knowledge Substrate](ufo2/core_features/knowledge_substrate/overview.md) feature.

### Record Processor (`record_processor/`)
Parses human demonstrations from Windows Step Recorder for learning from user actions.

### Dataflow (`dataflow/`)
Data collection pipeline for Large Action Model (LAM) training. See the [Dataflow](ufo2/dataflow/overview.md) documentation for workflow details.

### Model Worker (`model_worker/`)
Custom LLM deployment tools for running local models. See [Model Configuration](configuration/models/overview.md) for supported providers.

### Logs (`logs/`)
Auto-generated execution logs organized by task and timestamp, including screenshots, UI trees, and agent actions.

---

## 🎯 Galaxy vs UFO² vs Linux Agent vs Mobile Agent: When to Use What?

| Aspect | Galaxy | UFO² | Linux Agent | Mobile Agent |
|--------|--------|------|-------------|--------------|
| **Scope** | Multi-device orchestration | Single-device Windows automation | Single-device Linux CLI | Single-device Android automation |
| **Use Cases** | Cross-platform workflows, distributed tasks | Desktop automation, Office tasks | Server management, CLI operations | Mobile app testing, UI automation |
| **Architecture** | DAG-based task workflows | Two-tier state machines | Simple CLI executor | UI automation via ADB |
| **Platform** | Orchestrator (platform-agnostic) | Windows | Linux | Android |
| **Complexity** | Complex multi-step workflows | Simple to moderate tasks | Simple command execution | UI interaction and app control |
| **Best For** | Cross-device collaboration | Windows desktop tasks | Linux server operations | Mobile app automation |
| **Integration** | Orchestrates all agents | Can be Galaxy device | Can be Galaxy device | Can be Galaxy device |

**Choosing the Right Framework:**

- **Use Galaxy** when: Tasks span multiple devices/platforms, complex workflows with dependencies
- **Use UFO² Standalone** when: Single-device Windows automation, rapid prototyping
- **Use Linux Agent** when: Linux server/CLI operations needed in Galaxy workflows
- **Use Mobile Agent** when: Android device automation, mobile app testing, UI interactions
- **Best Practice**: Galaxy orchestrates UFO² (Windows) + Linux Agent (Linux) + Mobile Agent (Android) for comprehensive cross-platform tasks

---

## 🚀 Quick Start

### Galaxy Multi-Device Orchestration

```bash
# Interactive mode
python -m galaxy --interactive

# Single request
python -m galaxy --request "Your cross-device task"
```

**Documentation**: [Galaxy Quick Start](getting_started/quick_start_galaxy.md)

### UFO² Desktop Automation

```bash
# Interactive mode
python -m ufo --task <task_name>

# With custom config
python -m ufo --task <task_name> --config_path config/ufo/
```

**Documentation**: [UFO² Quick Start](getting_started/quick_start_ufo2.md)

---

## 📚 Key Documentation Links

### Getting Started
- [Installation & Setup](getting_started/quick_start_galaxy.md)
- [Galaxy Quick Start](getting_started/quick_start_galaxy.md)
- [UFO² Quick Start](getting_started/quick_start_ufo2.md)
- [Linux Agent Quick Start](getting_started/quick_start_linux.md)
- [Mobile Agent Quick Start](getting_started/quick_start_mobile.md)
- [Migration Guide](getting_started/migration_ufo2_to_galaxy.md)

### Galaxy Framework
- [Galaxy Overview](galaxy/overview.md)
- [Constellation Agent](galaxy/constellation_agent/overview.md)
- [Constellation Orchestrator](galaxy/constellation_orchestrator/overview.md)
- [Task Constellation](galaxy/constellation/overview.md)
- [Device Manager](galaxy/client/device_manager.md)

### UFO² Desktop AgentOS
- [UFO² Overview](ufo2/overview.md)
- [HostAgent](ufo2/host_agent/overview.md)
- [AppAgent](ufo2/app_agent/overview.md)
- [Core Features](ufo2/core_features/hybrid_actions.md)
- [As Galaxy Device](ufo2/as_galaxy_device.md)

### Linux Agent
- [Linux Agent Overview](linux/overview.md)
- [As Galaxy Device](linux/as_galaxy_device.md)

### Mobile Agent
- [Mobile Agent Overview](mobile/overview.md)
- [As Galaxy Device](mobile/as_galaxy_device.md)

### MCP System
- [MCP Overview](mcp/overview.md)
- [Local Servers](mcp/local_servers.md)
- [Creating MCP Servers](tutorials/creating_mcp_servers.md)

### Agent Integration Protocol
- [AIP Overview](aip/overview.md)
- [Message Types](aip/messages.md)
- [Transport Layers](aip/transport.md)

### Configuration
- [Configuration Overview](configuration/system/overview.md)
- [Agents Configuration](configuration/system/agents_config.md)
- [System Configuration](configuration/system/system_config.md)
- [Model Configuration](configuration/models/overview.md)
- [MCP Configuration](configuration/system/mcp_reference.md)

---

## 🏗️ Architecture Principles

UFO³ follows **SOLID principles** and established software engineering patterns:

- **Single Responsibility**: Each component has a focused purpose
- **Open/Closed**: Extensible through interfaces and plugins
- **Interface Segregation**: Focused interfaces for different capabilities
- **Dependency Inversion**: Dependency injection for loose coupling
- **Event-Driven**: Observer pattern for real-time monitoring
- **State Machines**: Well-defined states and transitions for agents
- **Command Pattern**: Encapsulated DAG editing with undo/redo

---

## 📝 Additional Resources

- **[GitHub Repository](https://github.com/microsoft/UFO)** - Source code and issues
- **[Research Paper](https://arxiv.org/abs/2504.14603)** - UFO³ technical details
- **[Documentation Site](https://microsoft.github.io/UFO/)** - Full documentation
- **[Video Demo](https://www.youtube.com/watch?v=QT_OhygMVXU)** - YouTube demonstration

---

**Next Steps:**

1. Start with [Galaxy Quick Start](getting_started/quick_start_galaxy.md) for multi-device orchestration
2. Or explore [UFO² Quick Start](getting_started/quick_start_ufo2.md) for single-device automation
3. Check [FAQ](faq.md) for common questions
4. Join our community and contribute!
