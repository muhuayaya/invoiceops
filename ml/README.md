# 模型说明

本目录包含 InvoiceOps 八标签分类模型的训练、评估、性能压测脚本，以及质量和容量门槛配置。训练数据见 [`data/README.md`](../data/README.md)。

## 目录结构

| 路径 | 说明 |
|---|---|
| `training.py` | 训练 TF-IDF 和 XLM-R 模型，统计分词长度 |
| `evaluate.py` | 在测试集上做最终评估，生成 `test_report.json` |
| `artifact_card.py` | 为训练好的模型生成模型卡，记录来源、依赖和文件哈希 |
| `benchmark.py` | 用开发集文本测量单个模型的推理延迟和内存 |
| `benchmark_api.py`、`benchmark_worker.py` | 在隔离的压测环境中启动接口和批量 worker |
| `capacity_loadgen.py` | 对隔离的接口做并发压测 |
| `capacity_fullstack_loadgen.py` | 对完整的六服务部署做 HTTPS 容量压测 |
| `gates/` | 质量门槛与容量门槛配置 |
| `pretrained/` | 预训练模型 `xlm-roberta-base`（不纳入版本库，需自行下载） |
| `artifacts/` | 训练产物（不纳入版本库） |

## 模型

| 模型 | 版本目录 | 角色 | 说明 |
|---|---|---|---|
| XLM-R | `artifacts/xlmr-v2` | 主模型 | 在 `FacebookAI/xlm-roberta-base` 上微调的多语言多标签分类模型 |
| TF-IDF | `artifacts/tfidf-v2` | 备用模型 | 字符级 TF-IDF 特征（3–5 字符 n-gram）加 One-vs-Rest 逻辑回归，主模型不可用时自动切换 |

`xlmr-v1`、`tfidf-v1` 是旧版本，保留用于对比和回退。

每个模型目录包含：

| 文件 | 说明 |
|---|---|
| `manifest.json` | 模型版本、标签顺序、各标签阈值和文件哈希 |
| `model_card.json` | 训练数据、参数、依赖版本等信息 |
| `dev_report.json` | 开发集评估结果 |
| `test_report.json` | 测试集评估结果（只生成一次） |
| `train_log.json` | 训练过程日志（XLM-R） |
| `model.joblib` / `model/` | 模型文件（TF-IDF / XLM-R） |

服务加载模型前会校验文件哈希、标签顺序和阈值，校验不通过的模型不会上线。

## 评估结果（测试集）

| 模型 | macro-F1 | micro-F1 | `SUPPLIER_MASTER_CHANGE` 召回率 | 中英 macro-F1 差距 |
|---|---|---|---|---|
| XLM-R `xlmr-v2` | 1.000 | 1.000 | 1.000 | 0.000 |
| TF-IDF `tfidf-v2` | 0.997 | 0.997 | 1.000 | 0.005 |

## 准备预训练模型

从 HuggingFace 下载 [`FacebookAI/xlm-roberta-base`](https://huggingface.co/FacebookAI/xlm-roberta-base)（提交 `e73636d4f797dec63c3081bb6ed5c7b0bb3f2089`，MIT 许可），放到 `ml/pretrained/xlm-roberta-base/`。该目录需包含 `config.json`、`model.safetensors`、`sentencepiece.bpe.model`、`tokenizer.json`、`tokenizer_config.json`。

## 训练

在项目根目录运行。每次训练都使用一个新的、尚不存在的输出目录。

```powershell
# TF-IDF 备用模型
uv run python -m ml.training train-tfidf --train data/invoiceops_train.csv --dev data/invoiceops_dev.csv --test data/invoiceops_test.csv --output ml/artifacts/tfidf-new

# XLM-R 主模型
uv run python -m ml.training train-xlmr --train data/invoiceops_train.csv --dev data/invoiceops_dev.csv --test data/invoiceops_test.csv --base-model ml/pretrained/xlm-roberta-base --base-revision e73636d4f797dec63c3081bb6ed5c7b0bb3f2089 --output ml/artifacts/xlmr-new

# 生成模型卡
uv run python -m ml.artifact_card --model ml/artifacts/xlmr-new --base ml/pretrained/xlm-roberta-base --base-revision e73636d4f797dec63c3081bb6ed5c7b0bb3f2089
```

训练命令会先校验三份数据，只用训练集拟合，用开发集选择检查点和各标签阈值，不会读取测试集结果。常用参数：`--seed`（默认 42）；TF-IDF 的 `--max-features`、`--c`；XLM-R 的 `--max-length`（默认 256）、`--epochs`（默认 3）。当前依赖为 CPU 版 PyTorch，XLM-R 在 CPU 上训练三轮约需 1 小时。

## 测试集评估

```powershell
uv run python -m ml.evaluate --model ml/artifacts/xlmr-new --train data/invoiceops_train.csv --dev data/invoiceops_dev.csv --test data/invoiceops_test.csv --frozen-gate ml/gates/engineering-quality-v1.json
```

评估前需要先在门槛文件中登记该模型版本。结果写入模型目录下的 `test_report.json`。如果该文件已存在，命令会拒绝执行：每个模型版本只评估一次，不根据测试结果调参。

## 门槛

| 文件 | 内容 |
|---|---|
| `gates/engineering-quality-v1.json` | 质量门槛：macro-F1 ≥ 0.80，micro-F1 ≥ 0.85，`SUPPLIER_MASTER_CHANGE` 召回率 ≥ 0.95，中英 macro-F1 差距 ≤ 0.08 |
| `gates/engineering-capacity-v1.json` | 完整容量门槛：六服务部署下的并发、吞吐、延迟、错误率、资源占用和持续运行稳定性要求 |
| `gates/engineering-capacity-short-v1.json` | 短时容量检查：并发 1/2/3，适合约 3 个同时在线用户的小规模部署 |

## 性能压测

压测由项目根目录的 PowerShell 脚本启动。脚本会创建独立的临时 Compose 环境，完成后自动清理，压测报告写入 `ml/benchmarks/`。

```powershell
# 只压测接口
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_compose_capacity_benchmark.ps1

# 完整六服务短时检查
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_fullstack_capacity_benchmark.ps1 `
  -GatePath ml/gates/engineering-capacity-short-v1.json `
  -BatchConcurrency 3 -BatchRows 10 -BatchTimeoutSeconds 180
```

完整六服务压测期间，会临时停止当前运行的 `invoiceops` 服务，结束后再恢复，请在维护时间运行。
