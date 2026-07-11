---
适用范围: hermes-memo 插件
参考材料:
  - hermes-memo 技术设计 (../spec/td_hermes-memo-plugin_zh.md)
  - Memo Phase 1 测试计划（voyager/memo/docs/test/tp_memo_phase-1_zh.md，W-29 段）
文档摘要: hermes-memo 插件的测试计划——单元层（fake Memo 客户端）+ 集成层（本地真 Memo 服务）+ 端到端（远端 test server 的 voyager/hermes 环境），对应 Memo WBS W-29 的 hermes 侧义务。
---

# hermes-memo 测试计划

## 1. 分层策略

| 层 | 替身 | 覆盖 | 运行 |
|---|---|---|---|
| 单元 | FakeMemoClient（进程内） | 容器映射、msg_ref 幂等、投递重试、RecentRaw 纪律、工具文案与分发、自动召回档 | `pytest tests/unit`（CI 常跑） |
| 集成 | 本地真 Memo 服务（memory_db 经隧道） | 北向真实往返：capture→RecentRaw、四工具、候选反馈链 | `pytest tests/integration`（需 `MEMO_LIVE=1`） |
| E2E | 远端 test server（voyager + hermes 同环境）+ 本地 Memo | S1-S3 新链路（对话→整理→候选→批准(SubmitCandidateFeedback 模拟 Mate)→注入）、逐轮自动档 | 人工触发，runbook 待补 |

## 2. 单元用例（已实现，tests/unit）

| 用例 | 验证 | 对应契约条目 |
|---|---|---|
| test_sync_turn_reports_provenance | channel_type/ref/msg_ref/turn_kind/speaker/ts 全字段如实 | 字段语义 |
| test_capture_retries_until_ack | 失败退避重试至回执；ref 冻结不重合成 | 原文完整性 |
| test_empty_turn_content_skipped | 空内容不产事件 | — |
| test_recent_raw_once_per_session | 首次 prefetch verbatim 注入（含 truncated），二次为空，仅一次调用 | 注入纪律 |
| test_recent_raw_refetch_on_compression | 压缩轮转重取；容器 ref 稳定 | 注入纪律/身份 |
| test_recent_raw_failure_is_silent | 服务不可用冷启动，不抛错 | 降级 |
| test_recall_tool_passthrough_and_degraded | packed_context 原样透传；degraded 直接用 | 注入纪律 |
| test_tool_validation / test_tool_error_never_raises | 参数校验与错误 JSON 化，不抛给 runtime | 工具纪律 |
| test_remember_and_forget_roundtrip | 显式记忆动词往返 | — |
| test_auto_recall_default_off / _on_injects_packed / _timeout_silent | 自动档三态 | 工具纪律 |
| test_gateway/cli/acp/fallback shape + non_primary skip | 容器映射一次定死；非 primary 不激活 | 身份与幂等 |
| test_msg_ref_* | 合成确定性与身份参与 | 身份与幂等 |
| test_kill_switch / test_json_overrides_env | 配置层级与关断 | — |
| test_four_tools_registered_with_canonical_names / test_tool_text_carries_untrusted_guidance | 规范工具文案 | 工具纪律 |

## 3. 集成与 E2E 用例（W-29 收口项）

| 用例 | 验证 | 状态 |
|---|---|---|
| IT-1 capture→事件表 | 真服务往返：上报后事件表 channel_type/turn_kind/speaker 与来源一致（承接 W-19 未闭 provenance 校验） | 待跑 |
| IT-2 capture→RecentRaw 闭环 | 上报若干回合→新 session RecentRaw 返回 verbatim；预算截断带 truncated | 待跑 |
| IT-3 四工具真往返 | remember→recall 命中→get 全文→forget 后不可召回 | 待跑 |
| IT-4 候选链（S1 后半） | 对话→ConsolidateNow→ListPromotionCandidates→SubmitCandidateFeedback(accepted，模拟 Mate)→（Mate 侧注入为其职责，Memo 侧验证状态记录） | 待跑 |
| E2E-1 S1 对话记忆闭环 | 真 hermes 会话经插件全链路 | 待跑（远端环境） |
| E2E-2 S2 显式记忆 | "记住X"→memo_remember→跨 session 召回 | 待跑（远端环境） |
| E2E-3 S3 跨渠道回忆 | 渠道A素材、渠道B召回（sources=memory 跨 conversation） | 待跑（远端环境） |
| E2E-4 逐轮自动档 | auto_recall=true 下开关与超时行为 | 待跑（远端环境） |
