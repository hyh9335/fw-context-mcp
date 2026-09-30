# 同一 DSH Host 下的项目级设计

## 不做的事

- 不安装 fw-context 专用 DSH plugin
- 不修改 DSH profile
- 不修改 DSH_HOME
- 不启动第二个 DSH

## 利用的 DSH 原生项目边界

- Workspace/session 由 cwd 归属
- 仓库内 AGENTS.local.md 会作为项目指令自动进入该项目 Agent 请求
- Goal 属于 session，而不是整个 Host
- Subagent child session 通过 parentSession / cwd 继承项目上下文
- 项目状态落盘，不依赖 Agent 长上下文

## 为什么 Guardian 默认 fresh

长期 Guardian 也会与 Lead 共漂移。
Fresh Guardian 每次从 immutable contract + current code + scanner evidence 重建认知，
更适合做 Goal drift 检测。

## 为什么没有不可绕过的项目 completion plugin

当前 DSH 工具/插件 registry 仍主要是 Host/root 级，workspace-scoped runtime 尚不是通用稳定机制。
为了零污染，项目不注册 Host plugin。

因此最终防线是：
- project-local AGENTS.local protocol
- executable hard gate
- same-HEAD acceptance artifacts
- Controller-only parent Goal completion

如果未来 DSH 正式支持 workspace-scoped tool/policy plugins，可以再把 final gate 升级为真正的 workspace-local pre-execute deny。
