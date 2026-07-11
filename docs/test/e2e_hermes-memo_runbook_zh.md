---
标题: Hermes Memo Plugin 真实联调 Runbook（Memo W-29 hermes 侧）
状态: draft
参考材料:
  - 测试计划 (./tp_hermes-memo-plugin_zh.md) §3
  - hermes-telex 联调 Runbook (../../../hermes-telex/docs/test/e2e_hermes-telex_runbook_zh.md)
  - Memo 服务器部署脚本 (voyager/memo/test/data/deploy_test_server.sh)
文档摘要: hermes-memo 插件的端到端联调步骤——memo 服务部署在 voyager-test-0、hermes-agent 在同服务器的 Incus VM 内、测试者经本机隧道+浏览器驱动 Telex 对话。
---

# Hermes Memo Plugin 真实联调 Runbook

## 拓扑

与 hermes-telex E2E 同构、同实例；新增：memo 服务部署在 test server 本机。

```
[本机: 测试者客户端]                    [voyager-test-0]
 local-test.sh up                        voyager API :8000（Telex）
   ├─ chisel tunnel ───────────────────►   │
   └─ web :3000（真实账号驱动对话）           ├─ memo 服务 :18000 http / :19000 grpc / :18080 观测
                                            │    （/data/memo，user voyager，共享 PG/Valkey 本机直连）
                                            └─ Incus VM（hermes-agent + hermes-telex + hermes-memo）
                                                 ├─ TELEX_BASE_URL → http://<incus-host-gateway>:8000
                                                 └─ MEMO_BASE_URL  → http://<incus-host-gateway>:18000
```

- 凭证：`memo-dev-key`（MATE_INSTANCE，scope `u-dev:mi-dev`）；管理面 `memo-admin-key`。
- 验证/管理调用在 test server 本机（`smc toc` + curl 127.0.0.1:18000）或经隧道扩展端口。

## 1. memo 服务部署（本机执行）

```bash
cd voyager/memo
bash test/data/deploy_test_server.sh    # build linux/amd64 + 配置组装 + 安装重启 + 健康探针
```

预期尾行：`recent-raw -> 200`。重复执行即为升级重启（幂等）。

## 2. VM 内可达性验证

```bash
# hermes-memo repo
smc -c sea toc <test-server> -- "sudo incus exec <hermes-vm> -- \
  curl -s -o /dev/null -w '%{http_code}\n' -X POST http://<incus-host-gateway>:18000/memo/v1/recent-raw \
  -H 'x-api-key: memo-dev-key' -H 'content-type: application/json' \
  -d '{\"channel_type\":\"telex\",\"channel_conversation_ref\":\"vm-probe\"}'"
```

预期 `200`。若不通，确认 Incus 网络网关 IP（VM 内 `ip route | head -1`）。

## 3. 插件部署（本机执行）

```bash
cd openclaw/hermes-memo
deploy/deploy-memo-plugin.sh    # 打包→push VM→装依赖→provider 导入校验→seed memo.json→重启 gateway
```

VM 的 `~/.hermes/config.yaml` 需含（手工维护，脚本只警告不改动）：

```yaml
memory:
  provider: memo
```

## 4. 起测试者客户端（本机）

```bash
cd openclaw/hermes-telex
scripts/local-test.sh up     # tunnel(127.0.0.1:8000) + web(:3000)
```

浏览器 `http://localhost:3000` 登录真实账号，与 hermes bot（hermes-telex E2E 注册的既有 bot）对话。

## 5. 联调用例（对应 TP §3 E2E-1..E2E-4）

| 用例 | 操作 | 期望 |
|---|---|---|
| E2E-2 显式记忆（S2） | 私聊 bot："记住：<独特事实X>"；隔一轮问"X 是什么" | agent 调 memo_remember 落 note；memo_recall 命中并作答 |
| E2E-1 对话记忆闭环（S1） | 与 bot 聊出可沉淀内容 → 服务器侧 `consolidate-now`（admin curl）→ `list-promotion-candidates` → `submit-candidate-feedback` accepted（模拟 Mate）→ 重启会话 | 候选带建议行+逐信号理由；反馈后离列；新会话 RecentRaw 注入近期对话 |
| E2E-3 跨渠道回忆（S3） | 渠道 A（私聊）说素材 → 渠道 B（频道 @bot）问 | B 处 memo_recall 命中 A 素材，出处引用 A 会话 |
| E2E-4 逐轮自动档 | VM `memo.json` 加 `"auto_recall": true` 重启 gateway；对话观察注入；再关闭 | 开=每轮注入相关记忆；关=仅工具调用；超时不阻塞回复 |
| 契约自查 | 服务器侧查事件表/RecentRaw 回读 | channel_type=telex、conversation ref=Telex 原生会话 id、turn_kind/speaker 如实 |

验证辅助（服务器本机）：

```bash
# 事件是否落库（capture 回执后）
smc toc <test-server> -- "curl -s -X POST http://127.0.0.1:18000/memo/v1/recent-raw \
  -H 'x-api-key: memo-dev-key' -H 'content-type: application/json' \
  -d '{\"channel_type\":\"telex\",\"channel_conversation_ref\":\"<会话id>\"}' | head -c 600"
# 触发整理 / 查看候选
smc toc <test-server> -- "curl -s -X POST http://127.0.0.1:18000/memo/admin/v1/consolidate-now \
  -H 'x-api-key: memo-admin-key' -H 'content-type: application/json' -d '{\"scope_key\":\"u-dev:mi-dev\"}' | head -c 400"
```

## 6. 收尾

- 结果记入 Memo TR-14（`voyager/memo/docs/test/tr_memo_phase-1_batch-14_zh.md`）。
- 服务停止：`smc toc <test-server> -- "sudo su - voyager -c 'kill \$(cat /data/memo/memo.pid)'"`。
