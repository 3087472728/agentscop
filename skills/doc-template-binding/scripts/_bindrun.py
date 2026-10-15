# -*- coding: utf-8 -*-
"""2.0 通用绑定执行器(spec 驱动) — 一份文书=一份 spec, 不再写单文书脚本。

流程与 1.0 完全一致: 登录 → 顶层全量备份+BindKit prebind 备份 → 绑定 →
□拆分/横线/字体审计 → 保存(带顶层页边距) → 回拉核对。变化只在:
绑定动作由 spec(JSON) 描述, 固定套路复用 _blocks.py 区块库。

用法:
  python _bindrun.py scan  <模板id>              # 打印结构大纲+已绑统计(写 spec 前必做)
  python _bindrun.py run   <spec.json> [--dry]   # 执行绑定; --dry 全程演练但不保存
  python _bindrun.py meta                        # 刷新字段元数据(在线 listAll, 铁律1)
  python _bindrun.py blocks                      # 区块库清单
  python _bindrun.py selftest                    # 离线自测(不联网)

spec / op / ctrl 速查见 _specs/README.md 与 _specs/骨架-通用模板.json。
"""
import os
import sys
import json
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, "/home/agentrunner/references/hyq"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _comp as C
from _bindkit import BindKit
from _blocks import BLOCKS, ROLE_PRESETS

BACKUP_DIR = os.environ.get(
    "DOC2TMPL_BACKUP_DIR",
    "/home/agentrunner/agentscop/workspaces/hyq/traffic-bind/backups")
MARGIN_KEYS = ("paperWidth", "paperHeight", "marginTop",
               "marginBottom", "marginLeft", "marginRight")


def _import_traffic():
    """兼容 import: traffic 符号链接失效时直接用 doc2template 包。"""
    try:
        from doc2template.client import TemplateClient
        from doc2template.importer import _load_token
    except ImportError:
        from traffic.doc2template.client import TemplateClient
        from traffic.doc2template.importer import _load_token
    return TemplateClient, _load_token


TemplateClient, _load_token = _import_traffic()


# ================================================================ Runner
class Runner:
    def __init__(self, spec):
        self.spec = spec
        self.tid = str(spec.get("template_id", ""))
        self.tag = spec.get("backup_tag") or self.tid[-8:]
        self.ok, self.warn = [], []
        self.bk = None  # 当前 BindKit(原语 find/line_end/cell 等隐式使用)

    # ---------- ctrl 速写解析 ----------
    def ctrl(self, spec, size=16):
        """spec → canvas 元素(支持列表展开)。"""
        if spec is None:
            return None
        if isinstance(spec, list):
            out = [self.ctrl(x, size=size) for x in spec]
            return [x for x in out if x is not None]
        if not isinstance(spec, dict):
            raise ValueError("ctrl spec 非法: %r" % (spec,))
        if "type" in spec:      # 成品 canvas 节点(如 run.T() 产物)直接透传
            return spec
        if "meta" in spec:
            return C.ctrl_from_meta(spec["meta"], size=spec.get("size", size))
        if "field" in spec:
            ctype = spec.get("ctype", "input")
            label = spec.get("label") or spec["field"]
            if ctype == "serial":
                return C.field_ctrl(label, spec["field"], "serial",
                                    placeholder=spec.get("placeholder", "{{案号}}"),
                                    rowflex=spec.get("rowflex", "right"), size=size)
            return C.field_ctrl(label, spec["field"],
                                "textarea" if ctype == "textarea" else ctype,
                                placeholder=spec.get("placeholder", "{{%s}}" % label),
                                size=size, with_time=spec.get("with_time", False))
        if "unbound" in spec:
            k = spec["unbound"]
            if k == "input":
                return C.input_ctrl(size=size)
            if k == "textarea":
                return C.textarea_ctrl("多行文本", "")
            if k == "number":
                return C.number_ctrl(size=size)
            if k == "date":
                return C.date_ctrl(size=size, with_time=spec.get("with_time", False))
            if k == "select":
                return C.select_ctrl("下拉选择")
            if k == "checkbox":
                return C.checkbox()
            if k == "signature":
                return C.signature()
            if k == "stamp":
                return C.stamp()
            raise ValueError("未知未绑定组件: %s" % k)
        if "select" in spec:
            d = spec["select"]
            return C.select_ctrl(d["label"], d.get("binding", ""), options=d.get("options"), size=size)
        if "checkgroup" in spec:
            d = spec["checkgroup"]
            return C.checkbox_group(d["label"], d.get("binding", ""), options=d.get("options"), size=size)
        if "sig" in spec:
            d = spec["sig"]
            return C.signature_bound(d["label"], d.get("binding", ""), size=size)
        if "text" in spec:
            node = {"value": spec["text"], "type": "text", "font": spec.get("font", "宋体")}
            if spec.get("size") is not None:
                node["size"] = spec["size"]
            if spec.get("bold") is not None:
                node["bold"] = spec["bold"]
            if spec.get("rowflex"):
                node["rowFlex"] = spec["rowflex"]
            if spec.get("tail"):
                node["value"] += spec["tail"]
            return node
        if spec.get("newline"):
            return {"value": "\n", "type": "text"}
        if spec.get("blank"):
            return {"value": ""}
        raise ValueError("ctrl spec 非法: %r" % (spec,))

    # ---------- 文本元素便捷 ----------
    @staticmethod
    def T(text, size=16, font="宋体", bold=None, rowflex=None, tail=""):
        node = {"value": text + tail, "type": "text", "font": font, "size": size}
        if bold is not None:
            node["bold"] = bold
        if rowflex:
            node["rowFlex"] = rowflex
        return node

    @staticmethod
    def NL():
        return {"value": "\n", "type": "text"}

    # ---------- 定位原语 ----------
    @staticmethod
    def find_in(main, needle, start=0, nth=1):
        cnt = 0
        for i in range(start, len(main)):
            if isinstance(main[i], dict) and needle in BindKit.txt(main[i]):
                cnt += 1
                if cnt == nth:
                    return i
        return None

    def find(self, needle, start=0, nth=1):
        return self.find_in(self.bk.main, needle, start, nth)

    def find_occurrence(self, needle, occ=1):
        return self.find(needle, 0, occ)

    def line_end(self, idx):
        """idx 起本行结束(含\\n元素自身保留, 返回其下标)。"""
        j = idx
        while j < len(self.bk.main):
            e = self.bk.main[j]
            if isinstance(e, dict) and "\n" in BindKit.txt(e):
                return j
            j += 1
        return len(self.bk.main)

    def line_start(self, idx):
        s = idx
        while s > 0 and isinstance(self.bk.main[s - 1], dict) \
                and "\n" not in BindKit.txt(self.bk.main[s - 1]) \
                and len(BindKit.txt(self.bk.main[s - 1])) < 200:
            s -= 1
        return s

    def slot_after(self, idx, skip=0):
        if idx is None:
            return None
        j, seen = idx + 1, 0
        while j < len(self.bk.main) and isinstance(self.bk.main[j], dict):
            t = BindKit.txt(self.bk.main[j])
            if t.strip() == "" and "\n" not in t:
                if seen == skip:
                    return j
                seen += 1
            j += 1
        return None

    def slots_after(self, idx, n, skip=0):
        out, j, seen = [], idx + 1, 0
        while j < len(self.bk.main) and len(out) < n:
            e = self.bk.main[j]
            t = BindKit.txt(e) if isinstance(e, dict) else ""
            if t.strip() == "" and "\n" not in t:
                if seen >= skip:
                    out.append(j)
                else:
                    seen += 1
            j += 1
        return out if len(out) == n else None

    def table(self):
        return next((e for e in self.bk.main if isinstance(e, dict) and e.get("type") == "table"), None)

    def cell(self, tr, col):
        tbl = self.table()
        if tbl is None:
            raise RuntimeError("模板无表格, 无法定位 r%dc%d" % (tr, col))
        return tbl["trList"][tr]["tdList"][col]["value"]

    # ---------- 行/区间替换 ----------
    def replace_line(self, anchor, parts, keep_anchor=False, start=0, nth=1):
        i = self.find(anchor, start, nth)
        if i is None:
            self.warn.append("replace_line 未找到: %s" % anchor)
            return None
        j = self.line_end(i)
        s = self.line_start(i) if not keep_anchor else i
        seg = ([] if not keep_anchor else [self.bk.main[i]]) + (self.ctrl(parts) or [])
        self.bk.main[s:j] = seg
        return s

    def replace_range(self, start_anchor, end_anchor, parts, keep_end=True, start=0):
        i = self.find(start_anchor, start)
        if i is None:
            self.warn.append("replace_range 起锚未找到: %s" % start_anchor)
            return None
        j = self.find(end_anchor, i + 1)
        if j is None:
            self.warn.append("replace_range 止锚未找到: %s" % end_anchor)
            return None
        s = self.line_start(i)
        seg = self.ctrl(parts) or []
        if keep_end:
            seg = seg + [self.bk.main[j]]
        self.bk.main[s:j + 1] = seg
        return s

    # ---------- steps ----------
    def apply_steps(self, bk):
        self.bk = bk
        for n, st in enumerate(self.spec.get("steps") or [], 1):
            try:
                self._step(bk, st.get("op"), st)
            except Exception as e:
                raise RuntimeError("step#%d %s 执行失败: %s" % (n, st.get("op"), e))
        return self

    def _step(self, bk, op, st):
        if op == "assert":
            if self.find(st["text"]) is None:
                raise RuntimeError("前置断言失败: 模板中无 %r" % st["text"])
            self.ok.append("assert ✓ %s" % st["text"][:16])
        elif op in ("bind_after", "bind_occurrence"):
            i = (self.find_occurrence(st["anchor"], st.get("occ", 1))
                 if op == "bind_occurrence"
                 else self.find(st["anchor"], st.get("start", 0), st.get("nth", 1)))
            if i is None:
                self.warn.append("%s 未找到: %s" % (op, st["anchor"]))
                return
            nodes = self.ctrl(st["ctrl"]) or []
            if not isinstance(nodes, list):
                nodes = [nodes]
            slots = self.slots_after(i, len(nodes), st.get("skip", 0))
            if slots is None:
                self.warn.append("%s 空位不足(%d): %s" % (op, len(nodes), st["anchor"]))
                return
            for s, node in zip(slots, nodes):
                bk.main[s] = node
            self.ok.append("%s %s ×%d" % (op, st["anchor"][:16], len(nodes)))
        elif op == "replace_line":
            s = self.replace_line(st["anchor"], st.get("parts", []),
                                  keep_anchor=st.get("keep_anchor", False),
                                  start=st.get("start", 0), nth=st.get("nth", 1))
            if s is not None:
                self.ok.append("replace_line %s" % st["anchor"][:16])
        elif op == "replace_range":
            s = self.replace_range(st["start_anchor"], st["end_anchor"],
                                   st.get("parts", []), keep_end=st.get("keep_end", True),
                                   start=st.get("start", 0))
            if s is not None:
                self.ok.append("replace_range %s..%s" % (st["start_anchor"][:10], st["end_anchor"][:10]))
        elif op == "set_text":
            i = self.find(st["anchor"], st.get("start", 0), st.get("nth", 1))
            if i is None:
                self.warn.append("set_text 未找到: %s" % st["anchor"])
                return
            bk.main[i]["value"] = st["text"]
            self.ok.append("set_text %s" % st["anchor"][:16])
        elif op == "clear_after":
            i = self.find(st["anchor"], st.get("start", 0), st.get("nth", 1))
            if i is None:
                self.warn.append("clear_after 未找到: %s" % st["anchor"])
                return
            for k in range(i + 1, min(i + 1 + st.get("count", 1), len(bk.main))):
                bk.main[k] = {"value": ""}
            self.ok.append("clear_after %s ×%d" % (st["anchor"], st.get("count", 1)))
        elif op == "table_cell":
            cell = self.cell(st["tr"], st["col"])
            if st.get("clear"):
                cell[:] = []
            elif "parts" in st:
                cell[:] = self.ctrl(st["parts"]) or []
            self.ok.append("table_cell r%dc%d" % (st["tr"], st["col"]))
        elif op == "table_assert":
            cell = self.cell(st["tr"], st["col"])
            text = "".join(BindKit.txt(e) for e in cell if isinstance(e, dict))
            if st["contains"] not in text:
                raise RuntimeError("表格断言失败 r%dc%d: 无 %r, 实为 %r"
                                   % (st["tr"], st["col"], st["contains"], text[:40]))
            self.ok.append("table_assert ✓ r%dc%d" % (st["tr"], st["col"]))
        elif op == "block":
            name = st["name"]
            if name not in BLOCKS:
                raise RuntimeError("未知区块: %s (可用: %s)" % (name, ",".join(sorted(BLOCKS))))
            self.ok.append("[%s] %s" % (name, BLOCKS[name](bk, self, **(st.get("args") or {}))))
        elif op == "kit":
            getattr(bk, st["method"])(**(st.get("args") or {}))
            self.ok.append("kit.%s" % st["method"])
        else:
            raise RuntimeError("未知 op: %s" % op)

    # ---------- 收尾(与 1.0 流水线一致) ----------
    def finishers(self, bk):
        sp = self.spec
        if sp.get("split_checkboxes", True):
            bk.split_checkboxes()
        if sp.get("clean_date_residual", True):
            bk.clean_date_residual()
        ul = sp.get("underlines") or {}
        if ul:
            bk.set_underlines(ul)
            if sp.get("fit_underlines", True):
                bk.fit_underlines()
        if sp.get("font_map"):
            bk.apply_font_map([tuple(r) for r in sp["font_map"]])
        bk.audit_fonts()
        bk.check_none()

    def margins_of(self, raw):
        m = {k: raw.get(k) for k in MARGIN_KEYS if raw.get(k) is not None}
        m.update(self.spec.get("margins") or {})
        return m or None

    # ---------- 认证 ----------
    def _client(self):
        cli = None
        if self.spec.get("auth") != "browser":
            try:
                tok = _load_token().replace("Bearer ", "").strip()
                cli = TemplateClient(tok)
                if not self._probe(cli):
                    cli = None
            except Exception:
                cli = None
        if cli is None:
            print("[auth] 缓存 token 不可用, 走 OCR 自动登录…")
            from export_frontend import ensure_token
            pack = ensure_token()
            tok = pack["accessToken"].replace("Bearer ", "").strip()
            cli = TemplateClient(tok)
        return cli

    def _probe(self, cli):
        try:
            resp = cli.get_template_by_id(self.tid)
            if str(resp.get("code")) == "401":
                return False
            return bool((resp.get("data") or {}).get("canvasContent"))
        except Exception:
            return False

    # ---------- 字段元数据(铁律1) ----------
    def refresh_meta(self, cli):
        if not self.spec.get("refresh_meta", True):
            return -1
        base = cli.base_url.rsplit("/doc-template", 1)[0]
        url = base + "/case-filing-field-meta/listAll"
        auth = cli.headers["Authorization"]
        req = urllib.request.Request(url, headers={"Authorization": auth, "accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.loads(r.read().decode("utf-8", "replace"))
            data = resp.get("data") if isinstance(resp, dict) else resp
            recs = data if isinstance(data, list) else (data or {}).get("records") or []
            json.dump(recs, open(os.path.join(_HERE, "_field_meta.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            C._META_INDEX = None
            print("[meta] 在线字段清单已刷新: %d 条" % len(recs))
            return len(recs)
        except Exception as e:
            print("[meta] !! 在线清单拉取失败(%s), 使用本地快照" % e)
            return -1

    # ---------- 结构扫描 ----------
    @staticmethod
    def walk(doc):
        """深度遍历: yield (路径标签, 元素)。表格展开为 t{N}.r{R}c{C}。"""
        stack = [("", e) for e in (doc.get("main") or [])][::-1]
        while stack:
            path, e = stack.pop()
            if not isinstance(e, dict):
                continue
            if e.get("type") == "table":
                for ri, tr in enumerate(e.get("trList") or []):
                    for ci, td in enumerate(tr.get("tdList") or []):
                        for x in td.get("value") or []:
                            stack.append(("%sr%dc%d." % (path, ri, ci), x))
            else:
                yield path, e

    def scan(self, cli):
        resp = cli.get_template_by_id(self.tid)
        doc = json.loads((resp.get("data") or {}).get("canvasContent") or "{}")
        bound = unbound = nchk = 0
        print("== 结构大纲 ==")
        for path, e in self.walk(doc):
            t = e.get("type")
            if t == "table":
                continue
            text = BindKit.txt(e).replace("\n", "\\n")[:40]
            info = path
            if t == "control":
                c = e.get("control") or {}
                b = (c.get("extension") or {}).get("binding") or ""
                info += " [%s%s]" % (c.get("type", "?"), "@" + b if b else "")
                if b:
                    bound += 1
                else:
                    unbound += 1
            elif t == "checkbox":
                info += " [checkbox]"
                nchk += 1
            print("%4s| %s | %s" % ("", info, text))
        print("== 汇总: 已绑控件 %d, 未绑控件 %d, checkbox 元素 %d ==" % (bound, unbound, nchk))
        return doc

    # ---------- 回拉核对 ----------
    def verify(self, cli):
        resp = cli.get_template_by_id(self.tid)
        doc = json.loads((resp.get("data") or {}).get("canvasContent") or "{}")
        bound_attrs, n_unbound = [], 0
        for _, e in self.walk(doc):
            if e.get("type") == "control":
                c = e.get("control") or {}
                b = (c.get("extension") or {}).get("binding") or ""
                if b:
                    bound_attrs.append(b)
                else:
                    n_unbound += 1
        uniq = sorted(set(bound_attrs))
        print("[verify] 已绑控件 %d 个(去重 %d 种), 未绑控件 %d 个" % (len(bound_attrs), len(uniq), n_unbound))
        print("  绑定清单: %s" % ",".join(bound_attrs))
        v = self.spec.get("verify") or {}
        fails = []
        if v.get("expect_min_bindings") and len(bound_attrs) < v["expect_min_bindings"]:
            fails.append("绑定数 %d < 期望 %d" % (len(bound_attrs), v["expect_min_bindings"]))
        for a in v.get("expect_bind") or []:
            if a not in bound_attrs:
                fails.append("缺字段绑定: %s" % a)
        if fails:
            print("[verify] FAIL:\n  - " + "\n  - ".join(fails))
            return False
        print("[verify] PASS")
        return True

    # ---------- 主流程(与 1.0 流程一致) ----------
    def bind(self, dry=False):
        cli = self._client()
        raw = ((cli.get_template_by_id(self.tid) or {}).get("data") or {})
        name = raw.get("name") or self.tid
        os.makedirs(BACKUP_DIR, exist_ok=True)
        bkf = os.path.join(BACKUP_DIR, "%s-%s-%s-绑定前备份.json"
                           % (self.tid, time.strftime("%Y%m%d-%H%M%S"), self.tag))
        json.dump(raw, open(bkf, "w", encoding="utf-8"), ensure_ascii=False)
        print("[backup] 顶层全量备份 → %s" % bkf)

        self.refresh_meta(cli)
        bk = BindKit(self.tid, backup_tag=self.tag)
        print("[meta] 模板: %s | 主层级 %d 元素" % (bk.name, len(bk.main)))

        self.apply_steps(bk)
        self.finishers(bk)

        if dry:
            print("[dry] 演练完成, 未保存。审计摘要:")
            print("  " + "\n  ".join(self.ok))
            if self.warn:
                print("  警告:\n  - " + "\n  - ".join(self.warn))
            return True

        if self.warn:
            print("[warn] " + "; ".join(self.warn))
        resp = bk.save(margins=self.margins_of(raw))
        if not resp or str((resp or {}).get("code")) != "200":
            print("[save] FAIL: %r" % resp)
            return False
        print("[save] OK, 已回写页边距。")
        return self.verify(cli)


# ================================================================ 离线自测
def _selftest():
    """不联网: 用内联假模板跑全 op/区块, 验证引擎。"""
    fixture = {"main": [
        {"value": "检查（勘验）时间：", "type": "text"},
        {"value": "年 月 日 至 ", "type": "text"},
        {"value": "", "type": "text"},
        {"value": "\n", "type": "text"},
        {"value": "案号：", "type": "text", "rowFlex": "right"},
        {"value": "散", "type": "text"},
        {"value": "字", "type": "text"},
        {"value": "\n", "type": "text"},
        {"value": "性别：", "type": "text"},
        {"value": "", "type": "text"},
        {"value": "\n", "type": "text"},
        {"value": "□现金 □转账", "type": "text"},
        {"value": "\n", "type": "text"},
        {"value": "年    月    日", "type": "text", "rowFlex": "right"},
        {"value": "\n", "type": "text"},
        {"type": "table", "trList": [
            {"tdList": [{"value": [{"value": "受送达人", "type": "text"}]},
                        {"value": [{"value": "", "type": "text"}]}]},
            {"tdList": [{"value": [{"value": "送达方式", "type": "text"}]},
                        {"value": [{"value": "", "type": "text"}]}]}]}],
        "version": "1.0.7"}
    r = Runner({"template_id": "selftest", "backup_tag": "selftest", "refresh_meta": False,
                "asserts": ["案号：", "受送达人"],
                "steps": [
                    {"op": "assert", "text": "案号："},
                    {"op": "block", "name": "case_no", "args": {"placeholder": "辽X交自检字〔〕号"}},
                    {"op": "block", "name": "time_range",
                     "args": {"anchor": "检查（勘验）时间：", "start_field": "startDate", "end_field": "endDate"}},
                    {"op": "bind_after", "anchor": "性别：", "ctrl": {"meta": "personGender"}},
                    {"op": "table_cell", "tr": 0, "col": 1,
                     "parts": [{"meta": "personName"}, {"meta": "companyName"}]},
                    {"op": "block", "name": "delivery_mode", "args": {"tr": 1, "col": 1}},
                    {"op": "block", "name": "ymd_replace", "args": {"bind": "systemDate"}},
                ]})
    bk = object.__new__(BindKit)
    bk.main = json.loads(json.dumps(fixture))["main"]
    bk.page_setup = {}
    bk.tid, bk.name, bk.backup_path = "selftest", "自测模板", "/tmp/selftest-bk.json"
    bk.cli = None
    # ---- 步骤(离线只跑 apply_steps, BindKit 收尾方法依赖真实实例) ----
    r.apply_steps(bk)
    print("\n".join("  ✓ " + s for s in r.ok))
    # ---- 断言 ----
    def _binding(e):
        ext = (e.get("control") or {}).get("extension")
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except Exception:
                ext = {}
        return (ext or {}).get("binding") or ""
    assert any(isinstance(e, dict) and e.get("type") == "control"
               and _binding(e) == "docNo" for e in bk.main), "案号未绑"
    assert r.find("案号：") is not None
    td = r.cell(1, 1)
    assert sum(1 for e in td if e.get("type") == "checkbox") == 6, "送达方式应为6个□"
    texts = "".join(BindKit.txt(e) for e in bk.main if isinstance(e, dict))
    assert "散" not in texts and "字\n" not in texts, "案号残留未清"
    assert not r.warn, "出现警告: %r" % r.warn
    print("selftest PASS: 案号serial/时间起止/当事人槽位/表格双字段/6□/年月日替换 全部通过")


# ================================================================ CLI
def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "selftest":
        return _selftest()
    if cmd == "blocks":
        print("区块库(_blocks.py) 可用区块:")
        for name, fn in sorted(BLOCKS.items()):
            doc = (fn.__doc__ or "").strip().splitlines()[0]
            print("  %-16s %s" % (name, doc))
        print("角色预设(铁律2): %s" % ", ".join(sorted(ROLE_PRESETS)))
        return 0
    if cmd == "meta":
        r = Runner({"refresh_meta": True})
        n = r.refresh_meta(r._client())
        return 0 if n >= 0 else 1
    if cmd == "scan" and len(args) > 1:
        r = Runner({"template_id": args[1], "refresh_meta": False})
        r.scan(r._client())
        return 0
    if cmd == "run" and len(args) > 1:
        dry = "--dry" in args
        spec = json.load(open(args[1], encoding="utf-8"))
        if spec.get("template_id") == "__TID__":
            print("!! spec 的 template_id 还是 __TID__, 先填真实模板id")
            return 2
        print("== run %s %s ==" % (os.path.basename(args[1]), "[dry]" if dry else ""))
        ok = Runner(spec).bind(dry=dry)
        return 0 if ok else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
