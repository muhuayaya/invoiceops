# InvoiceOps XLM-R 模型卡（v1）

## 当前状态

`offline_gate_passed_not_deployed`。真实 XLM-R v1/v2/v3 训练与冻结评估均已记录；v3 通过全部离线准入门槛。训练记录见 [`training-record-v1.json`](training-record-v1.json)、[`training-record-v2.json`](training-record-v2.json) 和 [`training-record-v3.json`](training-record-v3.json)，评估见对应 v1/v2/v3 报告。当前 API/Compose 镜像仍显式使用轻量 fallback，因为镜像不打包本地模型制品；v3 可在完成制品打包与 serving 配置后部署。

## 预期模型

- 基座：`xlm-roberta-base`
- 任务：八类中英双语多标签分类，sigmoid 输出，`BCEWithLogitsLoss`
- 最大长度：v1 为 256 tokens；v2/v3 为 96 tokens
- 数据版本：`invoiceops-v1-dataset-20260915`（SHA256 `b273f1571f9c42d4a10320098fd590b91e21fbb01e0d864322880ea82a55ceae`）
- 标签版本：`invoiceops-v1`
- 固定种子：42

## v3 冻结评估事实（当前离线候选）

- 训练配置：seed=42、learning rate=3e-5、10 epochs、batch size=16、最大长度 96；制品 SHA256 `bdc09ba087871035cc28ee659808fda7540457369bc115b031773bcfe9ab2878`。
- 冻结数据集：170/35/35 行，SHA256 `b273f1571f9c42d4a10320098fd590b91e21fbb01e0d864322880ea82a55ceae`；全量 `en/zh/mixed` 为 80/70/90。
- Macro-F1：1.0000；Micro-F1：1.0000；Hamming loss：0.0000；`SUPPLIER_MASTER_CHANGE` recall：1.00。
- 英文/中文/混合 Macro-F1 分别为 0.5000/0.5000/0.2500；英文/中文差值为 0。语言分组中无支持标签的零值仍保留在报告中，不能将小样本分组结果外推为真实业务收益。
- 500 次单条性能：P50 88.33ms，P95 117.65ms；Macro-F1、Micro-F1、关键召回、中英差值和 P95 全部通过门槛。
- 结论：v3 通过冻结离线准入，但尚未部署到当前 API/Compose serving；active runtime 仍保持可解释 fallback。

## v2 历史评估

v2 的 Macro-F1 为 0.8341、Micro-F1 为 0.8000，英文/中文 Macro-F1 差值为 0.0899，因此保留为 rejected 历史候选，详见 [`xlmr-evaluation-v2.json`](xlmr-evaluation-v2.json)。

## 准入门槛

Macro-F1 ≥ 0.80、Micro-F1 ≥ 0.85、`SUPPLIER_MASTER_CHANGE` recall ≥ 0.95、中英文 Macro-F1 差值 ≤ 0.08，且无 LLM 单条 P95 ≤ 800 ms。未实际测量的指标保持为空，不得推断为通过。

## 已知限制

数据主要由受控模拟工单和公开语料表达参考组成，不能代表真实企业分流收益。高风险标签始终进入人工复核；模型不执行付款、ERP 写回或供应商银行账户修改。
