# fw-context × DSH 项目级自治控制器（同一 DSH Host，无需重启）

## 核心原则

本版本不安装任何 fw-context 专用 DSH 插件，不修改 DSH_HOME，不修改 profile，不需要单独启动 DSH。

它只复用当前 DSH 已经存在的通用能力：
- Goal
- Subagent / Subagent Fork / continuable child（若当前 preset 可用）
- Workflow（若可用）
- Bash / file tools

项目专用部分全部落在 fw-context 仓库：

```
<repo>/
  AGENTS.local.md
  .dsh-control/
    contracts/
    prompts/
    gates/
    state/
    evidence/
```

因此其它项目不会读取这些项目级指令，也不会共享这个 Goal / 状态。

## 为什么不再使用项目专用 completion-guard plugin

当前 DSH 的工具/插件 registry 主要是 Host/profile 级；真正 workspace-scoped runtime 仍不是稳定通用能力。
为了完全避免污染其它任务，本版不向 Host 注册任何 fw-context 插件。

完成防线改为：

1. HUMAN-OWNED immutable contract
2. code-derived hard gate executable
3. project-local AGENTS.local.md 强制 Controller protocol
4. fresh Guardian
5. fresh Verifier
6. final gate 必须绑定当前 HEAD + verifier/guardian acceptance

这不是 Host 级不可绕过插件，但不会影响其它项目。

## 一次入口

在 fw-context 项目中的一个新/现有 DSH 会话，直接发送：

`prompts/START_HERE_SINGLE_ENTRY.md`

入口会自己：
- 检测 DSH 当前可用工具；
- 安装 `.dsh-control/`;
- 创建/更新项目级 `AGENTS.local.md`;
- 生成 immutable contract 和 gate;
- 运行 self-test;
- 创建 DSH persisted Goal;
- 启动 Controller;
- 自动创建 Lead / Guardian / Verifier;
- 直接开始实际工程工作。

无需第二条启动提示词。
