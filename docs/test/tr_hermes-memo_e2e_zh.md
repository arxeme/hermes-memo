---
标题: Hermes Memo Plugin 真实联调报告（Memo W-29 hermes 侧）
状态: final
测试日期: 2026-07-11 ~ 2026-07-12
参考材料:
  - 联调 Runbook (./e2e_hermes-memo_runbook_zh.md)
  - 测试计划 (./tp_hermes-memo-plugin_zh.md) §3
文档摘要: hermes-memo 插件在真实环境（memo@voyager-test-0 + hermes VM + Telex）的端到端联调结果——E2E-1~E2E-4 全部通过；联调发现并修复两个真实缺陷（工具注册时机、会话容器标识）。
---

# Hermes Memo Plugin 真实联调报告

## 1. 环境

| 组件 | 位置 | 说明 |
|---|---|---|
| memo 服务 | voyager-test-0 `/data/memo`（:18000 http / :19000 grpc / :18080 观测） | `test/data/deploy_test_server.sh` 部署；共享 PG/Valkey 本机直连、S3 直连 <s3-endpoint>；scope `u-dev:mi-dev` 联调前清空 |
| hermes-agent | 同服务器 Incus VM `<hermes-vm>`（user hermes） | `deploy/deploy-memo-plugin.sh` 部署插件；`memory.provider: memo`；内置记忆关闭（见 §4 发现 3） |
| 测试者客户端 | 本机 chisel 隧道 + web :3000 | Telex Web 真实账号驱动；bot `<bot-id>`（与 hermes-telex E2E 同实例） |
| 网络 | VM → Incus 网关 `<incus-host-gateway>:18000` → memo | `memo.json` seed：base_url + memo-dev-key |

## 2. 用例结果

| 用例 | 验证内容 | 结果 | 证据 |
|---|---|---|---|
| E2E-2 显式记忆（S2） | "记住：数据库迁移冻结窗口…" → `memo_remember` 落 note → 提问命中 | PASS | `tool memo_remember completed (0.11s)`；`memo_file` 行 `notes/fact/数据库迁移的冻结窗口…md`（kind=fact, active） |
| E2E-2 补充（kind/pinned 推断） | "这条很重要，务必记住：生产发布前必须先在预发环境完成验证" | PASS | note 58：kind=**norm**、**pinned=t**——模型按工具指引正确推断类别与重要性 |
| 跨 session 召回 | gateway 重启（会话清空）后提问 | PASS | 新会话 `memo_recall completed (0.12s, 2031 chars)` 命中并正确作答 |
| E2E-1 候选链（S1 后半） | ConsolidateNow → ListPromotionCandidates → accepted（模拟 Mate） | PASS | sweep 报告 `candidates_surfaced × 1`；候选带建议行+逐信号理由（pinned 信号）；accepted 后候选离列 |
| E2E-1 注入（S1 收口） | 修复容器标识后重启 gateway → "我们刚才在聊什么？" | PASS | 新 agent 进程**零工具调用**准确复述重启前对话——只能来自 RecentRaw verbatim 注入；重启前后容器同为 `telex:b7cbc72a481784b0` |
| E2E-3 跨渠道回忆（S3） | 频道 @bot 问 DM 里的事实 | PASS | 频道容器 `telex:f5cb62347490a5df` 中 `memo_recall (0.22s)` 跨会话命中 DM 素材，回答正确 |
| E2E-4 逐轮自动档 | `memo.json auto_recall: true` → 重启 → 对话；后关回 | PASS | `initialized (…, auto_recall=True)`；对话流畅无阻塞；显式工具照常可用；验证后已关回默认 |
| 契约自查（provenance） | 事件表核对 | PASS | conversation_key=`telex:<Telex原生conv id>`、turn_kind user/agent、speaker role/display_name、`hm-` 合成 msg_ref 全部如实 |

## 3. Memo 服务侧断言（服务器 DB/API 直查）

- capture 全量落库：三轮对话 user/agent 事件成对入 `memo_session_event`，回执后即可经 `RecentRaw` 回读（verbatim、角色与顺序保持）。
- 显式 remember = 同步在线写路径：工具返回时 note 已可检索（0.07~0.15s），符合"写入零模型调用"设计。
- 候选反馈状态机：accepted 记录、surfaced 列表即时收敛。

## 4. 联调发现（已修复，各随缺陷单独提交）

1. **工具注册时机**（`fix: register tool schemas unconditionally`）：hermes 在 `initialize()` 之前收集工具 schema；原实现按会话状态门控导致注册 0 工具，模型 fallback 到内置 memory 工具。修复：schema 无条件返回，激活防护移到 dispatch 时。
2. **会话容器标识**（`fix(conversation): channel-native chat_id…`）：`gateway_session_key` 是 gateway 内部会话记录，**重启即变**（实测两次重启两个 key）——幂等命名空间断裂、RecentRaw 跨重启断链。修复：优先用平台插件上报的 `chat_id`（Telex 原生 conversation id，实测跨重启稳定），session key 降级为兜底。
3. **环境项（非插件缺陷）**：VM `~/.hermes/logs` 被 root 占有致 gateway 起不来（chown 修复）；hermes 内置记忆与 memo 工具并存时模型偏向内置——按 Memo 取代内置记忆的定位，将 `memory_enabled`/`user_profile_enabled` 置 false（VM config，一次性设置）。

## 5. 遗留

- 早期联调事件留有两个 gateway-session-key 形态的历史容器（`telex:agent:main:telex:dm:*`），无害，测试 scope 随时可 purge。
- openclaw 侧适配器未开始（用户决策 hermes 先行）；Memo TR-14 待双 runtime 收口后成文。
- 崩溃恢复回填（契约"应当"项）Phase 1 不做——无持久投递缓冲（与 membrain 同限制，TD §7）。
