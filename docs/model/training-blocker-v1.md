# XLM-R 训练阻塞记录（v1）

历史记录：2026-09-15 尝试安装锁定的 `torch==2.5.1` 与 `transformers==4.47.1`，运行时安装成功；随后尝试下载 `hf-internal-testing/tiny-xlm-roberta`，仅取得 tokenizer/config 文件，没有对应权重；另一个候选 `sshleifer/tiny-xlm-roberta` 返回 Hugging Face 401/不存在。

状态更新：2026-09-16 已从 `FacebookAI/xlm-roberta-base` 下载可加载的 MIT 权重，来源 revision、文件校验值和许可证记录在 `docs/model/model-source-manifest-v1.json`。原“无权重”阻塞已解除；v1、v2 和 v3 训练、误差分析与 500 次冻结评估均已完成。

数据语言识别和切分覆盖已修正并重新生成数据集（SHA256 `b273f1571f9c42d4a10320098fd590b91e21fbb01e0d864322880ea82a55ceae`）；v1/v2/v3 均按固定种子训练。v2 是历史 rejected 候选；v3 使用 lr=3e-5、10 epochs、batch=16，在同一冻结评估器上达到 Macro-F1/Micro-F1 1.0000、`SUPPLIER_MASTER_CHANGE` recall 1.0、中英 Macro-F1 差值 0、P95 117.65ms，完整结果在 `docs/model/xlmr-evaluation-v3.json`。

模型离线准入阻塞已解除；真实企业数据仍需另行试点验证。v3 尚未部署，因为当前 serving 镜像不包含本地模型制品，fallback 仍是唯一 active runtime；部署前必须完成制品打包、serving 配置和回滚验证。
