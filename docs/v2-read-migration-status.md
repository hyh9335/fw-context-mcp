# v2 生产读权威迁移状态（工具矩阵）

> 状态追踪文档 — 记录 42 个 MCP 工具在 v2-only 生产索引下的读权威覆盖。
> 更新日期与 HEAD：见文末。本文件只记录现状，不包含修复代碼。

## 背景

本仓库的 M5 契约（`.dsh-control/contracts/IMMUTABLE_M5_GOAL_CONTRACT.md`，frozen）规定：

- 生产默认 **v2-only**：正常索引不产生 legacy `index.db`（legacy 仅作显式兼容岛）。
- "多 build 版本共享索引"由 **M6 store**（`store_v2/m6_*`，`index-m6.db`）承载：
  canonical shared facts + interned BuildSet（`fact_canon` UNIQUE，多 build 通过 `buildset_id` 共享同一物理事实）。
- v2 store（`store_v2/`，`index-v2.db`）承载 world/snapshot/semantic 事实。

查询路由两层（`mcp/server.py`）：

```
M6 authority (默认开) → M6 实现 (index-m6.db)
   ↓ 关
v2 authority           → v2 实现 (index-v2.db, 经 queryview / v2_shadow_adapters)
   ↓ 关
legacy handler         → legacy index.db
```

部分工具（maintenance / variants）未接入双层路由，直接走 DB 层 legacy `index.db`，
在 v2-only 生产索引下无数据 → 迁移缺口（下表）。

## 工具矩阵（42 个）

### A. 已迁移 — 核心语义工具 31 个 ✅

双层路由（`m6_authority_route` 包 `v2_authority_route`），v2-only 生产索引下直接读 M6 共享索引，正常。

| 类别 | 工具 |
|---|---|
| 搜索 | lookup_symbol, search_code, search_bodies, search_content, semantic_search, smart_search |
| 调用图 | find_all_callers_recursive, find_call_path, find_callees_recursive, find_callers, find_dead_code, find_hotspots, find_indirect_call_sites, find_indirect_targets, find_references, find_wrapper_callers, trace_data_flow |
| 源码 | explain_symbol, get_file_map, get_source, get_symbol_context, read_file |
| 继承 | get_class_members, get_inheritance_chain, get_method_overrides, get_template_instances |
| 变量/条件 | find_variables, explain_condition, list_conditional_regions |
| 向量 | get_vector_table |

### B. 未迁移 — maintenance 9 个（其中 4 个读 legacy 语义库）

裸 DB 层注册（`mcp.tool()` 直接包 legacy handler），未接 v2/M6 路由。

| 工具 | 数据源 | v2-only 下表现 |
|---|---|---|
| `get_active_build` | legacy `build_configs` + symbols/files/refs | ❌ 退化（"No build config indexed"） |
| `list_projects` | legacy `*.db` 的 `build_configs` 计数 | ❌ 退化 |
| `list_variants` | legacy `index.db` `get_all_builds_for_project` | ❌ 退化 |
| `reset_index` | legacy `open_db(index.db)` + `get_active_config` | ⚠️ 部分退化 |
| `get_project_info` | 全局注册表 `projects.db` | ✅ 不受影响 |
| `check_dependencies` | 环境检测 | ✅ 不受影响 |
| `check_ollama` | Ollama 检测 | ✅ 不受影响 |
| `configure_llm` | LLM 配置写入 | ✅ 不受影响 |
| `get_environment_status` | config + compile_commands + LLM 探测 | ✅ 不受影响 |

### C. 未迁移 — variants 比较 2 个

我上一轮将它们从 M6 路由摘出、直连 DB 层完整实现（legacy-compat 模式下正确且 W6 全绿），
但它们在 v2-only 生产模式下与 B 组同类退化。

| 工具 | 数据源 | v2-only 下表现 |
|---|---|---|
| `compare_variants` | DB 层 `index.db`（`resolve_db_context`） | ❌ 退化 |
| `group_equivalent_variants` | DB 层 `index.db` | ❌ 退化 |

## 代码定位（证据）

- `src/fw_context_mcp/mcp/shared/readiness.py:100` — `_db_path` 返回 legacy `index.db`
- `src/fw_context_mcp/mcp/shared/queryview.py:242` — v2 store（`index-v2.db`）解析（仅核心工具用）
- `src/fw_context_mcp/indexer/runner.py:502-524` — 正常路径 v2-only，legacy DB 不创建
- `src/fw_context_mcp/mcp/server.py` — 路由注册：31 工具双层，maintenance/variants 裸注册
- `src/fw_context_mcp/store_v2/m6_store.py` — `add_fact` 对同 `fact_canon` merge BuildSet（"只建一次"）

## 结论

多 build 共享索引（M6 store）本身正常且是主读权威；缺口集中在 6 个 DB 层裸工具
（get_active_build / list_projects / list_variants / reset_index + compare_variants / group_equivalent_variants），
它们仍读 legacy `index.db`。

修复方向（未实施）：将这 6 个工具接入 `queryview` / `v2_shadow_adapters` 的 v2 读取层
（get_active_build / list_variants 读 v2 `builds`/`snapshots` 表；compare/group 读 v2 `semantic_*`）。

---
_生成于 2026-10-13；HEAD 基线 `e0efe49`（W6 修复后）。_
