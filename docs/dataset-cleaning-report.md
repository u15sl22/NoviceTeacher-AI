# 数据集只读清洗报告

生成时间：2026-10-08T04:48:46.838901+08:00  
清洗版本：`clean-v2`

## 边界

- 原始教案和原始 JSON 均以只读方式处理，运行前后 SHA-256 必须一致。
- 未连接或写入应用数据库；未调用任何外部模型。
- 所有记录保持 `unreviewed / not_verified`。相似、模板和泄漏均为待人工检查的候选标记，不表示正确、错误或已核验。

## 总览

| 项目 | 数量 |
|---|---:|
| 教案 | 41 |
| 原始批注 | 1118 |
| 产生格式标准化派生值的批注 | 1091 |
| 原始维度可按精确别名映射 | 1118 |
| 批注文本疑似已混入原教案 | 0 |
| 使用派生 DOCX完成检查的旧 DOC | 17 |
| 使用本地 OCR完成检查的扫描 PDF | 5 |
| 原始字节完全重复组 | 0 |
| 同教案标准化重复组 | 0 |
| 跨教案内容完全相同组 | 0 |
| 疑似近似重复组 | 0 |
| 跨至少3份教案的模板候选组 | 0 |
| 疑似重复教案正文组 | 0 |

### 教案来源定位状态（按教案）

| 值 | 数量 |
|---|---:|
| resolved | 41 |

### 正文提取状态（按教案）

| 值 | 数量 |
|---|---:|
| extracted | 19 |
| extracted_from_derived_docx | 17 |
| extracted_from_ocr | 5 |

### 原文引用定位状态

| 值 | 数量 |
|---|---:|
| exact_unique | 355 |
| not_found | 286 |
| format_only_unique | 207 |
| excerpt_segments_found | 184 |
| exact_multiple | 45 |
| absence_claim | 34 |
| format_only_multiple | 7 |

### 机械检查标记

| 值 | 数量 |
|---|---:|
| source_location_needs_review | 338 |

### 原始维度分布

| 值 | 数量 |
|---|---:|
| 过程设计 | 130 |
| 教学内容 | 110 |
| 教学目标 | 92 |
| 教学方法 | 92 |
| 评价反馈 | 91 |
| 教学资源 | 90 |
| 课堂互动 | 90 |
| 文档规范 | 87 |
| 教学理念 | 86 |
| 课程思政 | 84 |
| 版式观感 | 84 |
| 学情分析 | 82 |

## 详细结果

详细派生记录保存在 `C:/Users/13548/Desktop/NoviceTeacher-AI/.runtime/dataset-cleaning`：

- `cleaned_annotations.jsonl`：逐条标准化结果与完整来源追踪；
- `lesson_profiles.csv`：41份原教案的格式、解析状态和候选标准化元数据；
- `duplicate_groups.csv`：确定性重复组；
- `near_duplicate_candidates.csv`：只供人工复核的相似组；
- `template_candidates.csv`：跨教案重复文本候选；
- `source_issues.csv`：无法唯一定位或暂不支持解析的记录；
- `manifest.json`：输入输出哈希和运行边界。

派生 DOCX和 OCR文本只用于机械定位，不替代原始文件，也不表示内容已经人工核验。仍未取得可检查正文的二进制文件数量为 0；OCR识别误差可能使部分引用进入 `not_found`。怎样定义人工评价标准仍需另行决定。
