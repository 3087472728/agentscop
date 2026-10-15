---
name: weekly_report
description: 生成结构化周报。当用户要求撰写周报/周总结时使用：准备数据 JSON，调用本技能的渲染脚本产出 Markdown 周报，然后向用户报告输出位置。
---

# 周报生成

## 何时使用

用户要求生成周报、周总结、weekly report 时使用本技能。

## 技能文件布局

```
weekly_report/
├── SKILL.md                    # 本文件
├── templates/weekly_report.md  # 周报模板（{placeholder} 占位）
└── scripts/render_report.py    # 渲染脚本（纯标准库）
```

## 操作步骤

1. 与用户确认本周关键信息（如缺失则先询问）：
   - `week`（如 2026-W39）、`author`、`done`（已完成事项列表）、
     `next`（下周计划列表）、`risks`（风险与阻塞，可为空）。
2. 将数据整理为 JSON 文件，例如工作区内 `report_data.json`：

```json
{
  "week": "2026-W39",
  "author": "张三",
  "done": ["完成A模块", "修复B问题"],
  "next": ["启动C调研"],
  "risks": ["D依赖未就绪"]
}
```

3. 运行渲染脚本（路径相对本技能目录）：

```bash
python scripts/render_report.py --data report_data.json --out weekly_report_2026-W39.md
```

- 模板默认取本技能 `templates/weekly_report.md`，可用 `--template` 覆盖。
- 脚本将 `{week}`、`{author}` 等占位符替换为数据字段；列表字段渲染为
  Markdown 无序列表；未提供的字段保持占位符原样并输出警告。

4. 检查生成的 `weekly_report_*.md`（用 Read 工具），确认无遗留
   占位符后，向用户报告文件位置与内容摘要。

## 注意事项

- 数据文件与输出文件都写在工作区当前目录，不要写入技能目录内部。
- 一次只渲染一份周报；批量需求请逐份执行。
