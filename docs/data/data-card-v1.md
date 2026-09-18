# InvoiceOps v1 数据卡

## 用途与边界

这是用于验证数据管线、评估器和模型生命周期的 PoC 数据集，不是企业历史工单，也不能证明真实业务收益。最终标签数据来自受控模板和人工审核候选；公开语料只提供语言表达参考。

## 来源与许可

来源、URL、许可、用途限制和文件校验值记录在 [`ml/data/source_manifest.json`](../../ml/data/source_manifest.json)。默认候选来源包括 BANKING77（CC BY 4.0）和 Chinese-Ambiguous-Reference（MIT）；它们的原始意图标签不能直接作为 InvoiceOps 真值。

## 处理与脱敏

- 文本执行 Unicode NFKC、空白规范化和中英混合语言识别。
- 邮箱、手机号、IBAN/银行账号、发票号和模拟供应商标识被规则替换。
- 模型/LLM 边界只接收脱敏文本；未知元数据字段被丢弃。
- 所有训练候选必须有人工审核状态；LLM 或自动映射不能直接产生训练真值。

## 标签和切分

标签体系为 `invoiceops-v1` 八类标签。场景矩阵和模板版本在 [`ml/data/scenario_matrix.json`](../../ml/data/scenario_matrix.json)。数据按模板、翻译、近重复和供应商上下文组进行目标为 70/15/15 的分组切分；由于硬泄漏组边界，本数据实际为 170/35/35（约 70.83%/14.58%/14.58%），且 train/dev/test 均覆盖八类标签，冻结测试集不参与调参。目标比例、seed、算法和制品 SHA256 记录在 [`invoiceops-v1-coverage.json`](invoiceops-v1-coverage.json)。

覆盖、语言分布、组合分布和实际切分比例见 [`invoiceops-v1-coverage.json`](invoiceops-v1-coverage.json)。当前全量语言分布为英文 80、中文 70、中英混合 90；train/dev/test 均保留三种语言，冻结测试集为英文 15、中文 15、中英混合 5。冻结挑战集 16 条覆盖英文 9、中文 5、中英混合 2，并覆盖八类标签。
冻结挑战集及双人复核字段见 [`challenge-set-v1.csv`](challenge-set-v1.csv)。

## 已知偏差与禁用用途

数据以模拟发票场景为主，可能存在模板语言单一、标签边界被人为强化和中文/英文表达分布不代表真实企业的偏差。不得用于自动付款、供应商银行账户修改、生产 SLA 承诺或宣称真实分流收益。
