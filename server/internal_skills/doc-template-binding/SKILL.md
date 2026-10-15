---
name: 文书模板字段绑定
description: 辽宁省交通运输执法平台（cctef-case-service）文书模板的字段绑定实战指南。当需要在执法平台上为文书模板绑定字段、排查绑定问题时使用。
version: 2.1.0
author: admin
tags:
  - 内部
  - 执法平台
  - 文书模板
---

# Skill：交通执法文书模板字段绑定（doc-template-binding）

> 适用范围：辽宁省交通运输执法平台（cctef-case-service）任意文书模板的字段绑定。
> 2.0 变更（2026-09-23）：固化已完成 70 份文书的绑定套路，沉淀为**通用区块库 + spec 驱动执行器**。
> **绑定新文书不再写 `_bind_*.py` 单文书脚本**（这是 1.x 执行慢的根源：每份文书都要现场写脚本、
> 反复试锚点、来回跑）。现在一份文书 = 一份 spec(JSON)，固定流程自动执行，速度快且结果可核对。
> 2.1 变更（2026-09-23）：新增**联动规则铁律 7**（当事人区表格 linkGroup/linkTrigger）+《抽样取证凭证》实战记录。

## 工作路径（与 1.x 一致，不改变）

- 工作目录：`/home/agentrunner/references/hyq/traffic-master-5565698aa03ac0b4177ab858d7873cf85fe1e14e/doc2template`
  （下称 doc2template；用系统 `python3` 运行，token 缓存 `_sysmgmt_token.json`，字段快照 `_field_meta.json`）。
- 本技能 `scripts/` 目录带通用引擎副本（`_bindrun.py`、`_blocks.py`、`_specs/`）；
  若工作区没有 doc2template，把 `scripts/` 内容拷到工作目录即可，用法相同。
- 登录方式不变：优先缓存 token（`_load_token()`），失效自动走 `export_frontend.ensure_token()` OCR 登录。

## 2.0 标准流程（spec 驱动，禁止再写单文书脚本）

1. **扫描**：`python3 _bindrun.py scan <模板id>` —— 拉模板打印结构大纲 + 已绑统计。
   代替 1.x 时代的试探脚本，写 spec 前必做。
2. **选家族 spec**：到 `doc2template/_specs/` 找结构最接近的模板复制改名。
   - 送达回证家族（已完成 40 份同构）→ `送达回证-家族模板.json`，通常只改 `template_id`/`backup_tag`/案号占位。
   - 其他文书 → `骨架-通用模板.json` 裁剪；op/ctrl/区块参数速查见 `_specs/README.md`。
3. **演练**：`python3 _bindrun.py run <spec.json> --dry` —— 全流程演练但**不保存**。
   必须 0 警告（警告=锚点没找到/空位不足，按 scan 大纲修 spec 再演练）。
4. **正式执行**：`python3 _bindrun.py run <spec.json>` —— 自动完成与 1.x 完全一致的流水线：
   顶层全量备份（workspaces/hyq/traffic-bind/backups）→ BindKit prebind 备份 → 绑定 →
   □拆分/日期残留清理/横线/字体审计/None 检查 → 保存（自动带顶层页边距）→ **回拉核对**（统计绑定数、
   逐字段核对 `verify.expect_bind`，FAIL 会退出码 1）。
5. **截图肉眼比对**：`_shot.py` 截图确认版式（同 1.x）。
6. **更新清单**：在《正式文书清单》Excel 打完成标记（同 1.x）。

**禁令**：不要再为单份文书编写 `_bind_*.py` 全流程脚本。
spec 表达不了的新套路 → 沉淀成新区块函数加进 `_blocks.py`（签名 `fn(bk, run, **args)`，登记进 `BLOCKS`），
spec 里用 `{"op":"block","name":...}` 调用——新套路必须可复用，仍然不写单文书脚本。

## 固定组件区块库（_blocks.py，`python3 _bindrun.py blocks` 查看清单）

| 区块 | 用途 |
|---|---|
| `case_no` | 案号 serial 行（自动清残留散字、右对齐） |
| `time_range` | 时间起止行：标签+起(DATETIME)+至+止(DATETIME) |
| `officer_lines` | 执法人员N + 执法证号N 行 |
| `signature_line` | 精简式签名行：标签+signature+systemDate（用户确认标准，不放姓名框与"（签名或者盖章）"文字） |
| `stamp_date` | 印章区：机关名（继承原字体，严禁假设）+stamp+日期绑 systemDate（主层级或表格 r/c） |
| `remark_textarea` | 备注：保留标签+未绑定多行文本 |
| `delivery_mode` | 送达方式 6□ 标准重建（未绑定复选框，手勾） |
| `delivery_receipt` | 送达回证家族整表绑定（案号/案由/送达单位/受送达人/代收人/送达方式） |
| `party_slot` | 当事人区/委托区槽位按角色前缀路由字段（铁律 2），路由不到才落未绑定输入框或报错 |
| `approval_chain` | 审批链 level1-4：审批人/审批时间(DATETIME)/审批意见(TEXTAREA)/签名 |
| `ymd_replace` | "年 月 日"占位文字 → date 控件（完整替代） |

## spec 速查（完整示例见 _specs/）

- **op**：`assert` 文字必须存在 | `bind_after` 锚点后空位放控件 | `bind_occurrence` 第N次出现 |
  `replace_line` 整行替换 | `replace_range` 区间重建 | `set_text` 改文字保留样式 |
  `clear_after` 清锚点后N元素 | `table_cell` 表格格填控件 | `table_assert` 表格格断言 |
  `block` 调区块 | `kit` 直调 BindKit 方法。
- **ctrl 速写**（parts/ctrl 通用）：`{"meta":"personName"}` 按在线元数据自动构建（推荐）|
  `{"field":"docNo","ctype":"serial|input|textarea|number|date",...}` |
  `{"unbound":"input|textarea|number|date|select|checkbox|signature|stamp"}` |
  `{"select":{...}}` `{"checkgroup":{...}}` `{"sig":{...}}` |
  `{"text":" 至 ","size":16,...}` `{"newline":true}` `{"blank":true}`。
- **收尾开关**：`split_checkboxes`/`clean_date_residual` 默认开；`underlines`+`fit_underlines`
  横线宽；`font_map` docx run 级字体回填；`margins` 顶层页边距覆盖（默认沿用模板现值）；
  `verify.expect_min_bindings` / `verify.expect_bind` 回拉核对。

## 铁律（2026-09-23 漏检事故沉淀，2.0 依然全部生效）

1. **字段存在性判定必须实时调接口，禁止只查本地快照。**
   绑定/检测前必须先调：
   `GET http://150.223.194.200:31305/api/java/api/cctef-case/case-filing-field-meta/listAll`
   取最新字段清单。本地 `_field_meta.json`（doc2template 目录）只是缓存快照，
   后端会持续新增字段——2026-09-23 快照 280 条 vs 在线 316 条，少的 35 条里
   恰好包含委托区全部 `agent*` 前缀字段和 `statementDefensePurpose`，
   导致委托区 6 格被误判为"无现成字段，推荐手填"。
   判定流程：先拉在线清单 → 按角色前缀路由匹配 → 都没有才判"无现成字段"。
   **2.0 落地方式**：执行器在 `run` 时自动在线刷新清单（`refresh_meta` 默认开，
   `python3 _bindrun.py meta` 可手动刷新）；`party_slot`/`ctrl_from_meta` 只认清单里存在的字段。

2. **字段路由按角色前缀，同义槽位先找专属前缀字段，不要拿通用字段硬凑或直接判无。**
   平台案件模型给不同角色区块建了带前缀的专属字段：

   | 角色 | 前缀/字段 | 字段示例 |
   |---|---|---|
   | 当事人（个人） | `person*` | personName / personGender / personAge / personPhone / personAddress / personIdCard |
   | 当事人（单位） | `company*` / `enterprise*` | companyName / companyAddress / companyPhone / enterpriseCreditCode |
   | 法定代表人 | `legalRep*`（姓名=legalPerson） | legalRepGender / legalRepAge / legalRepWorkUnit / legalRepPhone / legalRepAddress / legalRepCertInfo |
   | 委托代理人 | `agent*`（姓名=agentName） | agentNameGender / agentAge / agentNameWorkCompany（工作单位）/ agentPhone / agentNameWorkCompanyAddress / agentRepCertInfo（证件类型（号码））/ agentStartDate |
   | 委托人/违法行为人 | `nameIllegalActName*` | nameIllegalActNameName（名称）/ nameIllegalActNamePersonnel（签章）/ nameIllegalActNameSignature（签名）；受委托人签名=nameSignature |
   | 陈述申辩 | `statementDefense*` | statementDefensePurpose（目的）/ statementDefenseReason（事实和理由） |

   ⚠️ 注意命名不完全规整：委托代理人性别=`agentNameGender`（非 agentGender）、
   工作单位=`agentNameWorkCompany`（非 agentCompanyName）、证件=`agentRepCertInfo`。
   以在线接口返回为准，勿凭猜测拼写。
   **2.0 落地方式**：`party_slot` 区块内置 `ROLE_PRESETS`（person/company/legalRep/agent/委托人），
   spec 里 `{"op":"block","name":"party_slot","args":{"anchor":"性别：","occ":2,"role":"agent"}}`。

3. **同一 attributeName 在一份文书里多处绑定时，两处联动显示同一个值。**
   若业务需要各填各的（如当事人区性别 vs 委托区性别），必须绑带角色前缀的
   专属字段（personGender vs agentNameGender），而不是把通用字段绑两处，
   也因此不要得出"无现成字段→手填"的结论。

4. **组件构造统一走 `ctrl_from_meta(attr)`**（doc2template/_comp.py），
   会按元数据 fieldType 自动选 input/textarea/date/DATETIME/select/checkbox 控件。
   绑定信息存在 control.extension（JSON 字符串）的 `binding` 字段，`fieldKey` 恒空串。
   **2.0 落地方式**：spec 的 `{"meta":"..."}` 速写内部就是 `ctrl_from_meta`；
   手工控件用 `{"field":...}` 速写。

5. **每次绑定前先落盘操作前快照**（getTemplateById → 时间戳文件），
   保存失败自动回滚备份；只替换空白 control 元素，绝不动文字元素。
   **2.0 落地方式**：执行器自动做顶层全量备份 + BindKit prebind 备份，无需手工。

6. 页边距是 editTemplate 模板顶层字段（不在 canvasContent.pageSetup）；
   canvas 字号 px = Word pt × 4/3 (floor)；横线宽 = docx 实测 mm × 3.78。
   **2.0 落地方式**：保存时自动回写模板顶层页边距现值；横线宽用 `underlines` +
   `fit_underlines`，px 按上式换算。

7. **当事人区表格（个人/单位两段式）绑定后必须配联动组，缺联动组不算定版。**
   （2026-09-23 用户提出；行政强制措施决定书 v86 / 立案登记表 v47 / 行政处罚决定书 v58
   三例范本同构实证，已落地《抽样取证凭证-普通》v70。）
   - **存储结构**：控件 extension（JSON 字符串，`control.extension`）增补
     `linkGroup`（组名，"个人"/"单位"）+ `linkTrigger`（布尔，**每组恰好一个 true**=触发位，
     其余 false）。触发位=姓名/单位名称——用户填一处、整组随案件数据带入。
   - **标准组配**：组「个人」触发=`personName`，成员=`personIdCard`/`personAddress`/`personPhone`；
     组「单位」触发=`companyName`，成员=`companyAddress`/`companyPhone`/`legalPerson`/`creditCode`。
   - **补齐标准键**（与范本逐键一致，用 setdefault 不覆盖已有值）：
     `renderCode/showPreset/personPicker/lawPicker=false`、`sourceFields=[]`、
     `editable/showBorder=true`、`showEditable/allowAddSignature/dynamicTable/everyPage=false`、
     `qaCount=1`。
   - **就地补绑**：只改 `control.extension`（保持 JSON 字符串形态），
     不动 label/binding/placeholder/版式/单元格结构。
   - **与 sourceFields 联动区分**：`sourceFields:[personName,companyName]`
     （撤销决定书 [5]→partyName）是"一个框显多来源字段"，与 `linkGroup` 组联动并存、
     语义不同；表格当事人区用 linkGroup，一框双源用 sourceFields。
   - **脚本范本**：`traffic-bind/_bind_cy_link_20260923.py`
     （在线字段清单核验→快照→预检→补绑→回验→失败回滚）。
     待沉淀：联动补绑应加为 `_blocks.py` 区块（如 `party_link`），
     spec 用 `{"op":"block","name":"party_link","args":{...}}` 调用。

## 实战记录：陈述、申辩笔录-普通（2026-09-23，1.x 时代单文书脚本完成）

- 模板 id `2087781177726767105`，用户手工精简后 main 105 元素、24 组件。
- 已绑 18：docNo(serial) startTime/endTime(DATETIME) inquiryLocation personName
  legalPerson idDocumentNumber caseRelation agentName personGender[45] personAge[48]
  officer1/officer2 recorder statementDefenseReason signature×4(豁免) systemDate×3。
- 漏检 7 格（快照过期所致）现已有字段可绑：
  [21]personGender [24]personAge [27]companyName [30]personPhone [33]personAddress（当事人区）；
  [51]agentNameWorkCompany [54]agentPhone [57]agentNameWorkCompanyAddress
  [60]agentRepCertInfo（委托区）；[70]statementDefensePurpose（目的）。
- 待确认：[45][48] 是否从 personGender/personAge（与当事人联动）改为
  agentNameGender/agentAge（独立值）。
- 该文书如需重绑/补绑，按 2.0 流程写 spec 即可，不要复活单文书脚本。

## 实战记录：抽样取证凭证-普通（2026-09-23，联动规则补绑完成）

- 模板 id `2087702374467678210`，定版 v70：正文 docNo/occurPlace/violationCaseCause/
  violationBasis/officer1/officer2/officerCert1/officerCert2 + 表格[5]当事人区 9 控件
  （9 字段全带联动组）+ 物品清单表格[26]按用户标准保持手动填写。
  空槽 date/输入框（[7][11][17][20][29][30][38][48][56]）未绑、暂留。
- 表格[5]联动组（铁律 7）：组「个人」触发=personName(r0c3)，
  成员 personIdCard(r0c5)/personAddress(r1c1)/personPhone(r1c3)；
  组「单位」触发=companyName(r2c2)，
  成员 companyAddress(r3c1)/companyPhone(r4c1)/legalPerson(r4c3)/creditCode(r5c1)。
- 踩坑：v68→v69 平台重存产生浮点噪声（行高 38→37.99999999999999），extension 语义零变化——
  版本断言失败先做语义 diff 再决定是否重分析，勿盲目中止也勿盲跑。
- 备份：workspaces/hyq/traffic-bind/backups/2087702374467678210-20260923-v69联动补绑前备份.json。
