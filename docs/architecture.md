# MVP 架构说明

## 现在这版怎么工作

1. 用户提示词里出现复核关键词时，宿主 Agent 先判断当前阶段
2. 如果还没执行任务，宿主 Agent 调用 `agent-review plan-review --host <agent>`
3. 如果已经执行完或已有交付物，宿主 Agent 调用 `agent-review review --host <agent>`
4. 宿主 Agent 完成任务后，正常交付结果，并在末尾提示是否需要交付复核
5. CLI 收集输入材料；方案复核收集任务和方案，交付复核补全 `git diff` 和 `changed files`
6. CLI 创建 `.agent-review/cases/<YYMMDD-编号>/`
7. 如果目标项目是 git 仓库，把 `.agent-review/` 写入 `.git/info/exclude`
8. Policy 判断当前材料是否足够触发复核；方案复核由用户明确触发，不靠文件改动判断
9. Router 选 reviewer
10. 交付复核的本地安全 preflight 先拦 `.env`、疑似 token / API key
11. 如果打开真实 reviewer adapter，就调用 Codex / Claude Code / Hermes
12. 如果真实 reviewer 不可用、超时或输出不合规，就降级到内置复核器
13. 输出 `review-result.json` 和 `review-result.md`
14. 更新 `.agent-review/latest-review.md`
15. 更新 `.agent-review/复核案卷索引.md`
16. 每次运行前自动清理 `.agent-review/cases/` 里超过 7 天的案卷

## 为什么先做内置复核器

MVP 先保证项目能独立落地，而不是强依赖三种外部 Agent CLI 都可用。内置复核器至少能先拦住这几类明显问题：

- 代码改了但没测试
- 测试失败还说完成
- diff 里出现疑似密钥
- 只改文档时直接放行

它不是最终形态，但足够验证“用户手动触发复核”这件事本身。后续用顺了之后，再接任务开始或任务结束自动化。

## 外部 reviewer 安全边界

外部 reviewer 命令按参数数组执行，不走 shell。这样 `{case_path}` 里即使有空格，也不会被拆成多个参数。

外部 reviewer 返回结果以 `PASS / FIX` 为主。旧自定义 reviewer 如果仍返回 `WARN / BLOCK`，本地会兼容映射为：

- `WARN` → `FIX + severity=normal`
- `BLOCK` → `FIX + severity=critical`

非法状态会被视为 reviewer 失败，并降级为 `FIX + severity=normal`。

敏感信息拦截不交给外部 reviewer 判断。只要本地 preflight 发现 `.env` 或疑似密钥，直接输出 `FIX + severity=critical`，并保持 CLI 返回码 `2`。

内置 adapter：

- `codex`：使用 `codex exec`，read-only sandbox，并传 JSON Schema。
- `claude-code`：使用 `claude -p`，JSON Schema 约束输出，只允许读文件相关工具。
- `hermes`：使用 `hermes chat -q` 的 quiet 单次查询模式，输出后本地提取 JSON 并校验。

所有 adapter 子进程都会继承 `AGENT_REVIEW_ACTIVE=1`，避免 reviewer 结束时再次触发复核循环。

## 手动触发规则

当前阶段不做自动拦截。主 Agent 完成有交付物的任务后，只在回复末尾提示：

```text
需要我再调用 agent-review 做一次复核吗？你可以回复“复核”或“帮我复核一下”。
```

只有用户明确回复 `复核`、`帮我复核`、`帮我复核一下`、`Agent 复核`、`自动复核` 或 `agent-review`，主 Agent 才调用复核插件。

## 输出可观测性

每次复核都会区分两类 reviewer：

- `selected_reviewer`：路由阶段选中的 reviewer，表示原计划找谁审。
- `reviewer`：最终实际复核的 reviewer。如果外部 reviewer 失败并降级，这里会变成 `local`。

每次复核都会返回两个主要日志入口：

- `status` / `review_status`：外显结果，主要是 `PASS` 或 `FIX`。
- `severity`：严重程度，`none` / `normal` / `critical`。
- `can_deliver`：是否建议直接交付。
- `review_log_path`：本次可读复核报告，固定指向案卷里的 `review-result.md`。
- `latest_review_path`：当前项目最近一次复核报告，固定指向 `.agent-review/latest-review.md`。
- `runtime_index_path`：当前项目的原始案卷固定入口，指向 `.agent-review/复核案卷索引.md`。
- `knowledge_index_path`：历史兼容字段，固定为空；复核记录不写入知识库。

主 Agent 把结果发给用户时，必须说明：

- 复核结论：用“通过 / 需要修改 / 不建议继续”说明结果。
- 谁复核的：说明是哪个 Agent；如果降级成本地规则检查，也要直说。
- 主 Agent 怎么处理：说明采纳了什么、改了什么、验证了什么；没采纳也要说原因。

固定输出格式：

```markdown
复核结论：通过 / 需要修改 / 不建议继续

- 复核方：Codex / Claude Code / Hermes / 本地规则检查
- 发现的问题：一句话说明；没有问题就写“未发现需要修改的问题”。
- 主 Agent 处理：已采纳什么、已修改什么、已验证什么；未采纳就说明原因。
- 下一步：可以交付 / 我先修复 / 需要你决定。
```

如果下一步是“我先修复”，主 Agent 直接继续修复，不等待用户确认。

默认不要向用户展示 `review_log_path` 或 `latest_review_path`。只有用户主动要报告路径，或需要排查问题时再给。

本地案卷使用短编号：

```text
YYMMDD-三位编号
```

例如 `260620-001`。旧的完整 `case_id-host-to-reviewer.md` 文件名不再继续使用。

这个编号就是本地案卷目录名。知识库只保留 PRD、教程、结构说明等长期资料，不展示每次复核记录。

每个案卷目录还会生成一个 `review-brief.md`，这是外部 reviewer 默认读取的唯一原始材料。它只放任务、主 Agent 交付说法、改动文件、截断 diff、截断测试摘要和必要提示。

`manifest.json` 和 `review-result.json` 是小体积机器结果，不进入 reviewer 语境。真实外部 reviewer 的 stdout/stderr 和原始输出只在临时目录中用于解析，解析完成后不落到案卷里。

每次调用 `agent-review review` 或 `agent-review plan-review` 写入新案卷前，都会扫描 `.agent-review/cases/` 并删除超过 7 天的目录。这个清理覆盖复核记录和原始资料，避免本地目录长期膨胀。

配置读取顺序：

1. 先读全局配置 `~/.agent-review/config.json`
2. 再读目标项目里的 `agent-review.json`
3. 项目配置覆盖全局配置
