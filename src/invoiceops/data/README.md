# src/invoiceops/data

训练数据契约与校验。

| 文件 | 说明 |
|---|---|
| `contract.py` | 必需字段、标签列表和分组字段定义；`load_dataset` 读取三份数据，`validate_dataset` 校验并生成 SHA-256 清单 |
| `__main__.py` | 命令行入口 |
| `__init__.py` | 对外导出 |

校验内容：字段是否齐全、标签是否合法、是否有空值、三份数据之间是否有重复文本或分组泄漏。模型训练只使用 `text` 字段。

## 使用

```powershell
uv run python -m invoiceops.data --train data/invoiceops_train.csv --dev data/invoiceops_dev.csv --test data/invoiceops_test.csv --manifest ml/data_manifest.json
```
