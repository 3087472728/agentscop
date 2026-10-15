---
name: 大模型情报抓取
description: 抓取最新大模型动态：新模型发布、GitHub 开源项目、热门论文、社区热点。当需要生成大模型情报日报，或用户询问"最近有什么新模型/新发布/社区动向"时使用本技能。
version: 1.0.0
author: admin
tags:
  - 内部
  - 大模型
  - 资讯
---

# llm-news — 大模型情报抓取

## 何时使用

- 生成《每日/每周大模型情报》日报
- 用户询问最新模型、新发布、开源项目、论文或社区讨论动向
- 需要为对比表/汇报准备近期模型事实素材

## 如何使用

在工作区 `skills/` 目录下找到本技能，用 `python3` 运行抓取脚本（纯标准库，无需安装依赖）：

```bash
python3 skills/<agent_id>/llm-news/scripts/fetch_news.py --days 2
python3 skills/<agent_id>/llm-news/scripts/fetch_news.py --days 7 --focus Qwen
python3 skills/<agent_id>/llm-news/scripts/fetch_news.py --days 2 --out reports/raw.md
```

参数：

| 参数 | 说明 |
|---|---|
| `--days N` | 回看天数（默认 2） |
| `--focus 关键词` | 高亮与关键词相关的条目（标题/摘要匹配，加 ⭐ 并置顶汇总） |
| `--out 文件` | 同时把 Markdown 保存到指定文件 |

输出为 Markdown（stdout），六个板块：**HF 新模型 / 热门论文 / GitHub 新项目 / HN 热帖 / arXiv 最新 / Reddit 热帖**。单一数据源失败会自动跳过并在末尾标注状态，不影响其他板块。

## 数据源

| 板块 | 来源 | 说明 |
|---|---|---|
| HF 新模型 | huggingface.co/api/models | text-generation 类最新发布，新模型第一现场 |
| 热门论文 | huggingface.co/api/daily_papers | 每日精选论文（按社区点赞排序） |
| GitHub 新项目 | api.github.com/search | 近 N 天新创建的 LLM 主题仓库，按 star 排序 |
| HN 热帖 | hn.algolia.com/api | 近 N 天 LLM 相关故事（带讨论热度） |
| arXiv | export.arxiv.org/api | cs.CL 最新提交 |
| Reddit | r/LocalLLaMA RSS | 社区风向（可能被限流，失败自动跳过） |

## 汇总要求（生成日报时）

1. 去重合并：同一模型/项目在多个板块出现时合并为一条
2. 每条注明来源链接；模型类条目标注参数量/许可证（若信息可得）
3. 标题带生成日期；结尾给"今日一句话点评"
4. 不确定的信息（如未官宣的传闻）明确标注"未证实"
