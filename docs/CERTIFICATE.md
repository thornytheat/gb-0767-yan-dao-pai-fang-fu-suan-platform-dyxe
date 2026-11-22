# 复核监测证据单

从留档打开签发页，确认运行编号、监测烟道和分钟差额后下载一份 JSON。`originals` 中的原件解码成文本后以 UTF-8 重新编码，逐文件计算 SHA-256，应等于 `manifest.sha256`。

核验 `sha256` 时，从附件取出 `manifest`、`originals` 和 `results` 组成对象。深度排序对象键，以 UTF-8 编码 JSON，保留中文原字符，分隔符使用逗号和冒号且省略空白。结果是内容校验摘要。原件可用 `files` 对象重新归档，以 `run_id` 和逐行判据对照两次计算。

按 `results.validity.rows[].sources` 找分钟原件，`mode_sources` 定位工况；按 `results.reports.rows[].reports` 找报送单据。错误版本及 CSV 结构错误分别见 `oxygen.isolated` 和 `intake.files[].rejected`。

## 留档丢失后的恢复

把整份证据单 JSON 提交到 `POST /api/restore`（页面入口在「监测留档复跑」与「监测证据签发」）。恢复前先做全部校验，任何一项不通过都不会写入，也不会占用原编号；失败后可以修正证据单再试。

恢复区分两件事，结果写入 `verification`，并与分钟结论一起出现在留档页面：

| 状态 | 含义 | 判定 |
|---|---|---|
| `RULE_VERIFIED` | 附件校验通过，按原规则重算一致 | 原件逐文件 SHA-256、内容摘要、勾稽、编号均通过，且原规则版本仍在 `app/rules.py` 的 `ENGINES` 中，重算参数与五道工序结果逐字节一致 |
| `ATTACHMENT_VERIFIED` | 附件校验通过，原规则已停用未重算 | 只核对证据单自身：原件摘要、内容摘要、勾稽与小时结论守恒；不按现行规则改算，保留签发时的小时结论 |

编号仍由「规则版本 + 原件摘要」计算，因此恢复出的编号与签发时一致；同一张证据单重复提交或两班同时提交，都只建立一条留档，先到者生效，其余返回 `reused: true`。

被改动过的证据单会被拒绝，且不留下占位记录：原件被改报 `EVIDENCE_MISMATCH`，内容摘要不符同样如此；原规则可用时结论被改报 `RULE_MISMATCH`；小时结论自身不守恒报 `EVIDENCE_INCONSISTENT` 或 `EVIDENCE_UNBALANCED`。
