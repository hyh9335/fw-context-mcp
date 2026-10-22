# 最新主分支 vs 本地 v2 融合报告

> 对比源:https://github.com/turbyho/fw-context-mcp(GitHub 主分支 HEAD `0ff02e00`,推送于 2026-10-13 07:13Z,对应本地 API 分页可见 commit;本地 `origin/main` 缓存停在 `60edcc4f` = v0.31.0,以下内容以 GitHub API 拉取的最新主分支(到 v0.34+ / HEAD)为准)
> 对比对象:本地 `m3b-native-v2` 分支(HEAD `a829eba3`)
> 状态:留档对比 + 融合执行记录

## 一、历史关系

两条**独立历史**,无共同祖先(本地从主分支早期基线重写):
- 主分支:单 legacy DB + FTS5 架构,851+ commit 持续演进(v0.31 → v0.34+)
- 本地 v2:src 199 文件(含主分支全部 156 + 43 个 store_v2/variants/conditionals/platform),M6 多 build 共享索引架构

文件/工具集层面本地是**超集**(主分支 27 工具本地全有 + 本地 14 个独有),但**最新主分支在架构语义上已大幅前进**,与本地方向分道。

## 二、最新主分支核心演进(本地 v2 没有的)

| 演进 | 主分支 commit | 主分支内容 | 本地 v2 现状 | 冲突度 |
|---|---|---|---|---|
| 查询按 build 精确命名 | `e2ee0419` `74223166` `6d0a2fe6` | "one query for one build, server selects none";废弃隐式 scope list | 本地靠 variant/active 选择 | **高** |
| get_active_build 拆分 | `baec68fd` | get_project_status + get_build_info 取代 | 本地仍是单 get_active_build(读 legacy) | **高** |
| 废弃 default_variant | `5cf0925e` `8684a47d` | retires `[build] default_variant/image`;unite declared+indexed variants | 本地 config 仍声明 variants | **中高** |
| paging 体系 | `f2947011` `7b555ce6` `443f4e88` `65a0784c` 等 10+ | 每工具 offset/count/totals/pages/hints | 本地无分页 | **中·独立** |
| 每 build staleness | `65354db4` `b94d16bb` `64557c3f` | 报告每 build 独立 staleness} | 本地单库多 build(v2/M6 共享) | **高** |
| build-as-image | `b4bc665d` | 每个 build 的程序索引为 image | 本地无 image 概念 | **中** |
| indexer 精准修复 | `2bc5f6b9` `8cad8223` `43338b2e` `987a009e` `de946cf7` 等 | init-list 字段/overload/每索引 PageRank+override graph | 本地旧版前端 | **低·可移植** |

## 三、融合决策

### 融合项 A(兼容、低风险、用户可见)⭐
1. **paging 体系** — 纯 MCP 层增强,不碰数据层,平移到本地 v2/M6 路由工具
2. **indexer 精准修复** — 前端提取层增强,与 store 无关,可移植
3. **每 build staleness 报告** — 契合本地多 build 共享索引理念

### 冲突项 B(架构对齐决策,不可 cherry-pick)⚠️
4. **scope/build 精确命名 + get_active_build 拆分 + retire default_variant** — 与本地 M6 共享 + variants 工具直接冲突,需重新设计本地 build 选择语义
5. **build-as-image / 每 build 独立 DB** — 与本地"多 build 共享一个 store"对立

### 冲突项处理流程(详见证文末执行记录)
对冲突项 4/5:先出**架构蓝图 + 分步迁移 + 兼容期双轨 + 回归测试**,再动代码。

## 四、执行记录(持续追加)

- 2026-10-13:
  - 确认对比源为最新主分支(GitHub API HEAD `0ff02e00`)
  - 融合项 A 实施开始(paging → indexer → staleness)
  - 重新下载 FreeRTOS 用于真实索引测试
</parameter>
