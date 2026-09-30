# src/invoiceops/application

与框架无关的业务规则，接口和 worker 共用。

| 文件 | 说明 |
|---|---|
| `classifier.py` | `redact_text` 在存储和推理前屏蔽常见身份信息；`classify` 调用主模型，失败时切换备用模型，按阈值得出标签和复核原因 |
| `batch.py` | `parse_batch` 校验 UTF-8 CSV：必需 `text` 列、可选 `external_id` 列，文件不超过 10 MiB、最多 10,000 行、单行不超过 10,000 字符 |

## 复核原因

| 原因 | 条件 |
|---|---|
| `supplier_master_change` | 命中供应商主数据变更 |
| `no_label` | 没有标签达到阈值 |
| `low_confidence` | 任一标签概率与阈值相差不超过 0.05 |
| `model_fallback` | 由备用模型完成分类 |

有任一原因时状态为 `pending_review`，否则为 `not_required`。两个模型都不可用时抛出 `ModelUnavailable`。
