# _specs — 通用绑定 spec 目录(2.0 流程)

**铁的规矩: 绑定新文书不再写 `_bind_*.py` 脚本。一份文书 = 一份 spec(JSON) + 通用执行器。**

## 标准流程(与 1.0 流程一致, 只是绑定动作改为 spec 描述)

1. `python _bindrun.py scan <模板id>` — 拉模板打印结构大纲+已绑统计(写 spec 前必做, 代替试探脚本)
2. 从本目录选家族模板复制 → 改 `template_id` / `backup_tag` / 案号占位, 按 scan 结果调整 steps
3. `python _bindrun.py run <spec.json> --dry` — 全程演练不保存, 必须 0 警告
4. `python _bindrun.py run <spec.json>` — 正式执行: 顶层备份+prebind 备份→绑定→□拆分/横线/字体审计→保存(带顶层页边距)→回拉核对
5. 核对 PASS 后 `_shot.py` 截图肉眼比对(同 1.0)
6. 清单 Excel 打完成标记(同 1.0)

## 文件

| 文件 | 用途 |
|---|---|
| `送达回证-家族模板.json` | 40 份送达回证同构文书, 改 id 即用 |
| `骨架-通用模板.json` | 全部 op/ctrl/区块参数示例, 复制裁剪 |

## steps op 速查

| op | 关键参数 | 说明 |
|---|---|---|
| `assert` | text | 前置断言: 文字必须存在 |
| `bind_after` | anchor, ctrl(可列表), skip, nth | 锚点后空位放控件(列表依次占空位) |
| `bind_occurrence` | anchor, occ, ctrl | 第 occ 次出现的锚点后绑定 |
| `replace_line` | anchor, parts, keep_anchor, nth | 整行替换; parts 末尾一般放 `{"newline":true}` |
| `replace_range` | start_anchor, end_anchor, keep_end, parts | 区间重建(区块级重排) |
| `set_text` | anchor, text, nth | 改文字保留样式 |
| `clear_after` | anchor, count | 清锚点后 N 个元素(案号散字) |
| `table_cell` | tr, col, parts / clear | 表格单元格填控件/清空 |
| `table_assert` | tr, col, contains | 表格单元格文字断言 |
| `block` | name, args | 调 `_blocks.py` 固定组件区块 |
| `kit` | method, args | 直调 BindKit 任意方法 |

## ctrl 速写(parts/ctrl 通用)

| 写法 | 产物 |
|---|---|
| `{"meta":"personName"}` | 按在线元数据自动构建(推荐, 铁律1) |
| `{"field":"docNo","label":"案号","ctype":"serial/input/textarea/number/date","placeholder":..,"with_time":..}` | 手工构建控件 |
| `{"unbound":"input/textarea/number/date/select/checkbox/signature/stamp"}` | 未绑定组件 |
| `{"select":{...}}` `{"checkgroup":{...}}` `{"sig":{...}}` | 下拉/复选组/绑定签名 |
| `{"text":" 至 ","size":16,"rowflex":"center","tail":"\n"}` | 文本元素 |
| `{"newline":true}` `{"blank":true}` | 换行 / 空串 |

## 固定组件区块(_blocks.py, `python _bindrun.py blocks` 查看)

`case_no` 案号 | `time_range` 时间起止 | `officer_lines` 执法人员N行 | `signature_line` 精简签名行 |
`stamp_date` 印章区 | `remark_textarea` 备注 | `delivery_mode` 送达方式6□ | `delivery_receipt` 送达回证整表 |
`party_slot` 当事人槽位角色路由(铁律2) | `approval_chain` 审批链 | `ymd_replace` 年月日占位

**遇到 spec 表达不了的新套路: 把套路沉淀成新 block 加进 `_blocks.py` 供以后复用, 仍然不写单文书脚本。**
