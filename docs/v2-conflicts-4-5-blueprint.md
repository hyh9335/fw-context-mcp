# 本地 v2 融合 — 冲突项 4/5 架构蓝图与迁移计划

> 状态:阶段 1/2 已实施(bfee9263 / 0bd32540),阶段 3 部分达成。关联 docs/v2-main-branch-fusion-report.md。范围:主分支最新(turbyho/fw-context-mcp HEAD 0ff02e00)与本地 v2 直接冲突的架构演进。

## 实施记录(2026-10-13)

- **阶段 1**(`bfee9263`):新增 get_project_status(项目级概览:active/builds/total_facts/multi)与 get_build_info(单 build 详情:facts/fact_kinds/worlds/stale),均走 M6 authority 路由,v2-only 索引可用,向后兼容。真实 FreeRTOS 3-build 验证。
- **阶段 2**(`0bd32540`):resolve_build_name 精确模式,`FW_CONTEXT_M6_STRICT_SCOPE` 可开关;开启后多 build 无显式 variant 报错列候选,单 build/显式 variant/variant="*" 不受影响,默认关闭不破坏。
- **阶段 3**:查询选择路径已确认**不读 config default_variant**(resolve_build_name 基于 M6 build 注册序)。剩余"从 active world 精确推导"列 backlog(需 v2 查询层跨层传入 active_world_ids,避免过度设计)。

## 冲突总览

| # | 主分支演进 | 本地 v2 现状 | 冲突本质 |
|---|---|---|---|
| 4a | 查询按 build 精确命名 ("server selects none") | 靠 variant/active 选择 | 查询参数 vs 隐式默认 |
| 4b | get_active_build 拆为 get_project_status + get_build_info | 单工具 get_active_build(读 legacy) | 工具契约分裂 |
| 4c | 废弃 [build] default_variant/image | config 声明 variants,依赖 default | 配置模型 |
| 5 | build-as-image + 每 build 独立 DB | M6 多 build 共享单一 store | 存储拓扑对立 |

## 一、设计目标

1. 每个查询显式选择 project+build;未指定且多 build 时 fail-closed 报错并给候选。
2. 迁移期双轨,本地幸存工具(M6 层)不受破坏。
3. 保持 M6 多 build 共享 store(本地核心资产),在其上叠加"每 build 视图",不引入每 build 独立 DB。
4. get_active_build 的 v2-only 缺口(见 v2-read-migration-status)与 4b 一并解决。

## 二、架构目标态

```
get_project_status            (项目级概览)
  · variants/images/active/status
  · 每 build 行: variant, stale, config_hash, indexed_at   ← 融合项3
        └── 查询工具接收 project+variant+image 精确选择
get_build_info(variant, image) (单 build 详情)
  · identity / health / memory / defines / stale
```

- 4a: resolve_build_name 改为——无显式 variant/image 且多 build → 抛错列候选;单 build 可省略;保留 variant="*" 全 build 语义。
- 4b: 新增 get_project_status / get_build_info;get_active_build 进入过渡期(保留,内部委托)。
- 4c: default_variant 改为 "M6 active world" 推导,config variants 仅作 discovery 输入。
- 5: 不建每 build 独立 DB;从 M6(snapshot_builds.world_status / register_world / active_builds)读每 build 状态。build-as-image 仅对多程序单片场景有意义,本地标记 backlog。

## 三、分步迁移(双轨)

- 阶段1(纯增,不破坏):新增 get_project_status(M6/v2 路由)。每 build staleness = v2 world_status + M6 register_world + _check_header_staleness。新增 get_build_info。get_active_build 保留签名,委托两者。
- 阶段2(可开关精确):resolve_build_name 精确模式(多 build 无显式 variant 报错);variant="*"/单 build 豁免。
- 阶段3(配置收敛):default_variant 读作 deprecation;active 从 M6 active_builds() 推导。

## 四、测试与修复流程

| 阶段 | 测试 | 断言 |
|---|---|---|
| 1 | test_project_status_* (仿 W6 3 变体) | 列出 N build,每行 stale 布尔正确 |
| 1 | test_build_info_* | identity+memory+defines;未知 variant 报错含候选 |
| 1 | test_get_active_build_deprecated_* | 旧工具仍返回,与 project_status 一致 |
| 2 | test_scope_explicit_build_* | 多 build 无 variant → 报错列候选;单 build 免参 |
| 2 | W6 全量回归 | compare_variants/group 不受影响(variant="*" 豁免) |
| 3 | test_config_default_variant_* | 无 default_variant 仍选对 build |
| 全 | FreeRTOS 3 变体真实索引 + wire | 每 build stale 正确;精确命名生效 |

回归基线:store_v2+variants+slice1+paging(当前全绿 100+)保持绿。
修复流程:每阶段独立 commit(DECISION_LOG) → 真实索引验证 → 全量回归 → 下一阶段;失败回退该阶段 commit。

## 五、风险与回退

- A(阶段2 破坏无 variant 调用):豁免单 build/* 降低冲击;回退关精确模式 env。
- B(每 build stale 误报):stale 仅在状态表,不阻断查询;回退剔除该字段。
- C(M5 契约):改造限于查询/工具层,不碰索引写路径与 legacy retirement;每步 DECISION_LOG;gate 报削弱即停并上报。

## 六、待决

- build-as-image(5 完整形态):多程序单片场景才需要,本地无此场景,backlog。
- get_active_build 最终去留:过渡期后决定。
