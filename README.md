# 中药企业财务智能问答系统 

> 基于 LLM Agent + RAG 的中药上市公司财务分析智能问答系统 
> 开发周期：2026年4月 — 至今

---

## 项目简介

自然语言驱动的中药企业财务分析助手。用户用中文提问，系统自动理解意图、从 **MySQL 查财报数据**、从 **RAG 索引搜研报**，返回结构化答案。

**可问的问题：**
- *"2024年营收排名前五的中药企业"* → MySQL
- *"片仔癀近三年净利润趋势"* → MySQL
- *"同仁堂有什么经营风险"* → MySQL 风险规则分析
- *"中药行业研发投入趋势"* → RAG 研报

---

## 数据流（两套独立管线）

```
┌─ 财报PDF ─→ pdf_extractor/ ─→ JSON ─→ MySQL（2667行 4张表）──→ sql_tool ─┐
│ │
│ Agent ─── 意图识别 ─── 多步工具调用 ───→ 答案 │
│ │
└─ 研报MD ─→ rag_pipeline/ ─→ FAISS+BM25 索引 ────────→ rag_tool ──────────┘
```

### 管线一：财报提取 → MySQL（已完成 ）
- PDF 财报 → `pdf_extractor/`（14个模块）→ 结构化 JSON → 写入 MySQL
- MySQL 已有 **2667 行数据**（资产负债表/利润表/现金流量表/核心指标）
- `sql_tool`、`risk_tool`、`report_tool` 都直接查 MySQL

### 管线二：研报检索 → RAG（代码就绪，索引需本地重建 ）
- 研报 MD → `rag_pipeline/`（文档清洗+语义分块）→ FAISS + BM25 索引
- `rag_module.py` 封装了双路检索+RRF融合+Reranker
- 研报原文和索引文件未上传 GitHub，需本地重建

---

## 各模块实际状态

### 完整可用
| 模块 | 说明 |
|------|------|
| `models/model2/pipeline.py` | NER+Model2+NL2SQL 查询管线，checkpoint权重未上传 |
| `models/ner_model/` | BertForTokenClassification，路径不存在时会跳过 |
| `models/model1_schema_linking/` | Schema链接（字段→表映射），权重未上传 |

### 有隐患
- `session.py` — 空文件 0 字节
- `run_app.py` — 通过 .pyc 字节码加载（workaround）
- 数据库密码硬编码 `root/<DB_PASSWORD>`
- RAG索引和研报原文不在仓库中，需 `rag_pipeline/build_index.py` 重建

---

## 技术栈

| 组件 | 选型 |
|------|------|
| Web框架 | Flask |
| 数据库 | MySQL（PyMySQL + mysql-connector） |
| LLM | Ollama（本地部署 bge-m3） |
| 向量检索 | FAISS + BM25 |
| NER | BERT + BIO序列标注 |
| 可视化 | ECharts |
| PDF提取 | pdfplumber + 自定义规则 |

---

## 本地启动（需先准备环境）

```bash
# 1. 安装依赖
pip install flask pymysql mysql-connector-python faiss-cpu numpy requests

# 2. 准备 MySQL（需要本机有 MySQL 服务）
# 数据库名：finance_data
# 账密：root / <DB_PASSWORD>

# 3. 导入财报数据
python pdf_extractor/import_db.py

# 4. 重建 RAG 索引（如果要用研报搜索）
python rag_pipeline/build_index.py

# 5. 启动 Ollama
ollama pull bge-m3

# 6. 运行
python app.py
# 访问 http://127.0.0.1:7860
```

---

## 开发札记

### Day 1：项目骨架搭建

完成了最核心的几块基础设施：

**Agent 引擎（完整可用）**
- ReAct 风格的多步推理 Agent，支持 7 类任务分类（query/analysis/risk/report/compare/trend/chart）
- 6 个工具全部就绪：SQL查询、RAG研报搜索、数据处理、图表生成、风险预警、报告生成
- 其中 risk_tool 实现了 6 条财务风险检测规则（负债率/现金流/应收账款/库存/盈利下滑/ROE）
- report_tool 能生成六段式完整财务分析报告

**PDF 财报提取管线（完整可用）**
- 14 个模块组成完整 pipeline，支持上交所 + 深交所两种财报格式
- 字段映射 + 数值归一化（一亿八千万→180000000）+ LLM 回退兜底
- 已导入 MySQL 2667 行数据（4 张表）

**RAG 研报检索（完整可用）**
- rag_module.py（21KB）实现了 FAISS + BM25 双路检索 + RRF 融合排序 + Reranker
- 从研报 MD 建立向量索引，支持按公司/年份预过滤

**模型模块（骨架就绪，权重需本地训练）**
- model2/pipeline.py（16KB）整合了 NER → Model2 → time_parser → schema_mapping → SQL 生成
- inference_model2.py 已 finetune 但 checkpoint 权重未上传，会 fallback 到 bart-base-chinese
- ner_model 同理，路径不存在时跳过

> **踩坑**：session.py 是空文件 0 字节，之前完全没发现。
> run_app.py 通过 .pyc 字节码加载，是个 workaround。
> 数据库密码 root/<DB_PASSWORD> 直接硬编码在代码里了。

---

### Day 2：对话连续性升级

解决了最影响体验的问题：**追问断连**。

**问题**：用户问完片仔癀净利润是多少，再问那营收呢，系统会回答请提供公司名称——体验很差。

**方案**：不搞复杂 Memory，只保留 5 个核心槽位（company/indicator/period/task_type/last_result），追问时自动继承上一轮。

**实现**：
- 新增 FollowupResolver 独立类，统一处理 6 种追问类型（chart/report/rank/indicator/analysis/normal）
- session.py 砍掉了原来分散的 is_followup() 和 merge_question()，改为 4 个干净 getter
- agent.py 的 _handle_followup() 从 80 行精简到 30 行

**示例**：
- 那营收呢 → 继承 company=片仔癀，只换 indicator
- 为什么增长这么快 → 自动触发 sql_query → rag_search 分析链路
- 画图 → 用上一轮数据直接生成图表

> **心得**：这个改动其实不大，但对用户感知提升最明显。会很多功能和真的像一个财务分析助手的差别就在这里。

---

### Day 3：单位检测全链路可追溯 + 数据清洗

终于把单位这个老大难问题彻底解决了。

**问题**：PDF 提取时 detect_unit() 没找到单位声明就用默认值 1（元），但 PDF 实际用的是万元，导致数值差 10000 倍。异常数据混在库里，没人知道哪些是错的。

**方案**：全链路可追溯。

- value_normalizer 的 detect_unit_above() 从返回一个数字改为返回（倍率, 单位名, 原文出处）
- 单位信息从 table_extractor 贯穿到 JSON，再到数据库
- import_db 重构为 DataImporter 类，集成 pending_review 自动检测机制
- 数值 > 1 亿且单位声明为元时，自动写入 pending_review 表等待人工审核
- 支持 --review 参数交互式审核

**结果**：异常数据从 722 条降为 0，全部审核完成，四张表单位统一为万元。

> 踩坑：一开始以为 normalize() 写错了，查了两天才发现是 detect_unit() 没扫到页脚的声明。pdfplumber 提取的文本不完整，页眉页脚经常被截断。

---


### Day 4: Agent架构重写 + KG Resolver + sql_tool重构 + 代码清理

Day 3做完单位清洗后，发现了一个更大的结构问题。

**问题**: 系统功能越来越多，但Agent核心还是最初的ReAct风格。pipeline理解完意图后，Agent不信任，自己又做一遍分类，两套逻辑经常打架。sql_tool也不信任上游，自己做字段匹配而且只认三个主表。最直接的后果: 盈利能力对比查询返回空了N次，debug了两天才追到根因。

**Agent层改动(重写)**:
- 删除了TaskType枚举、SYSTEM_PROMPT、_classify()、_check_slots()、_planner()，旧ReAct骨架全部砍掉
- 删除了原来的_run_agent_loop()多轮LLM调用，改为_execute_plan()确定性执行
- agent.query()简化为: 追问检测 -> 多意图拆分 -> 单意图执行 -> 结果合并
- INTENT_PLANS硬编码6种计划，不再由LLM规划

**KG Resolver(新增)**:
- 250+同义词映射: 税后利润->净利润、毛利->毛利率等
- 8个概念组层级展开: 盈利能力->[毛利率,净利率,ROE,加权ROE]等
- 插入位置: pipeline.understand()之后、plan之前，作为Step 1.5

**sql_tool重构(从170行扩到630行)**:
- 新增列缓存: 启动时一次性加载information_schema
- 删除硬编码3表过滤，改为_column_exists动态检查
- _resolve_metric_with_fallback(): 字段回退链
- _execute_with_retry(): 4级降级重试(完整->去period->去year->全库)
- _query_compare()重写: 不再硬编码表名交叉查询
- _query_trend(): 新增pipeline兜底+字段回退标记

**followup_resolver.py(重写)**:
- 独立FollowupResolver类，6种追问类型
- resolve()返回结构化结果
- 追问类型智能识别: 画图->chart、为什么->analysis、第二名->rank

**session.py(重写)**:
- 删除is_followup()、merge_question()、has_previous_context()
- 改为4个干净getter
- slots从字典改为dataclass SlotState

**pipeline.py(修改)**:
- understand()新增概念组检测: 匹配到概念组时保留原文，不提前替换
- 交给KG Resolver在Step 1.5统一展开

**sql_generator.py/time_parser.py(修改)**:
- sql_generator: 优化多指标SQL生成，支持comparison意图
- time_parser: 重写中文时间解析

**RAG(修改)**:
- rag_module.py: _tokenize_chinese()从单字改为jieba分词，BM25召回提升

**前端(修改)**:
- static/style.css: 新增样式
- static/script.js: 交互优化

**新增文件**:
- agent/kg_resolver.py、intent_splitter.py、result_merger.py
- data/附件7.png

**删除的废弃代码**:
- analysis/(3个文件)、model1_schema_linking/(20+文件)
- run_app.py、空__init__.py、测试脚本

**其他**:
- .gitignore增加调试脚本排除模式
- app.py适配新Agent接口

> **踩坑**: 这个改动最大的教训是模块间信任问题。pipeline理解完->KG Resolver展开->sql_tool执行，这条链路里只要有一个环节不信任上游就会出bug。debug了两天一直在质疑KG Resolver，最后发现是sql_tool自己过滤了。结论: 每个模块只做自己的事，下游信任上游，出问题只查出错的那个环节。

---
*每次开发完执行 \git add .\ + \git commit -m " Day X: 改动说明"\ + \git push\ 继续记录*
