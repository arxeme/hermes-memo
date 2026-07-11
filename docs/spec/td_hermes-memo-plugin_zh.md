---
适用范围: hermes-memo 插件（Hermes MemoryProvider × Voyager Memo 服务）
参考材料:
  - Memo Phase 1 技术设计（voyager/memo/docs/spec/td_memo_phase-1_zh.md，§4.1 北向契约与宿主适配器契约）
  - hermes-agent MemoryProvider 接口（agent/memory_provider.py）
文档摘要: hermes-memo 插件的技术设计——把 Memo 宿主适配器契约落到 hermes MemoryProvider 生命周期上：capture 上报、RecentRaw 一次性消费、四个规范工具、逐轮自动召回档，以及契约逐项自查表。
---

# hermes-memo 插件技术设计

## 1. 概述

hermes-memo 是 Memo 在 hermes runtime 上的宿主适配器（Memo TD §2.2 组件表中
的"宿主适配器"位），实现为 hermes 的外部 MemoryProvider 插件（`memory.provider: memo`）。
适配器是薄层：不做记忆决策，只履行契约义务——如实上报、纪律注入、原样注册工具。

对照物：hermes-membrain-plugin（同接口的前代记忆插件，本插件的结构参照）；
openclaw 侧适配器后续按同一契约实现，两者的工具文案保持逐字节一致。

## 2. 生命周期映射

| MemoryProvider 钩子 | Memo 义务 | 实现 |
|---|---|---|
| `initialize(session_id, **kwargs)` | 会话建立 | 推导会话容器（§3）、建 HTTP 客户端与投递 worker、后台发起 RecentRaw 取数 |
| `prefetch(query)` | RecentRaw 注入 | 首次调用返回 RecentRaw 块（verbatim 格式化，最多等 `recent_raw_wait_seconds`）；此后返回自动召回结果（若开档）或空 |
| `queue_prefetch(query)` | 逐轮自动召回（默认关） | 开档时后台调 `Recall(query)`，下一轮 `prefetch` 消费；超时静默 |
| `sync_turn(user, assistant)` | capture 上报 | user/agent 两事件入投递队列（来源字段见 §4），不阻塞回合 |
| `on_session_switch(new_id, reset, …)` | 压缩重建 = 契约允许的 RecentRaw 重取点 | 重推导容器（gateway/cli 形状下不变）、flush 投递队列、重新调度 RecentRaw |
| `on_pre_compress` / `on_session_end` | 送达责任 | 同步 flush 投递队列（有界超时） |
| `get_tool_schemas` / `handle_tool_call` | 四工具原样注册 | `memo_recall` / `memo_get` / `memo_remember` / `memo_forget`（tools.py 为规范文案源） |
| `is_available` / kill switch | 可关断 | `HERMES_MEMO_DISABLE=1` 时插件加载但不激活 |

## 3. 会话容器映射（一次定死）

conversation = 渠道侧对话容器（Memo TD §3.1）。hermes 各调用形状的容器层级
固定如下，随插件版本不变：

| hermes 形状 | channel_type | channel_conversation_ref | 理由 |
|---|---|---|---|
| gateway 会话（有 gateway_session_key） | 平台 id（telex/discord/…） | gateway_session_key | 渠道原生会话标识，跨 session_id 轮转稳定 |
| ACP（Cursor/Zed） | `acp` | ACP session_id | IDE 会话即容器（上游未穿透 cwd，与 membrain 同限制） |
| 终端（cli/tui，含 oneshot） | `cli` | user_id | 终端无渠道容器，以"该用户的终端关系"为容器；跨重启稳定，RecentRaw 语义自然成立 |
| 其余 | `hermes` | `session:<session_id>` | 兜底；压缩轮转经 on_session_switch 重推导 |
| cron / subagent / 非 primary | —（不激活） | — | 非渠道会话，不上报不召回 |

**channel_msg_ref 合成**：hermes 不向 MemoryProvider 透传渠道原生消息 id，
按契约走确定性合成：`hm-<ts_ms>-<role>-<sha256(content)[:16]>`，在入队时刻
合成一次并随事件冻结在投递缓冲——任何重试携带同一 ref，幂等成立。
崩溃恢复后的回填（"应当"项）Phase 1 不做（无本地持久缓冲，同 membrain 已知限制）。

## 4. capture 事件构造

- 上报范围 = sync_turn 携带的 user/agent 渠道可见内容；hermes 内部轨迹
  （工具调用、思考）不经 MemoryProvider 流出，天然不上报。
- `ts` = 适配器观察到回合完成的时刻（hermes 不提供渠道原生时间戳；对
  hermes 内嵌回合两者近似相等，偏差 = 回合处理时长）。
- `speaker`：user 事件带 `channel_user_ref`（initialize 的 user_id）与
  `display_name`；agent 事件带 agent_identity。
- 投递：CaptureWorker 有界缓冲 + 指数退避重试直至 accepted/duplicated 回执
  （at-least-once）；缓冲满丢最旧并告警。

## 5. 注入纪律

- RecentRaw 每 session 建立取一次（initialize 时后台发起、首次 prefetch 消费），
  session 内不重复调用；`on_session_switch`（压缩/branch/resume/reset）为重取点。
- 事件 verbatim 注入：`[role @ ts] content` 逐行格式化，不改写不摘要；
  `truncated` 如实标注。
- recall 的 `packed_context`（含不可信包装）原样透传给模型，插件不拆包装。
- `degraded: true` 的结果直接使用，不重试不报错。
- 自动召回档默认关；开启时超时静默跳过（prefetch 侧 50ms 消费窗 + HTTP 侧
  `auto_recall_timeout_seconds`），永不阻塞回复。

## 6. 宿主适配器契约自查表

对照 Memo TD §4.1（宿主适配器契约），必须项逐条给证据：

| 契约条目 | 级别 | 履约 | 证据 |
|---|---|---|---|
| 同一逻辑会话恒定容器 id | 必须 | ✅ | §3 映射表一次定死；`test_cli_shape_is_per_user_stable`、`test_recent_raw_refetch_on_compression`（轮转后 ref 不变） |
| channel_msg_ref 确定性合成 | 必须 | ✅ | §3 合成规则；`test_msg_ref_deterministic_for_same_event`、`test_capture_retries_until_ack`（重试不重合成） |
| x-api-key 不透明保管、不构造 scope | 必须 | ✅ | 请求体无任何 scope 字段（client.py 全部端点）；key 只进 header |
| 上报渠道可见全部消息、内部轨迹不上报 | 必须 | ✅ | §4；sync_turn 只见渠道内容（接口性保证） |
| 投递失败退避重试 + 本地缓冲至回执 | 必须 | ✅ | capture.py；`test_capture_retries_until_ack` |
| 断连恢复回填 | 应当 | ⚠️ Phase 1 不做 | 无持久缓冲（进程内缓冲覆盖瞬时故障）；与 membrain 同已知限制，记 §3 |
| ts = 事件时间 | 必须 | ✅（近似） | §4：hermes 无渠道原生 ts，取回合完成时刻，偏差有界 |
| turn_kind 如实标注 | 必须 | ✅ | sync_turn 只产 user/agent；cron/subagent 上下文整体不激活（§3） |
| speaker.role 必填 + user ref 一致 | 必须 | ✅ | `test_sync_turn_reports_provenance` |
| 附件仅 attachment_refs | 必须 | ✅（空集） | hermes 未向 provider 透传附件；上报事件不含二进制 |
| session_marker 上报 | 可选 | 未做 | reset 语义已由容器映射吸收 |
| packed_context / RecentRaw 原样注入 | 必须 | ✅ | §5；`test_recent_raw_once_per_session`（verbatim）、`test_recall_tool_passthrough_and_degraded` |
| RecentRaw 每 session 一次、压缩重建可重取 | 必须 | ✅ | `test_recent_raw_once_per_session`、`test_recent_raw_refetch_on_compression` |
| 工具按 Memo 名称/参数/指引原样注册 | 必须 | ✅ | tools.py 为规范文案；`test_four_tools_registered_with_canonical_names`、`test_tool_text_carries_untrusted_guidance` |
| 自动召回默认关、超时静默 | 必须 | ✅ | `test_auto_recall_default_off`、`test_auto_recall_timeout_silent` |
| degraded 直接用、不重试不报错 | 应当 | ✅ | `test_recall_tool_passthrough_and_degraded` |

## 7. 已知限制（Phase 1）

- 无持久投递缓冲：进程崩溃可能丢最后数个回合（内存缓冲只覆盖瞬时故障）。
- ts 为适配器观察时刻，非渠道原生时间戳（hermes 接口限制）。
- 群聊第三方发言依赖 hermes gateway 以 user_content 形式汇入；speaker 细分
  以 gateway 提供的 user_id 为准。
- ACP 容器为 per-session（上游未穿透工作区标识）。
