## Purpose

建立从标签体系、数据集、训练实验到模型准入和回滚的可重复生命周期，确保 PoC 的模型效果、成本和版本来源可验证而非依赖手工文件。

## ADDED Requirements

### Requirement: Versioned taxonomy
系统 MUST 为标签定义、业务说明、示例、风险等级、标签阈值和路由规则分配不可变版本；已发布预测必须继续引用其原始版本。

#### Scenario: Taxonomy update
- **WHEN** 负责人新增或修改标签定义
- **THEN** 系统创建新的标签体系版本且不改变历史预测的标签含义

### Requirement: Dataset lineage
每个训练或评估数据集版本 MUST 记录原始来源、生成方式、脱敏检查、去重结果、语言分布、标签分布、切分规则和内容校验值。

#### Scenario: Reproduce dataset version
- **WHEN** 团队查询一个模型版本的训练来源
- **THEN** 系统能够定位到对应数据集版本、构建配置和校验值

### Requirement: Reproducible experiment
训练运行 MUST 记录代码版本、依赖环境、随机种子、模型配置、数据集版本、指标和模型制品标识。

#### Scenario: Completed training run
- **WHEN** 训练任务成功结束
- **THEN** 实验记录包含复现实验所需的全部版本信息和模型制品地址

### Requirement: Baseline comparison
候选模型 SHALL 与至少一个轻量基线和当前已批准模型在同一冻结测试集上比较；报告 MUST 包含 micro-F1、macro-F1、每标签 precision/recall/F1、Hamming loss、人工复核覆盖率、时延和模型大小。

#### Scenario: Candidate evaluation
- **WHEN** 候选模型完成离线评估
- **THEN** 系统生成使用相同测试集和标签体系版本的对比报告

### Requirement: Promotion gate
候选模型只有在冻结测试集上达到约定准入门槛、关键高风险标签召回不低于门槛、无数据泄漏证据且性能预算达标时才 SHALL 被批准为 PoC 当前模型。

#### Scenario: Candidate fails critical recall
- **WHEN** 候选模型整体 macro-F1 达标但高风险标签召回低于门槛
- **THEN** 系统拒绝提升该模型并保留失败原因

### Requirement: Model rollback
系统 MUST 保留上一稳定模型及其阈值配置，并允许授权人员将当前模型回滚到已批准版本。

#### Scenario: Regression after promotion
- **WHEN** 当前模型出现错误率、时延或业务质检指标回归并触发回滚条件
- **THEN** 授权人员可恢复上一稳定版本且新请求使用回滚后的版本
