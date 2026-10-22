# 上游 main 最新 ↔ 多 build v2 融合对账（2026-10-22）

> 对比源：turbyho/fw-context-mcp `origin/main` @ `34f7f2bb`（v0.35.0 之后，2026-10-19）
> 对比对象：本地 `m3b-native-v2` @ `0705ecb2`（2026-10-14，多 build M6 共享事实图线路）
> 上一轮融合台账：`docs/v2-main-branch-fusion-report.md`（2026-10-13，对账 base `0ff02e00`）。
> 本轮把 base 从 `0ff02e00` 推进到 `34f7f2bb`，并对 236 个上游提交（v0.31.0→tip）逐簇核对 v2 现状。
> 两条线路无共同祖先：cherry-pick 机械上可行（diff 三方合并），低接触区基本干净，索引器/MCP handlers 高冲突区需手工适配。

## 一、已吸收确认（无需动作）

| v2 提交 | 内容 | 对应上游 |
|---|---|---|
| `39775a69`/`5b1c0239`/`30655984`/`f0f7ef16` | paging 模块 + M6 读路径分页 | `f2947011` `7b555ce6` `443f4e88` `bf2c7d54` `b4cf8f68` `65a0784c` `51ce0881` `a3457984` 等 |
| `6baf7f24` | init-list 元素字段解析系列 | `2bc5f6b9` `9439f745` `8cad8223` `d95d4374` `429fd44e` |
| `bfee9263`/`7a930e77` | get_project_status / get_build_info / get_build_health + 每 build staleness | `baec68fd` `65354db4`（语义对齐版） |
| `0bd32540`/`3fd50c35` | strict build scoping stage 1/2（conflict-4） | `6d0a2fe6` `e2ee0419` `5ef75aee` |
| `7bdd80ec` | list_projects 发现 M6-only 项目 | （v2 自有，覆盖上游 v2-only 场景） |
| `a829eba3`/`60b7b930` | Body/FileContent 事实进 M6 | — |

## 二、高价值·可吸收·多 build 直接相关（建议下一轮融合）

### B1. 构建编排 / builders 整批（v2 完全没有；多 build 的"进料口"能力）
v2 的 builder 仍是 v0.31 前端。上游 10-01..10-06 重构了 variant/构建语义：

- `29dd3db2` 每 build 询问 GCC driver（去掉 allowlist）+ `f1d770b9` 用自身 driver 的头/宏解析 GCC 构建 + `b11dfd0f` driver 查询与编译同跑 + `eee1805b` 自动把构建编译器加进 allowlist
- `1aca7e10` 内置 clang-18 头供 GCC 构建（v2 `data/` 无 clang-resource）
- `988ab384` 每个 variant 构建进 `.fw-context/build/<variant>/out`；`5c8cb5b0` 真 make 构建；`e2b0043f` PlatformIO env=独立 variant；`a2a61eaa` config 指定的 DB 只索引不构建
- 链接脚本：`db3fe820`（esp-idf ninja -L）、`4372081e`（PlatformIO envdump）、`ff4d634b`（MEMORY 续行）
- 韧性：`4e9cceff`（build env/extra_path 透传）、`824176c7`（macOS EPERM）、`79890fe9`（索引停止→构建停止）、`d2b7838a`（每 variant 独立编译 DB）、`35cf8e95`（staging 原子写）、`641e9b96`（zephyr 原子替换 image DB）

### B2. Indexer 事实质量波（直接提升 M6 事实质量——v2 的全部事实来自这条前端）
- `995f785f` TU 增量决策按 hash 不按 mtime（**v2 仍用 `MTIME_TOLERANCE_S`**）+ `6856266e` 头文件一律重哈希（含生成头）
- `5ef17f80`/`49317bad`/`15f7c1b7`/`8f94cb66` 解析文本的哈希/读取/存储一致性（body 事实正确性的根）
- `028e76b6`/`f584bd44`/`cff1f333` manifest 每 listing 一条、无头 TU 也记
- `987a009e` 每轮重建 override graph；`de946cf7` 每轮 PageRank + hotspot 缓存
- `bbc02b4a` 中断的索引保留已解析单元；`7adc4e55` 无变更跳过宏步骤（perf）；`45caf7ef` 每文件独立句柄
- `89a2513a` 双哈希统一 transient-macros 配置；`43338b2e` fp_assignments 每事实一行
- `19bb1c19` 汇编文本取自预处理记录
- `6bd1e16e` 嵌入不全的 build 打 "partial" 戳 + `38eab3b3` uncovered-sources 按 build —— 天然映射到 M6 World 状态机

### B3. 查询/工具面的多 build 语义（conflict-4 收尾 + 10-13 后新波次）
- `8b724776` 每个回答携带每 build 索引状态（v2 无）；`7b2959cd` 状态/查询命名 build 命令；`7cdb8405` 多 build 分析建议；`d4819da7` LLM 指令中的 build/tools 修正
- `f5e5003b` reindex_file 在**每个**编译它的 build 重索引 + `8ae7efb1` 线程化+notice + `d9d30a51` 重嵌入 header 符号 + `dff0947f`(!) 合并 reindex_file_impl —— 与 v2 的 M6 直接物化 reindex（`03d58de0`）对齐语义
- `0ff02e00`/`7fdcd26c` checkout 跟随/记录 —— 建议落为 M6 world 元数据
- `b94d16bb`/`64557c3f` per-build DB/row-format 检查 —— v2 共享库，适配为 per-world 检查
- conflict-4 收尾：stage3 active-world 推导（`3fd50c35` 留的 backlog）+ 4c default_variant 收敛（`5cf0925e`(!) `8684a47d` `1e04c570` `de8d7dae`）

### B4. Search 管道收敛与质量（v2 仍有 SEARCH_CODE 管道，10 个文件）
- `bce0ec14`(!) 移除 SEARCH_CODE 管道及 fallback —— v2 应在 M6 读路径上做同型收敛（吸收的是简化，不是功能）
- limit/超时：`24aa1885` `27a0bc80` `30c694f9`；`1e38074d` relevance floor 按全匹配集；`4bda70b5` source boost 限 200 窗口；`ad3202f4`/`b1697436` 格式化截断；`4a4c295b` 字段恢复；`904e0ab3` did-you-mean 进程间稳定；`1347bc3a` 向量顺序测试
- v2 有 did_you_mean/relevance_floor 的旧版本——这些是 bugfix，需 diff 移植

## 三、低冲突整批吸收（架构无关，建议一批移植）

- Config 韧性：`e0039060` `80126542` `2d8dbf16` `10e1132d` `6c0dc020` `95e19fea`
- LLM/Ollama/deps：`9d6dab02` `df2b8ffe` `ca149788` `842d2410` `f781d53c` `89190725` `7b42f85a` `89296b45` `c5c3f5a4`
- Doctor/状态：`ddbc2d3d` `65a0729a` `8d867627` `a247b26b`（config_error 状态，v2 无）`839de073`（新版升级提示，v2 无）`4fc97ff7` `d1477d18` `b747768b` `2c12266d`（reason 命名）
- Init：`b9350b4a` `3a1cc4c9` `bb369cd9` `d9f85394` `cb6bc980` `9b960585` `11c93ca6` `88e6651c`
- CLI：`17d18c8d` `915d91eb` `693e26a3`（takeover 语义）`c78ef704` `55bea3ac` `ff8a94ec`
- Daemon/性能：`05b0e12c` `bc0748ae` `56928167` `2c12266d`（DB integrity 每进程一次）`13fe31ea`（连接缓存按连接键）`f3d55d4c`（count 不 join）
- Cache：`f13e14aa` `dcf2bcc8` `d128785b` `ba203ad9`
- 读取/图修复（M6 侧对应适配）：`c158b093` `451e5e65` `6eed8181` `7e1b4b7f` `6f4ff392` `e247a089` `c2f3a3b5`（variables per-build cap）`f9d00e05`；callgraph/resolve 簇 `c38540fc` `e9055a06` `d6bd67ba` `007ac47c` `28a6b350` `9cceb60a` `1ac3393e` `bff8dc7b` `5620674c` —— v2 callgraph 走 M6，需判断在 M6 查询层重现
- Registry：`67222c35` `82c50f01` `89340238`
- 文档/工具描述：`8e6af27c` `a4f5521e` `098a9343` `dfde34f8` `5d4f3251` `4f4f...docs(tools)` 系列

## 四、不吸收（与 v2 架构对立 / 已被取代）

- **每 build 独立 DB、build-as-image**（冲突项 5）：v2 蓝图已决策保持 M6 共享单店；image 概念如有多程序场景需求，建模为 M6 World 属性（backlog）
- **get_active_build 的上游式移除**：v2 采用过渡期委托（4b），最终语义一致即可
- `dff0947f` 的破坏性签名变更随 B3 一并处理，不单独移植

## 五、建议执行顺序

1. **C 类整批**（低风险，一次 cherry-pick 会话 + 冲突手解）
2. **B2 indexer 质量波**（提升 M6 事实质量；每步跑 `python .dsh-control/gates/run_m5_stage_gate.py`）
3. **B1 builder 批**（多 build 核心能力；用 v2 已有的 FreeRTOS 3-variant 真实索引回归）
4. **B3 + conflict-4 stage3 收尾**（每回答携带 per-build 状态、reindex 语义对齐、active-world 推导）
5. **B4 search 收敛**（M6 读路径统一后移除 SEARCH_CODE）

## 六、v0.35 波次量化（`0ff02e00`→`34f7f2bb`，58 提交）

> v2 在 2026-10-13 吸收的正是 v0.34 波（base `0ff02e00` = v0.34.0 发布后 5 天），踪迹：
> `docs/v2-main-branch-fusion-report.md`、`docs/v2-conflicts-4-5-blueprint.md`、
> 提交 `39775a69`/`5b1c0239`（fuse paging）、`6baf7f24`（fuse init-list）、
> `bfee9263`/`0bd32540`/`3fd50c35`（conflict 4/5 stage 1/2/3）。
> 本轮量化 = v0.35.0 wave 相对该 base 的净增量。

- 体量：**128 文件，+11,990 / −2,239 行**；构成：fix(mcp)×15、feat(mcp)×4、refactor(mcp)×4、fix(deps)×6、fix(config)×4、fix(cli)×4+feat(cli)×3、fix(daemon)×3、fix(llm)×3、fix(index)×2、perf(db)×1、docs/test 若干
- 逐文件 blob 对比 v2 vs 融合 base：

| 类别 | 文件数 | 含义 |
|---|---|---|
| 干净落地 | **62（48%）** | v2 自 base 起未改动（含 wave 新增文件），cherry-pick 零冲突 |
| 已吸收 | 0 | v2 停在 10-14，wave 未移植 |
| 需手工合并 | **66** | 其中 docs/tests/元数据 23 个（琐碎），src 43 个 |

- 66 个手工文件中**绝大多数是"点状修复落在 v2 也改过的文件上"**——上下文冲突，不是语义冲突。
- 真正需要动脑的只有 4 项：
  1. `f5e5003b`+`8ae7efb1`+`d9d30a51`+`dff0947f` — reindex_file 每 build 化：v2 已有自己的 M6 直接物化 reindex（`03d58de0`），需在其上**重新实现**上游语义（唯一中改点）
  2. `8b724776` — 每回答携带每 build 索引状态：在 v2 的 authority 路由/回答组装点注入 M6 world_status 读（小中改）
  3. `ef0c6053`! — CLI 按 build 选择 search/export/analyze：接 v2 的 resolve_build_name strict 模式（管道工作）
  4. `6bd1e16e` — partial build 戳：映射到 M6 World 状态（小改）

**结论：不需要大幅度修改代码。** wave 无新架构，约一半文件干净落地；其余为点状适配；
仅 reindex_file 一项需要语义级重实现。M6 存储架构、读/写权威层完全不动。

> 维护提醒：本文件是对账快照；执行进展应回写 `docs/v2-main-branch-fusion-report.md` 的执行记录。
