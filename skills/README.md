# 公司内部技能库（internal_skills/）

本目录是**公司内部技能上架区**：每个子目录就是一个技能，放入即上架、
删除即下架，**无需重启服务**（前端技能市场 → "公司内部技能库"来源即可看到）。

## 上架一个技能

```text
internal_skills/
  <技能id>/            # 子目录名即技能 ID，建议英文小写-连字符
    SKILL.md           # 必需
    scripts/...        # 可选：技能附带脚本、资料等，随安装一起进入工作区
```

`SKILL.md` 格式（frontmatter + 正文）：

```markdown
---
name: 技能展示名
description: 一句话说明这个技能做什么（列表页展示用）。
version: 1.0.0
author: 部门/作者
tags:
  - 内部
  - 规范
---

# 技能正文

给智能体看的指令、流程、注意事项……
```

## 权限

默认**所有登录用户**可浏览、安装。如需只开放给部分用户，在
`app_factory.py` 注册时传入白名单：

```python
InternalSkillHub(allowed_users={"zhangsan", "lisi"}),
```

将来接入角色系统时改 `internal_skill_hub.py` 的 `_check_user` 即可。

## 将来接入云效 skills 库

`hub_id="internal-skills"` 是已安装技能记录的外键，**保持不变**。
云效库就绪后，把 `internal_skill_hub.py` 中 `_scan / _meta / _pack`
三个数据访问方法改为调用云效 API（或新写 YunxiaoSkillHub 替换注册），
前端与用户已装技能零改动。详见模块文件头部说明。
