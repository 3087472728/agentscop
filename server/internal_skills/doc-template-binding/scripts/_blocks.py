# -*- coding: utf-8 -*-
"""固定组件通用区块库(2.0 spec 驱动绑定流程的积木)。

来源: 从已完成 70 份文书的 _bind_*.py 脚本中提炼的重复套路。
用法: 不再为单份文书写脚本。新文书写 spec(JSON) 引用这里的区块,
由 _bindrun.py 执行。若出现新套路, 沉淀成新 block 加进本文件,
仍然不写单文书脚本。

区块函数统一签名: fn(bk, run, **args) -> str(结果摘要)
  bk  = _bindkit.BindKit 实例(已拉取模板, 已备份)
  run = _bindrun.Runner 实例(提供 find/line_end/replace_line/ctrl 等原语)

区块清单(参数详见各 docstring / `python _bindrun.py blocks`):
  case_no           案号 serial 行(清残留散字)
  time_range        时间起止行: 标签+起(DATETIME)+至+止(DATETIME)
  officer_lines     执法人员N+执法证号N 行
  signature_line    精简式签名行: 标签+signature+systemDate
  stamp_date        印章区: 机关名(继承字体)+stamp+日期绑 systemDate
  remark_textarea   备注行: 保留标签+追加未绑定多行文本
  delivery_mode     送达方式 6□ 标准重建(未绑定复选框)
  delivery_receipt  送达回证家族整表标准绑定(案号/案由/送达单位/受送达人/代收人/送达方式)
  party_slot        当事人区槽位按角色前缀路由字段(铁律2)
  approval_chain    审批链 level1-4(审批表家族)
  ymd_replace       "年 月 日"占位文字 → date 控件(完整替代)
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, "/home/agentrunner/references/hyq"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _comp as C

# 角色前缀预设(铁律2: 同义槽位先找专属前缀字段)。
# try 列表按序探测在线元数据, 命中即用; 都没有才按 fallback 处理。
ROLE_PRESETS = {
    "person": {
        "姓名": ["personName"], "性别": ["personGender"], "年龄": ["personAge"],
        "电话": ["personPhone"], "住址": ["personAddress"], "身份证": ["personIdCard"],
    },
    "company": {
        "名称": ["companyName"], "地址": ["companyAddress"], "电话": ["companyPhone"],
        "信用代码": ["enterpriseCreditCode"], "法定代表人": ["legalPerson", "enterpriseLegalPerson"],
    },
    "legalRep": {
        "姓名": ["legalPerson"], "性别": ["legalRepGender"], "年龄": ["legalRepAge"],
        "工作单位": ["legalRepWorkUnit"], "电话": ["legalRepPhone"], "住址": ["legalRepAddress"],
        "证件": ["legalRepCertInfo"],
    },
    "agent": {
        "姓名": ["agentName"], "性别": ["agentNameGender"], "年龄": ["agentAge"],
        "工作单位": ["agentNameWorkCompany"], "电话": ["agentPhone"],
        "地址": ["agentNameWorkCompanyAddress"], "证件": ["agentRepCertInfo"],
    },
    "委托人": {
        "名称": ["nameIllegalActNameName"], "签名": ["nameIllegalActNameSignature"],
        "签章": ["nameIllegalActNamePersonnel"],
    },
}


def _meta_index():
    """实时字段索引(须先由 run.refresh_meta() 拉取过在线清单)。"""
    return C._load_meta_index()


# ---------------------------------------------------------------- 案号
def case_no(bk, run, placeholder="辽X交XX字", rowflex="right", anchor="案号："):
    """案号 serial。清锚点后到行尾的散字/残留, 放 serial 控件; 已绑则跳过。"""
    i = run.find(anchor)
    if i is None:
        return "!! 未找到案号标签: %s" % anchor
    nxt = i + 1
    if nxt < len(bk.main) and isinstance(bk.main[nxt], dict) \
            and bk.main[nxt].get("type") == "control":
        return "案号已绑, 跳过"
    j = run.line_end(nxt)
    bk.main[nxt:j] = [C.field_ctrl("案号", "docNo", "serial",
                                   placeholder=placeholder, rowflex=rowflex)]
    return "案号 serial @%d" % nxt


# ---------------------------------------------------------------- 时间起止
def time_range(bk, run, anchor, label=None, start_field="startDate",
               end_field="endDate", with_time=True, size=16):
    """'标签：{起} 至 {止}' 整行替换; DATETIME 类字段 with_time=True(精确到分)。"""
    i = run.find(anchor)
    if i is None:
        return "!! 未找到锚点: %s" % anchor
    run.replace_line(anchor, [
        run.T(label or anchor, size=size),
        {"field": start_field, "label": start_field, "ctype": "date", "with_time": with_time},
        run.T(" 至 ", size=size),
        {"field": end_field, "label": end_field, "ctype": "date", "with_time": with_time},
        run.NL(),
    ], keep_anchor=False)
    return "时间起止 %s/%s%s" % (start_field, end_field, " 精确到分" if with_time else "")


# ---------------------------------------------------------------- 执法人员
def officer_lines(bk, run, count=2, officer_fmt="officer%dName",
                  cert_fmt="officer%dCertNo", anchor="行政执法人员：",
                  cert_label="行政执法证号：", size=16):
    """前 count 次出现的执法人员行: '行政执法人员：{姓名}  证号：{证号}'。"""
    done = 0
    start = 0
    for n in range(1, count + 1):
        i = run.find(anchor, start)
        if i is None:
            break
        run.replace_line(anchor, [
            run.T(anchor, size=size),
            {"field": officer_fmt % n, "label": "执法人员%d姓名" % n, "ctype": "input"},
            run.T("  %s " % cert_label, size=size),
            {"field": cert_fmt % n, "label": "执法证号%d" % n, "ctype": "input"},
            run.NL(),
        ], keep_anchor=False, start=i)
        start = i + 1
        done += 1
    return "执法人员行 x%d" % done


# ---------------------------------------------------------------- 签名区
def signature_line(bk, run, label="行政执法人员：", date_bind="systemDate", size=16):
    """精简式签名行(用户确认标准): 标签+signature+间隔+日期, 不放姓名框与(签名或者盖章)文字。"""
    run.replace_line(label, [
        run.T(label, size=size),
        {"unbound": "signature"},
        run.T("   ", size=size),
        {"field": date_bind or "", "label": "系统日期", "ctype": "date"},
        run.NL(),
    ], keep_anchor=False)
    return "签名行: %s + signature + %s" % (label.strip("："), date_bind or "未绑日期")


# ---------------------------------------------------------------- 印章区
def stamp_date(bk, run, agency="交通运输执法部门（印章）", tr=None, col=None,
               anchor=None, date_bind="systemDate", rowflex="right"):
    """印章区: 机关名(继承原字体属性, 严禁假设)+stamp+日期绑 systemDate。
    tr/col 给定→表格单元格; 否则按 anchor 行替换。"""
    def build(props):
        nodes = [{"value": agency, "type": "text", "rowFlex": rowflex, **props}]
        nodes.append(C.stamp(rowflex=rowflex))
        nodes.append({"value": "日期：", "type": "text", **props})
        nodes.append(C.date_ctrl("日期选择", rowflex=rowflex,
                                 bind=date_bind) if date_bind else C.date_ctrl(rowflex=rowflex))
        return nodes

    if tr is not None:
        cell = run.cell(tr, col)
        src = next((e for e in cell if isinstance(e, dict)
                    and ("印章" in bk.txt(e) or agency[:4] in bk.txt(e))), {})
        props = {k: src[k] for k in ("font", "size", "bold") if k in src}
        cell[:] = build(props)
        return "印章区 @t%d r%d c%d" % (0, tr, col)
    i = run.find(anchor or agency[:4])
    if i is None:
        return "!! 未找到印章区锚点: %s" % (anchor or agency)
    s = run.line_start(i)
    j = run.line_end(i)
    props = {k: bk.main[i][k] for k in ("font", "size", "bold")
             if k in bk.main[i]}
    bk.main[s:j] = build(props)
    return "印章区 @%d" % s


# ---------------------------------------------------------------- 备注
def remark_textarea(bk, run, anchor="备注：", tr=None, col=None):
    """备注行: 保留'备注：'标签, 追加未绑定多行文本。"""
    if tr is not None:
        cell = run.cell(tr, col)
        cell.append(C.textarea_ctrl("多行文本", ""))
        return "备注 textarea @t%d r%d c%d" % (0, tr, col)
    i = run.find(anchor)
    if i is None:
        return "!! 未找到: %s" % anchor
    s = run.slot_after(i)
    if s is None:
        bk.main.insert(i + 1, C.textarea_ctrl("多行文本", ""))
        s = i + 1
    else:
        bk.main[s] = C.textarea_ctrl("多行文本", "")
    return "备注 textarea @%d" % s


# ---------------------------------------------------------------- 送达方式
def delivery_mode(bk, run, tr=3, col=1,
                  items=("直接送达", "留置送达", "委托送达", "邮寄送达", "电子送达", "公告送达")):
    """送达方式: N 个未绑定复选框(不创建字段, 手勾), 居中宋体16。"""
    cell = run.cell(tr, col)
    nodes = []
    for k, it in enumerate(items):
        nodes.append(C.checkbox())
        nodes.append(run.T(it + ("　" if k < len(items) - 1 else ""), size=16,
                           bold=False, rowflex="center",
                           tail="\n" if it == "委托送达" else ""))
    cell[:] = nodes
    return "送达方式 %d□ @r%dc%d" % (len(items), tr, col)


# ---------------------------------------------------------------- 送达回证家族
def delivery_receipt(bk, run, case_placeholder="辽X交X送字〔〕号",
                     cause_field="violationCaseCause", agency_field="agency",
                     rows=(0, 1, 2, 3), consignee_unbound=True):
    """送达回证家族标准绑定(已完成 40 份同构, 绑法一致):
    案号 serial + 案由 + r0c1 送达单位 + r1c1 受送达人(person+company)
    + r2c1 代收人(未绑定输入框) + r3c1 送达方式(6□未绑定)。
    印章区/备注另用 stamp_date / remark_textarea 区块。"""
    out = [case_no(bk, run, placeholder=case_placeholder)]
    i = run.find("案由：")
    if i is not None:
        s = run.slot_after(i)
        if s is not None:
            bk.main[s] = run.ctrl({"meta": cause_field})
    tbl = next((e for e in bk.main if isinstance(e, dict) and e.get("type") == "table"), None)
    if tbl is None:
        out.append("!! 无表格")
    else:
        r0, r1, r2, r3 = rows
        run.cell(r0, 1)[:] = [run.ctrl({"meta": agency_field})]
        run.cell(r1, 1)[:] = run.ctrl([{"meta": "personName"}, {"meta": "companyName"}])
        if consignee_unbound:
            run.cell(r2, 1)[:] = [run.ctrl({"unbound": "input"})]
        out.append(delivery_mode(bk, run, tr=r3, col=1))
        out.append("送达单位/受送达人/代收人 @r%dc1..r%dc1" % (r0, r2))
    return "; ".join(out)


# ---------------------------------------------------------------- 当事人槽位(铁律2)
def party_slot(bk, run, anchor, occ=1, role=None, try_fields=None,
               fallback="unbound", size=16):
    """当事人区/委托区槽位绑字段: 按角色前缀路由(铁律2), 禁止拿通用字段硬凑。
    role+槽位关键词 → ROLE_PRESETS; 或 try_fields 显式给候选列表。
    fallback: unbound=放未绑定输入框 / keep=不动 / raise=报错终止。"""
    if try_fields is None:
        key = next((k for k in ROLE_PRESETS.get(role or "", {}) if k in anchor), None)
        try_fields = ROLE_PRESETS.get(role or "", {}).get(key or "", [])
    idx = run.find_occurrence(anchor, occ)
    if idx is None:
        return "!! 锚点未找到: %s #%d" % (anchor, occ)
    s = run.slot_after(idx)
    if s is None:
        return "!! 槽位未找到: %s #%d" % (anchor, occ)
    meta = _meta_index()
    for attr in try_fields:
        if attr in meta:
            bk.main[s] = run.ctrl({"meta": attr})
            return "%s #%d → %s" % (anchor, occ, attr)
    if fallback == "raise":
        raise RuntimeError("无现成字段(在线清单已核): %s 候选=%s" % (anchor, try_fields))
    if fallback == "unbound":
        bk.main[s] = C.input_ctrl(size=size)
        return "%s #%d → 未绑定输入框(无现成字段, 候选=%s)" % (anchor, occ, try_fields)
    return "%s #%d → 保持不动" % (anchor, occ)


# ---------------------------------------------------------------- 审批链
def approval_chain(bk, run, levels=4, tr_list=None, col=1,
                   name_fmt="level%dApproverName", time_fmt="level%dApproveTime",
                   opinion_fmt="level%dApproveOpinion", sig_fmt="level%dSignature"):
    """审批链 level1-N(审批表家族): 表格模式下每级一行,
    绑 审批人(input)/审批时间(DATETIME)/审批意见(TEXTAREA)/签名(signature_bound)。"""
    done = 0
    for n in range(1, levels + 1):
        tr = (tr_list[n - 1] if tr_list else None)
        nodes = [
            {"field": name_fmt % n, "label": "%d级审批人" % n, "ctype": "input"},
            {"field": time_fmt % n, "label": "%d级审批时间" % n, "ctype": "date", "with_time": True},
            {"field": opinion_fmt % n, "label": "%d级审批意见" % n, "ctype": "textarea"},
            {"sig": {"label": "%d级审批签名" % n, "binding": sig_fmt % n}},
        ]
        if tr is not None:
            run.cell(tr, col)[:] = [run.ctrl(x) for x in nodes]
        else:
            i = run.find("%d级审批" % n) or run.find("审批人")
            if i is None:
                continue
            s = run.slot_after(i)
            if s is None:
                continue
            bk.main[s:s + 1] = [run.ctrl(x) for x in nodes]
        done += 1
    return "审批链 x%d" % done


# ---------------------------------------------------------------- 年月日占位
def ymd_replace(bk, run, bind="systemDate", with_time=False, occ=1, size=16):
    """'年 月 日'类占位文字 → date 控件(完整替代, 不留年月日文字)。
    第 occ 处(全文顺序); bind 空→未绑定日期。"""
    pat = re.compile(r"^年[\s_]*月[\s_]*日?$")
    seen = 0
    for i, e in enumerate(bk.main):
        if not isinstance(e, dict) or e.get("type") in ("control", "checkbox", "image", "table"):
            continue
        s = bk.txt(e).strip()
        if not s or not pat.match(s):
            continue
        seen += 1
        if seen != occ:
            continue
        bk.main[i] = (C.date_ctrl("日期选择", size=size, bind=bind, with_time=with_time)
                      if bind else C.date_ctrl("日期选择", size=size))
        return "年月日 #%d → date(%s) @%d" % (occ, bind or "未绑", i)
    return "!! 未找到第 %d 处年月日占位" % occ


BLOCKS = {
    "case_no": case_no,
    "time_range": time_range,
    "officer_lines": officer_lines,
    "signature_line": signature_line,
    "stamp_date": stamp_date,
    "remark_textarea": remark_textarea,
    "delivery_mode": delivery_mode,
    "delivery_receipt": delivery_receipt,
    "party_slot": party_slot,
    "approval_chain": approval_chain,
    "ymd_replace": ymd_replace,
}
