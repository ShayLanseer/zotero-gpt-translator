#!/usr/bin/env python3
"""Local PDF mechanics for translation by the current chat model. No model API."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import time

dependency_path = os.environ.get("ZGT_DEPS")
runtime_config = Path(__file__).with_name("runtime.local.json")
if not dependency_path and runtime_config.is_file():
    dependency_path = json.loads(runtime_config.read_text(encoding="utf-8-sig")).get("deps")
    if dependency_path and not Path(dependency_path).is_absolute():
        dependency_path = str((runtime_config.parent / dependency_path).resolve())
if dependency_path:
    sys.path.insert(0, dependency_path)
try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def require_pdf():
    if fitz is None:
        raise ValueError("缺少 PyMuPDF；使用 requirements.txt 安装，或通过 ZGT_DEPS 指定独立依赖目录。")
    fitz.TOOLS.set_small_glyph_heights(True)


def config_path(explicit=None):
    if explicit:
        return Path(explicit).resolve()
    for parent in [Path.cwd(), *Path.cwd().parents]:
        candidate = parent / ".zotero" / "connection.json"
        if candidate.is_file():
            return candidate
    raise ValueError("未找到 .zotero/connection.json；请用 --config 指定。")


def connect(args):
    """Bind a new workstation to its own running local Zotero, read-only."""
    workspace = Path(args.workspace).resolve()
    path = workspace / ".zotero" / "connection.json"
    if path.exists():
        api = API(path)
        api.get(api.root + "/items?limit=1")
        emit({"connected": True, "config": str(path), "existing_config_preserved": True, "library_write_requested": False})
        return
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request("http://127.0.0.1:23119/api/users/0/items?limit=1", headers={"Zotero-API-Version": "3"})
    with opener.open(request, timeout=15) as response:
        server_id = response.headers.get("Zotero-Server-ID")
        if not server_id or not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", server_id):
            raise ValueError("Zotero 未返回可核实的实例标识；不要复制其他电脑的连接配置。")
        if not isinstance(json.load(response), list):
            raise ValueError("本机端点不是预期的 Zotero 文库列表。")
    config = {"mode": "local", "base_url": "http://127.0.0.1:23119/api", "library_type": "user", "library_id": "0", "server_id": server_id, "access": "read-only", "api_version": "3", "bypass_proxy": True}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
    api = API(path)
    api.get(api.root + "/items?limit=1")
    emit({"connected": True, "config": str(path), "existing_config_preserved": False, "library_write_requested": False})


def prepare_fonts(work):
    """Use fonts from this Windows machine; never distribute Windows fonts."""
    require_pdf()
    from fontTools.ttLib import TTFont
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    assets = Path(work).resolve() / "math-assets"
    assets.mkdir(parents=True, exist_ok=True)
    song, times = assets / "font-simsun.ttf", assets / "font-times.ttf"
    if not song.exists():
        font = TTFont(fonts / "simsun.ttc", fontNumber=0)
        font.save(song)
        font.close()
    if not times.exists():
        shutil.copyfile(fonts / "times.ttf", times)
    if "simsun" not in fitz.Font(fontfile=str(song)).name.lower() or "times" not in fitz.Font(fontfile=str(times)).name.lower():
        raise ValueError("工作目录现有字体不符合宋体/Times 规则，不覆盖；请核实。")
    return {"song": str(song), "times": str(times)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("拒绝 Zotero API 重定向；请检查本机连接。")


class API:
    def __init__(self, path):
        self.config_path = Path(path).resolve()
        self.config = load(path)
        if self.config.get("library_type", "user") != "user" or str(self.config.get("library_id", "0")) != "0":
            raise ValueError("此版本只支持本机 /users/0 个人文库；不自动切换文库。")
        self.base = self.config["base_url"].rstrip("/")
        self.local_url(self.base)
        if urllib.parse.urlsplit(self.base).path != "/api":
            raise ValueError("连接必须使用 Zotero 本机 /api 路径。")
        self.server_id = self.config["server_id"]
        self.root = "/users/0"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.token = None

    @staticmethod
    def local_url(url):
        p = urllib.parse.urlsplit(url)
        if p.scheme != "http" or p.hostname != "127.0.0.1" or p.port != 23119 or p.username or p.password:
            raise ValueError("只允许 http://127.0.0.1:23119 本机 Zotero；不发送至远程服务。")

    def request(self, path, method="GET", body=None, headers=None, url=None, timeout=120):
        target = url or self.base + path
        self.local_url(target)
        hh = {"Zotero-API-Version": "3", "Zotero-Server-ID": self.server_id, **(headers or {})}
        req = urllib.request.Request(target, data=body, headers=hh, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                identity = response.headers.get("Zotero-Server-ID")
                if identity and identity != self.server_id:
                    raise ValueError("Zotero 实例标识已变化，停止。")
                data = response.read()
                return response.status, json.loads(data) if data and "json" in response.headers.get("Content-Type", "") else data
        except urllib.error.HTTPError as e:
            # Do not expose API keys, document content, or untrusted HTML error bodies.
            raise ValueError(f"Zotero {method} 请求失败：HTTP {e.code} ({path or '本机上传'})") from e

    def get(self, path):
        return self.request(path)[1]

    def item(self, key):
        if not re.fullmatch(r"[A-Z0-9]{8}", key):
            raise ValueError("条目 key 必须是 8 位字母数字。")
        return self.get(f"{self.root}/items/{key}")

    def listing(self, path):
        result = []
        for start in range(0, 1000000, 100):
            page = self.get(path + ("&" if "?" in path else "?") + f"limit=100&start={start}")
            result.extend(page)
            if len(page) < 100:
                return result
        raise ValueError("分页数量异常，停止。")

    def children(self, key):
        return self.listing(f"{self.root}/items/{key}/children")

    def file_path(self, key):
        raw = self.get(f"{self.root}/items/{key}/file/view/url")
        value = raw.decode("utf-8").strip().strip('"') if isinstance(raw, bytes) else raw
        p = urllib.parse.urlsplit(value)
        if p.scheme != "file" or p.netloc not in ("", "localhost"):
            raise ValueError("本机 API 未返回本地文件路径。")
        path = Path(urllib.request.url2pathname(p.path))
        if not path.is_file():
            raise ValueError("原 PDF 尚未下载到本机。")
        return path

    def authorize(self):
        _, result = self.request("/local/authorize", "POST", json.dumps({"appName": "Zotero GPT 全文翻译"}).encode(), {"Content-Type": "application/json"}, timeout=180)
        if not result.get("key"):
            raise ValueError("未获得 Zotero 本机写入授权；请由用户处理 Zotero 授权界面。")
        self.token = result["key"]

    def write(self, path, body, headers=None):
        if not self.token:
            self.authorize()
        return self.request(path, "POST", body, {"Zotero-API-Key": self.token, **(headers or {})})


def snapshot_links(page):
    """Keep opaque PDF Link annotations too, including named/remote destinations."""
    return [(xref, page.parent.xref_object(xref)) for xref, kind, *_ in page.annot_xrefs() if kind == fitz.PDF_ANNOT_LINK]


def restore_links(doc, page, originals, discard_xrefs=()):
    # Redactions can unlink Link annotations. Restore original objects without
    # reconstructing destination dictionaries (which could lose named targets).
    for link in page.get_links():
        if link.get("xref"):
            page.delete_link(link)
    kind, value = doc.xref_get_key(page.xref, "Annots")
    if kind == "xref":
        value = doc.xref_object(int(value.split()[0]))
    refs = re.findall(r"\d+\s+\d+\s+R", value) if kind != "null" else []
    original_refs = {f"{xref} 0 R" for xref, _ in originals}
    discard_refs = {f"{xref} 0 R" for xref in discard_xrefs}
    refs = [x for x in refs if x not in original_refs and x not in discard_refs]
    for xref, raw in originals:
        doc.update_object(xref, raw)
        refs.append(f"{xref} 0 R")
    doc.xref_set_key(page.xref, "Annots", "[" + " ".join(refs) + "]")


def rounded_rect(rect):
    return [round(float(x), 4) for x in rect]


def link_signature(page):
    # xrefs may change on save; destinations and hit rectangles must not.
    def clean(value):
        if isinstance(value, (fitz.Point, fitz.Rect)):
            return rounded_rect(value)
        if isinstance(value, float):
            return round(value, 4)
        return value
    return sorted([json.dumps({k: clean(v) for k, v in link.items() if k not in {"xref", "id"}}, sort_keys=True, ensure_ascii=False) for link in page.get_links()])


def link_target(link):
    """Destination only: old and new clickable rectangles must differ."""
    kind = link.get("kind")
    if kind is None:
        kind = fitz.LINK_URI if "uri" in link else fitz.LINK_NAMED if "nameddest" in link and "page" not in link else fitz.LINK_GOTO
    if kind in {fitz.LINK_GOTO, fitz.LINK_NAMED} and link.get("page", -1) >= 0:
        return {"kind": fitz.LINK_GOTO, "page": link["page"], "to": rounded_rect(link.get("to", [0, 0])), "zoom": round(float(link.get("zoom", 0)), 4)}
    if kind == fitz.LINK_URI:
        if not link.get("uri"):
            raise ValueError("外部链接缺少 URI。")
        return {"kind": kind, "uri": link["uri"]}
    if kind == fitz.LINK_NAMED:
        name = link.get("name") or link.get("nameddest")
        if not name:
            raise ValueError("命名链接缺少目标。")
        return {"kind": kind, "name": name}
    if kind == fitz.LINK_GOTO and link.get("page") == -1 and isinstance(link.get("to"), str):
        return {"kind": kind, "page": -1, "to": link["to"]}
    if kind in {fitz.LINK_GOTOR, fitz.LINK_LAUNCH}:
        target = {k: link[k] for k in ["kind", "file", "page", "to", "zoom"] if k in link}
        if not target.get("file"):
            raise ValueError("文件链接缺少目标。")
        if "to" in target and not isinstance(target["to"], str):
            target["to"] = rounded_rect(target["to"])
        return target
    raise ValueError("无法可靠识别链接目标，需明确映射；不能保留错位旧热区。")


def target_key(link):
    return json.dumps(link_target(link), sort_keys=True, ensure_ascii=False)


def plan_page_links(page, items):
    originals = snapshot_links(page)
    known = {link["xref"]: link for link in page.get_links() if link.get("xref")}
    retained, moved = [], []
    for xref, raw in originals:
        link = known.get(xref)
        if link is None:
            if items:
                raise ValueError("页面含无法解析的链接注释，需先核实其区域与目标。")
            retained.append((xref, raw))
            continue
        source_rect = fitz.Rect(link["from"]) * page.derotation_matrix
        affected = [item for item in items if intersection(source_rect, item["bbox"]) > 0.2 or any(intersection(source_rect, span["bbox"]) > 0.2 for span in item["source_spans"])]
        if not affected:
            retained.append((xref, raw))
            continue
        key = target_key(link)
        mapped = any(run.get("link") and target_key(run["link"]) == key for item in affected for run in item.get("runs", []))
        if not mapped:
            raise ValueError(f"第 {page.number+1} 页原链接需要迁移但缺少译文映射：{key}；请添加 runs，不得恢复旧位置或直接丢失链接。")
        moved.append(link)
    return retained, moved


def page_source(page):
    blocks = []
    for bi, block in enumerate(page.get_text("dict")["blocks"]):
        if block["type"] != 0:
            continue
        spans = []
        for li, line in enumerate(block["lines"]):
            for si, span in enumerate(line["spans"]):
                if not span["text"].strip():
                    continue
                spans.append({"id": f"p{page.number+1}-b{bi}-l{li}-s{si}", "text": span["text"], "bbox": rounded_rect(span["bbox"]), "font": span["font"], "size": span["size"], "color": span["color"], "flags": span["flags"], "direction": list(line["dir"])})
        if spans:
            blocks.append({"bbox": rounded_rect(block["bbox"]), "spans": spans})
    return {"page": page.number+1, "width": page.cropbox.width, "height": page.cropbox.height, "rotation": page.rotation, "blocks": blocks, "images": [rounded_rect(x["bbox"]) for x in page.get_image_info()], "links": link_signature(page)}


def prepare(args):
    require_pdf()
    work = Path(args.work).resolve()
    if work.exists() and any(work.iterdir()):
        raise ValueError("工作目录已有内容；继续使用现有翻译文件，或指定新的空目录。")
    binding = None
    if args.key:
        api = API(config_path(args.config))
        record = api.item(args.key)
        data = record["data"]
        if data.get("itemType") != "attachment" or data.get("contentType") != "application/pdf":
            raise ValueError("prepare --key 需要 PDF 附件 key；先 inspect 父条目。")
        if not data.get("parentItem"):
            raise ValueError("独立 PDF 附件没有父条目；先确定挂载目标。")
        source = api.file_path(args.key)
        parent = api.item(data["parentItem"])
        binding = {"config_path": str(api.config_path), "server_id": api.server_id, "parent_key": data["parentItem"], "source_key": args.key, "title": parent["data"].get("title", ""), "library_id": parent.get("library", {}).get("id")}
    else:
        source = Path(args.pdf).resolve()
    with fitz.open(source) as doc:
        if not doc.is_pdf or doc.needs_pass:
            raise ValueError("需要可读取且已解密的 PDF。")
        for page in doc:
            if any(kind == fitz.PDF_ANNOT_REDACT for _, kind, *_ in page.annot_xrefs()):
                raise ValueError("原 PDF 有待应用涂黑注释，停止以避免连带修改。")
        work.mkdir(parents=True, exist_ok=True)
        (work / "pages").mkdir()
        (work / "previews").mkdir()
        shutil.copyfile(source, work / "source.pdf")
        sources = []
        for page in doc:
            obj = page_source(page)
            path = work / "pages" / f"{page.number+1:04}.source.json"
            save(path, obj)
            sources.append(digest(path))
            save(work / "pages" / f"{page.number+1:04}.translation.json", {"page": page.number+1, "items": [], "keep": [], "protected_regions": [], "blank_page_confirmed": False})
            page.get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False).save(work / "previews" / f"{page.number+1:04}.png")
        save(work / "manifest.json", {"schema": 1, "source_sha256": digest(work / "source.pdf"), "source_original_path": str(source), "page_count": len(doc), "source_json_sha256": sources, "zotero": binding})
    draft_info = planning(work, "draft")
    sheets = contact_sheets(sorted((work/"previews").glob("*.png")), work/"source-contact")
    emit({"work": str(work), "pages": len(sources), "zotero": binding, "draft": draft_info, "contact_sheets": sheets, "next": "先读 reading.txt 和缩略图，编辑 segments.translation.json 后 compile；亦可直接填写 pages/*.translation.json。"})


def planning(work, command):
    import importlib.util
    from types import SimpleNamespace
    path = Path(__file__).with_name("translation_plan.py")
    spec = importlib.util.spec_from_file_location("translation_plan", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "draft" if command == "draft" else "compile_plan")(work, SimpleNamespace(**globals()))


def contact_sheets(paths, prefix):
    result = []
    for offset in range(0, len(paths), 18):
        batch = paths[offset:offset+18]
        sheet = fitz.open()
        page = sheet.new_page(width=960, height=math.ceil(len(batch)/3)*440)
        for index, path in enumerate(batch):
            x, y = index % 3 * 320, index // 3 * 440
            page.insert_text((x+10, y+16), str(offset+index+1), fontsize=12)
            page.insert_image(fitz.Rect(x+10,y+24,x+310,y+432), filename=str(path))
        output = Path(str(prefix)+f"-{offset//18+1:02}.png")
        page.get_pixmap(alpha=False).save(output)
        sheet.close()
        result.append(str(output))
    return result


def intersection(a, b):
    # Most rectangles on a page are disjoint. Avoid allocating two MuPDF
    # rectangles and their intersection for every candidate pair.
    width = min(a[2], b[2]) - max(a[0], b[0])
    if width <= 0:
        return 0.0
    height = min(a[3], b[3]) - max(a[1], b[1])
    return width * height if height > 0 else 0.0


class RectIndex:
    """Conservative grid index; exact intersection still makes every decision."""
    def __init__(self, entries, cell=32):
        self.cell = cell
        self.buckets = {}
        for key, box in entries:
            for slot in self.slots(box):
                self.buckets.setdefault(slot, []).append((key, box))

    def slots(self, box):
        for x in range(math.floor(box[0]/self.cell), math.floor(box[2]/self.cell)+1):
            for y in range(math.floor(box[1]/self.cell), math.floor(box[3]/self.cell)+1):
                yield x, y

    def candidates(self, box):
        seen = set()
        for slot in self.slots(box):
            for key, other in self.buckets.get(slot, ()):
                if key not in seen:
                    seen.add(key)
                    yield key, other


def rect(value):
    if not isinstance(value, list) or len(value) != 4 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in value):
        raise ValueError("bbox 必须包含四个有限坐标。")
    r = fitz.Rect(value)
    if r.is_empty or r.is_infinite:
        raise ValueError("bbox 不合法。")
    return r


def union(spans):
    r = fitz.Rect(spans[0]["bbox"])
    for span in spans[1:]:
        r |= fitz.Rect(span["bbox"])
    return r


def weighted_size(spans):
    total = sum(len(s["text"]) for s in spans)
    return sum(s["size"] * len(s["text"]) for s in spans) / total


def read_work(work):
    require_pdf()
    work = Path(work).resolve()
    manifest = load(work / "manifest.json")
    if digest(work / "source.pdf") != manifest["source_sha256"]:
        raise ValueError("工作副本 source.pdf 已变化，必须重新准备。")
    source, plans = [], []
    for i in range(manifest["page_count"]):
        p = work / "pages" / f"{i+1:04}.source.json"
        if digest(p) != manifest["source_json_sha256"][i]:
            raise ValueError("源提取 JSON 已修改；只能编辑 translation JSON。")
        source.append(load(p))
        plans.append(load(work / "pages" / f"{i+1:04}.translation.json"))
    return work, manifest, source, plans


def validate(work):
    work, manifest, source, plans = read_work(work)
    prepared, errors = [], []
    for sp, plan in zip(source, plans):
        try:
            if plan["page"] != sp["page"]:
                raise ValueError("翻译页码不匹配。")
            spans = {s["id"]: s for b in sp["blocks"] for s in b["spans"]}
            span_index = RectIndex((key, s["bbox"]) for key, s in spans.items())
            if not spans and not plan.get("blank_page_confirmed"):
                raise ValueError("该页无可提取文字，需检查扫描页/空白页；不得静默跳过。")
            covered, items, kept = set(), [], []
            for kind, groups in [("text", plan["items"]), ("keep", plan["keep"])]:
                for group in groups:
                    ids = group["source_ids"]
                    if not ids or len(ids) != len(set(ids)) or any(x not in spans or x in covered for x in ids):
                        raise ValueError("源 span 未知、重复或 source_ids 为空。")
                    covered.update(ids)
                    ss = [spans[x] for x in ids]
                    box = union(ss)
                    if kind == "keep":
                        if group["kind"] not in {"formula", "figure", "table", "metadata", "reference", "page-number"} or not group.get("reason", "").strip():
                            raise ValueError("保留文字必须有明确类别和理由。")
                        kept.extend(ss)
                        continue
                    text = group["text"]
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("中文译文不能为空。")
                    if not re.search(r"[\u3400-\u9fff]", text):
                        raise ValueError("译文没有中文；非翻译文字应明确分类保留。")
                    if any(abs(s["direction"][0]-1) > 0.01 or abs(s["direction"][1]) > 0.01 for s in ss):
                        raise ValueError("检测到旋转/竖排文字；需要专门处理，不能直接覆盖。")
                    target = rect(group["bbox"]) if "bbox" in group else fitz.Rect(box.x0, box.y0, box.x1, box.y1 + 2)
                    if not (fitz.Rect(0, 0, sp["width"], sp["height"]) + (-0.1, -0.1, 0.1, 0.1)).contains(target):
                        raise ValueError("译文框超出页面。")
                    if not (target + (-0.2, -0.2, 0.2, 0.2)).contains(box):
                        raise ValueError("译文框未覆盖其源文字。")
                    if abs(target.x0-box.x0) > 12 or abs(target.y0-box.y0) > 12 or target.width > box.width*1.2+2:
                        raise ValueError("译文框位置/栏宽变化过大，停止以免页面偏移。")
                    original = weighted_size(ss)
                    floor = min(original, max(7.0, original*0.8))
                    size = group.get("font_size", original)
                    if not isinstance(size, (int, float)) or not math.isfinite(size) or size < floor or size > original*1.25:
                        raise ValueError("字号低于可读下限或偏离原文字号。")
                    align = group.get("align", "left")
                    if align not in {"left", "center", "right", "justify"}:
                        raise ValueError("align 无效。")
                    maths = group.get("math", [])
                    mids = [m["id"] for m in maths]
                    markers = re.findall(r"\{\{math:([A-Za-z0-9_-]+)\}\}", text)
                    if len(mids) != len(set(mids)) or sorted(mids) != sorted(markers):
                        raise ValueError("公式标记缺失、重复或未知。")
                    for m in maths:
                        if not re.fullmatch(r"[A-Za-z0-9_-]+", m["id"]) or not (box + (-1, -1, 1, 1)).contains(rect(m["bbox"])):
                            raise ValueError("公式裁取区域必须在源段落内。")
                    items.append({**group, "bbox": list(target), "font_size": size, "floor": floor, "source_spans": ss, "align": align})
            missing = set(spans)-covered
            if missing:
                raise ValueError(f"尚未处理 {len(missing)} 个源 span，例如 {sorted(missing)[:4]}")
            protected = [rect(p["bbox"]) for p in plan.get("protected_regions", [])]
            for p in plan.get("protected_regions", []):
                if p.get("kind") not in {"figure", "table", "formula"} or not p.get("reason"):
                    raise ValueError("保护区域需注明图/表/公式类别和理由。")
            for i, item in enumerate(items):
                target = item["bbox"]
                for other in items[i+1:]:
                    if intersection(target, other["bbox"]) > 0.2:
                        raise ValueError("译文段落框重叠。")
                image_obstacles = sp["images"]
                if item.get("image_overlap_review"):
                    if len(item["image_overlap_review"].strip()) < 20:
                        raise ValueError("图片边界误报例外需具体记录目视核对证据。")
                    # PDF images may be clipped by the content stream. Their
                    # reported object bounds can extend into an existing caption.
                    # Restrict this opt-in exception to the original text footprint.
                    original_box = union(item["source_spans"])
                    if not (original_box + (-0.2, -0.2, 0.2, 2)).contains(fitz.Rect(target)):
                        raise ValueError("图片边界例外不可扩大到原文字区域以外。")
                    image_obstacles = []
                obstacles = [s["bbox"] for s in kept] + image_obstacles + [list(r) for r in protected]
                if any(intersection(target, b) > 0.2 for b in obstacles):
                    raise ValueError("译文框碰到保留文字、图片或图表公式保护区。")
                # Redaction removes any overlapping character, even outside its own span.
                own = set(item["source_ids"])
                for s in item["source_spans"]:
                    for oid, other_box in span_index.candidates(s["bbox"]):
                        if oid not in own and intersection(s["bbox"], other_box) > 0.2:
                            raise ValueError("源文字区域与其他组重叠，需合并段落或专门处理公式。")
            prepared.append(items)
        except (KeyError, TypeError, ValueError) as e:
            errors.append(f"第 {sp['page']} 页：{e}")
    if errors:
        raise ValueError("\n".join(errors))
    return work, manifest, source, plans, prepared


def plan_hash(work, count):
    return hashlib.sha256("".join(digest(work / "pages" / f"{i+1:04}.translation.json") for i in range(count)).encode()).hexdigest()


def rendering_fonts(assets, prepared):
    """Subset local body fonts once, before independent HTML Stories load them.

    Glyph outlines, metrics and hints are retained. Native math and figures are
    untouched; the final PDF still passes its existing font and geometry checks.
    """
    if not any(i.get("typography", {}).get("family") == "song" for page in prepared for i in page):
        return {}, {}
    from fontTools import subset, __version__ as fonttools_version
    from fontTools.ttLib import TTFont
    names = ["font-simsun.ttf", "font-times.ttf"]
    for name in names:
        if not (assets / name).is_file():
            raise ValueError("宋体样式需要工作目录 math-assets 中的本机宋体及 Times 字体。")
    source_hashes = {name: digest(assets/name) for name in names}
    codepoints = sorted({ord(c) for page in prepared for item in page for c in item["text"]} | set(range(32, 127)))
    key = hashlib.sha256(json.dumps([source_hashes, codepoints, fonttools_version], sort_keys=True).encode()).hexdigest()
    directory = assets / ".font-cache" / key
    directory.mkdir(parents=True, exist_ok=True)
    record_path = directory / "fonts.json"
    record = load(record_path) if record_path.exists() else {}
    hashes = {}
    for name in names:
        output = directory / name
        if not output.is_file() or digest(output) != record.get(name):
            options = subset.Options()
            options.recalc_timestamp = False
            options.layout_features = ["*"]
            options.drop_tables += ["FFTM", "MERG", "meta"]
            font = TTFont(assets / name)
            worker = subset.Subsetter(options=options)
            worker.populate(unicodes=codepoints)
            worker.subset(font)
            font.save(output)
            font.close()
        hashes[name] = digest(output)
    save(record_path, hashes)
    return {name: (directory/name).relative_to(assets).as_posix() for name in names}, source_hashes


def build_inputs(work, manifest):
    assets = work / "math-assets"
    from fontTools import __version__ as fonttools_version
    return {"engine": digest(__file__), "crop_engine": digest(Path(__file__).with_name("crop_guard.py")), "redaction_engine": digest(Path(__file__).with_name("redaction_guard.py")), "pymupdf": fitz.VersionBind, "fonttools": fonttools_version, "source": manifest["source_sha256"], "plans": [digest(work/"pages"/f"{i+1:04}.translation.json") for i in range(manifest["page_count"])], "fonts": {name: digest(assets/name) for name in ["font-simsun.ttf", "font-times.ttf"] if (assets/name).is_file()}}


def cached_quality(work, inputs):
    path = work / "quality.json"
    if not path.is_file():
        return None
    try:
        previous = load(path)
        old_output = work / previous["output"]
        if old_output.parent != work or previous.get("build_inputs") != inputs or digest(old_output) != previous["output_sha256"]:
            return None
        return previous, old_output
    except (OSError, ValueError, KeyError, TypeError):
        return None


def math_asset(original, page_number, crop, assets, source_hash):
    import importlib.util
    from types import SimpleNamespace
    module = globals().get('_crop_guard')
    if module is None:
        spec = importlib.util.spec_from_file_location('crop_guard', Path(__file__).with_name('crop_guard.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        globals()['_crop_guard'] = module
    return module.guarded_asset(original, page_number, crop, assets, source_hash, SimpleNamespace(**globals()))


def guarded_remove_text(page, items, source):
    import importlib.util
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location('redaction_guard', Path(__file__).with_name('redaction_guard.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.remove_text(page, items, source, SimpleNamespace(**globals()))


def redaction_regions(items, source):
    """Batch a paragraph only when its box touches no foreign source text.

    Graphics and images remain untouched by apply_redactions. A paragraph with
    any intervening kept/other text uses the original per-span rectangles.
    """
    index = RectIndex((s['id'], s['bbox']) for b in source['blocks'] for s in b['spans'])
    for item in items:
        own = set(item['source_ids'])
        box = union(item['source_spans'])
        if any(key not in own and intersection(box, other) > 0
               for key, other in index.candidates(box)):
            yield from (s['bbox'] for s in item['source_spans'])
        else:
            yield box


def paragraph_markup(item, run_links, original, pno, assets, source_hash, preview_glyphs=None):
    raw_text = item["text"]
    fragments, cursor = [], 0
    for run in sorted(item.get("runs", []), key=lambda r: r["start"]):
        start, end = run["start"], run["end"]
        if start < cursor or end <= start or raw_text[start:end] != run["text"]:
            raise ValueError("引用区间重叠或与原文不符。")
        if run.get("kind") not in {"citation", "crossref", "hyperlink"}:
            raise ValueError("引用标注类别无效。")
        if len(run_links) >= 150:
            raise ValueError("单页引用标注数量过多。")
        # Encode unique locator colors without carrying blue into
        # green on citation-heavy pages (the previous +index did).
        index = len(run_links)
        color_key = (0xB02020 if run["kind"] == "crossref" else 0x0060A0) + ((index % 40) << 8) + index // 40
        run_links[color_key] = run.get("link")
        fragments.extend([html.escape(raw_text[cursor:start]), f'<span style="color:#{color_key:06x}">'+html.escape(raw_text[start:end])+"</span>"])
        cursor = end
    fragments.append(html.escape(raw_text[cursor:]))
    text = "".join(fragments).replace("\n", "<br>")
    for m in item.get("math", []):
        name = math_asset(original, pno, m, assets, source_hash)
        proof = load((assets/name).with_suffix('.verified.json'))
        r = fitz.Rect(proof['bbox'])
        tag = f'<img src="{name}" style="width:{r.width}pt;height:{r.height}pt;vertical-align:middle">'
        text = text.replace("{{math:"+m["id"]+"}}", tag)
    return text


def paragraph_style(item, text, assets, font_files):
    size = item["font_size"]
    # CJK font comes from MuPDF's bundled Noto fallback and is embedded.
    bold = sum(len(s["text"]) for s in item["source_spans"] if s["flags"] & 16) > sum(len(s["text"]) for s in item["source_spans"])/2
    color = item["source_spans"][0]["color"]
    typography = item.get("typography", {})
    family = "sans-serif"
    font_css = ""
    line_height = float(typography.get("line_height", 1.05))
    indent = float(typography.get("indent_em", 0))
    if not 1 <= line_height <= 2 or not 0 <= indent <= 3:
        raise ValueError("行距或首行缩进超出允许范围。")
    if typography.get("family") == "song":
        for font_file in ["font-simsun.ttf", "font-times.ttf"]:
            if not (assets / font_file).is_file():
                raise ValueError("宋体样式需要工作目录 math-assets 中的本机宋体及 Times 字体。")
        family = "TranslationSong"
        font_css = "@font-face {font-family:TranslationSong;src:url("+font_files["font-simsun.ttf"]+")} @font-face {font-family:TranslationLatin;src:url("+font_files["font-times.ttf"]+")}"
        # MuPDF selects the first family before CJK fallback rather
        # than following a CSS family list for missing characters.
        # Give Latin text its own explicit face without touching tags.
        pieces = re.split(r"(<[^>]+>)", text)
        for pi in range(0, len(pieces), 2):
            plain = html.unescape(pieces[pi])
            rebuilt, cursor = [], 0
            for match in re.finditer(r"[A-Za-z][A-Za-z0-9 .+/-]*[A-Za-z0-9]|[A-Za-z]", plain):
                rebuilt.append(html.escape(plain[cursor:match.start()]))
                rebuilt.append('<span style="font-family:TranslationLatin">'+html.escape(match.group(0))+'</span>')
                cursor = match.end()
            rebuilt.append(html.escape(plain[cursor:]))
            pieces[pi] = "".join(rebuilt)
        text = "".join(pieces)
    if typography.get("weight") == "normal":
        bold = False
    alignment = typography.get("align", item["align"])
    if alignment not in {"left", "center", "right", "justify"}:
        raise ValueError("正文对齐方式无效。")
    css = font_css + f"* {{margin:0;padding:0}} body {{font-family:{family};font-size:{size}pt;line-height:{line_height};color:#{color:06x};font-weight:{'bold' if bold else 'normal'};text-align:{alignment}}} p {{margin:0;padding:0;text-indent:{indent}em}}"
    if indent:
        text = "<p>" + text + "</p>"
    return text, css


def layout_preflight(work, manifest, sources, prepared, font_files=None):
    """Check every paragraph with build's exact markup, fonts and layout limits.

    Only scratch pages and disposable asset caches are written. Never creates a
    delivery PDF, changes plans, lowers font floors or authorizes a review.
    """
    started = time.perf_counter()
    assets = work / "math-assets"
    assets.mkdir(exist_ok=True)
    if font_files is None:
        font_files, _ = rendering_fonts(assets, prepared)
    errors, checked, page_times = [], 0, []
    preview_glyphs = {}
    archive = fitz.Archive(str(assets))
    with fitz.open(work / "source.pdf") as original:
        for pno, items in enumerate(prepared):
            page_start = time.perf_counter()
            run_links = {}
            with fitz.open() as scratch:
                page = scratch.new_page(width=sources[pno]["width"], height=sources[pno]["height"])
                for item in items:
                    checked += 1
                    try:
                        text = paragraph_markup(item, run_links, original, pno, assets, manifest["source_sha256"], preview_glyphs)
                        text, css = paragraph_style(item, text, assets, font_files)
                        spare, scale = page.insert_htmlbox(item["bbox"], text, css=css, archive=archive, scale_low=item["floor"]/item["font_size"])
                        if spare < 0 or item["font_size"]*scale < item["floor"]-0.01:
                            raise ValueError("段落放不下；请调整忠实译文或经过核实的区域，不降低字号下限。")
                    except (ValueError, KeyError, TypeError) as exc:
                        errors.append({"page": pno+1, "source_ids": item["source_ids"], "bbox": item["bbox"], "error": str(exc)})
            page_times.append({"page": pno+1, "seconds": time.perf_counter()-page_start})
    report = {"valid": not errors, "checked_paragraphs": checked, "errors": errors,
              "source_sha256": manifest["source_sha256"], "plan_sha256": plan_hash(work, manifest["page_count"]),
              "seconds": time.perf_counter()-started, "pages": page_times,
              "formula_isolation_checked": True}
    save(work / "preflight.json", report)
    return report


def require_layout(report):
    if report["errors"]:
        raise ValueError("排版预检发现以下问题（已检查全部段落；详见 preflight.json）：\n" + "\n".join(
            f"第 {e['page']} 页 {e['source_ids'][0]}：{e['error']}" for e in report["errors"]))


def preflight(args):
    work, manifest, sources, _, prepared = validate(args.work)
    report = layout_preflight(work, manifest, sources, prepared)
    emit(report)
    require_layout(report)


def build(args):
    started = time.perf_counter()
    work, manifest, sources, plans, prepared = validate(args.work)
    validated_at = time.perf_counter()
    output = work / "中文全文译文.pdf"
    if output.exists() and not args.output:
        raise ValueError("译文 PDF 已存在。请用 --output 指定新的 PDF 文件名以保留旧版本。")
    if args.output:
        output = work / Path(args.output).name
        if output.suffix.lower() != ".pdf" or output.exists():
            raise ValueError("--output 应为未使用的 PDF 文件名。")
    inputs = build_inputs(work, manifest)
    cached = cached_quality(work, inputs)
    if cached:
        quality, previous = cached
        shutil.copyfile(previous, output)
        quality["output"] = output.name
        quality["timings_seconds"] = {"validation": validated_at-started, "total": time.perf_counter()-started}
        quality["reused_complete_pdf"] = True
        save(work / "quality.json", quality)
        emit({"output": str(output), "pages": quality["page_count"], "reused_complete_pdf": True, "seconds": quality["timings_seconds"]["total"], "next": "相同计划和已核验 PDF 完整复用；此前未复核时仍须 render/review。"})
        return
    doc = fitz.open(work / "source.pdf")
    original = fitz.open(work / "source.pdf")
    measurements = []
    added_link_count = 0
    link_checks = []
    stages = {"validation": validated_at-started}
    page_times = []
    try:
        assets = work / "math-assets"
        assets.mkdir(exist_ok=True)
        step = time.perf_counter()
        font_files, font_hashes = rendering_fonts(assets, prepared)
        stages["font_preparation"] = time.perf_counter()-step
        step = time.perf_counter()
        require_layout(layout_preflight(work, manifest, sources, prepared, font_files))
        stages["layout_preflight"] = time.perf_counter()-step
        archive = fitz.Archive(str(assets))
        for pno, items in enumerate(prepared):
            page_start = time.perf_counter()
            page = doc[pno]
            links, moved_links = plan_page_links(page, items)
            moved_xrefs = [link["xref"] for link in moved_links]
            preserved_signatures = Counter(link_signature(page))
            for link in moved_links:
                # Match the existing serialized entry by its unchanged rectangle.
                for signature in list(preserved_signatures):
                    obj = json.loads(signature)
                    if obj.get("from") == rounded_rect(link["from"]) and target_key(obj) == target_key(link):
                        preserved_signatures[signature] -= 1
                        if preserved_signatures[signature] <= 0:
                            del preserved_signatures[signature]
                        break
            run_links = {}
            contents = []
            for item in items:
                contents.append(paragraph_markup(item, run_links, original, pno, assets, manifest["source_sha256"]))
            guarded_remove_text(page, items, sources[pno])
            for item, text in zip(items, contents):
                size = item["font_size"]
                typography = item.get("typography", {})
                text, css = paragraph_style(item, text, assets, font_files)
                spare, scale = page.insert_htmlbox(item["bbox"], text, css=css, archive=archive, scale_low=item["floor"]/size)
                if spare < 0 or size*scale < item["floor"]-0.01:
                    raise ValueError(f"第 {pno+1} 页段落 {item['source_ids'][0]} 放不下；不会加页或跳过。请调整忠实译文/核实空白区域。")
                if typography.get("family") == "song":
                    chinese_spans = [s for b in page.get_text("dict", clip=fitz.Rect(item["bbox"]))["blocks"] if b["type"] == 0 for line in b["lines"] for s in line["spans"] if re.search(r"[\u3400-\u9fff]", s["text"])]
                    if not chinese_spans or any("simsun" not in s["font"].lower() for s in chinese_spans):
                        raise ValueError(f"第 {pno+1} 页宋体未实际生效，拒绝静默使用回退字体。")
                measurements.append({"page": pno+1, "source_ids": item["source_ids"], "scale": scale, "font_size": size*scale, "bbox": item["bbox"], "image_overlap_review": item.get("image_overlap_review")})
            restore_links(doc, page, links, moved_xrefs)
            seen_runs = set()
            page_added = 0
            if run_links:
                for block in page.get_text("dict")["blocks"]:
                    if block["type"] != 0:
                        continue
                    for line in block["lines"]:
                        for span in line["spans"]:
                            target = run_links.get(span["color"])
                            if target:
                                seen_runs.add(span["color"])
                                destination = link_target(target)
                                if destination["kind"] == fitz.LINK_GOTO and destination["page"] >= len(doc):
                                    raise ValueError("引用跳转目标页无效。")
                                if "to" in destination and not isinstance(destination["to"], str):
                                    destination["to"] = fitz.Point(destination["to"])
                                    if destination["kind"] == fitz.LINK_GOTO and destination["page"] >= 0:
                                        destination["to"] *= doc[destination["page"]].derotation_matrix
                                page.insert_link({**destination, "from": fitz.Rect(span["bbox"])})
                                added_link_count += 1
                                page_added += 1
            if any(target and color not in seen_runs for color, target in run_links.items()):
                raise ValueError("有链接未找到实际译文文字区域，停止以避免丢失或错位。")
            page = doc.reload_page(page)
            active_xrefs = {xref for xref, kind, *_ in page.annot_xrefs() if kind == fitz.PDF_ANNOT_LINK}
            if active_xrefs.intersection(moved_xrefs):
                raise ValueError("旧原文链接热区仍存在，停止。")
            if preserved_signatures - Counter(link_signature(page)) or len(page.get_links()) != sum(preserved_signatures.values()) + page_added:
                raise ValueError("链接数量与保留/重建记录不一致。")
            link_checks.append({"retained_raw": [raw for _, raw in links], "expected_links": link_signature(page), "removed_xrefs": moved_xrefs, "removed_original_regions": len(moved_links)})
            page_times.append({"page": pno+1, "seconds": time.perf_counter()-page_start})
        # Original destination xref references remain stable (no renumbering).
        # Embed only glyphs actually used; CJK font collections otherwise add
        # their full payload for every independent Story text region.
        step = time.perf_counter()
        doc.subset_fonts()
        stages["font_subset"] = time.perf_counter()-step
        # Level 1 removes the now-unreferenced full font streams but preserves
        # xref numbering, including original link and bookmark destinations.
        step = time.perf_counter()
        doc.save(output, deflate=True, garbage=1)
        stages["pdf_save"] = time.perf_counter()-step
    finally:
        doc.close()
        original.close()
    with fitz.open(work / "source.pdf") as source_doc, fitz.open(output) as out:
        if len(out) != len(source_doc) or out.get_toc(simple=False) != source_doc.get_toc(simple=False):
            raise ValueError("输出页数/书签不一致，停止。")
        page_checks = []
        for a, b in zip(source_doc, out):
            same_geometry = a.rect == b.rect and a.cropbox == b.cropbox and a.rotation == b.rotation
            check = link_checks[a.number]
            links_ok = link_signature(b) == check["expected_links"] and not (Counter(check["retained_raw"]) - Counter(raw for _, raw in snapshot_links(b))) and not ({xref for xref, _ in snapshot_links(b)} & set(check["removed_xrefs"]))
            if not same_geometry or not links_ok:
                raise ValueError(f"第 {a.number+1} 页尺寸/链接验证失败，停止。")
            page_checks.append({"page": a.number+1, "geometry_ok": True, "links_ok": True, "links": link_signature(b), "removed_original_link_regions": check["removed_original_regions"]})
    quality = {"schema": 1, "output": output.name, "output_sha256": digest(output), "source_sha256": manifest["source_sha256"], "plan_sha256": plan_hash(work, manifest["page_count"]), "page_count": manifest["page_count"], "coverage_ok": True, "bookmarks_ok": True, "pages": page_checks, "paragraphs": measurements, "min_scale": min((x["scale"] for x in measurements), default=1), "kept": [{"page": p["page"], "keep": p["keep"]} for p in plans]}
    quality["added_reference_link_regions"] = added_link_count
    quality["rebuilt_link_regions"] = added_link_count
    quality["removed_original_link_regions"] = sum(check["removed_original_regions"] for check in link_checks)
    quality["retained_original_link_regions"] = sum(len(check["retained_raw"]) for check in link_checks)
    quality["timings_seconds"] = {**stages, "page_processing": sum(p["seconds"] for p in page_times), "total": time.perf_counter()-started}
    quality["page_timings_seconds"] = page_times
    quality["native_kept_text_verified"] = True
    quality["build_inputs"] = inputs
    quality["reused_complete_pdf"] = False
    save(work / "quality.json", quality)
    emit({"output": str(output), "pages": quality["page_count"], "paragraphs": len(measurements), "min_scale": quality["min_scale"], "next": "render 后视觉检查，review 后才可 attach。"})


def current_quality(work):
    work = Path(work).resolve()
    manifest = load(work / "manifest.json")
    q = load(work / "quality.json")
    if q.get('build_inputs') != build_inputs(work, manifest):
        raise ValueError('生成引擎、字体或字形检查规则已变化；请重建并复核，不沿用旧版通过记录。')
    output = work / q["output"]
    if output.parent != work or digest(output) != q["output_sha256"] or digest(work / "source.pdf") != q["source_sha256"] or plan_hash(work, manifest["page_count"]) != q["plan_sha256"]:
        raise ValueError("产物或翻译计划已变化；请重建并复核。")
    return work, manifest, q, output


def render(args):
    require_pdf()
    work, _, quality, output = current_quality(args.work)
    directory = work / "translated-previews"
    directory.mkdir(exist_ok=True)
    index_path = directory / "index.json"
    previous = load(index_path) if index_path.exists() else {}
    expected = previous.get("files", {}) if previous.get("pdf_sha256") == quality['output_sha256'] else {}
    files = {}; reused = 0
    with fitz.open(output) as doc:
        for page in doc:
            path = directory / f"{page.number+1:04}.png"
            if path.is_file() and expected.get(path.name) == digest(path):
                reused += 1
            else:
                page.get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False).save(path)
            files[path.name] = digest(path)
    sheets = previous.get("contact_sheets", []) if reused == len(files) else []
    if not sheets or any(not Path(p).is_file() for p in sheets):
        sheets = contact_sheets([directory/name for name in files], work/"translated-contact")
    save(index_path, {"pdf_sha256":quality['output_sha256'],"files":files,"contact_sheets":sheets})
    emit({"previews": str(directory), "pages": len(files), "reused_previews":reused,"contact_sheets":sheets})


def review(args):
    work, _, q, _ = current_quality(args.work)
    if len(args.note.strip()) < 12:
        raise ValueError("需要具体的人工检查说明。")
    save(work / "review.json", {"output_sha256": q["output_sha256"], "plan_sha256": q["plan_sha256"], "note": args.note})
    emit({"reviewed": True, "note": args.note})


def attach(args, api_factory=API):
    if not args.yes:
        raise ValueError("仅在用户要求添加该论文译文时运行 attach --yes。")
    work, manifest, q, output = current_quality(args.work)
    review_data = load(work / "review.json")
    if any(review_data.get(k) != q[k] for k in ["output_sha256", "plan_sha256"]):
        raise ValueError("当前译文未完成人工复核。")
    binding = manifest.get("zotero")
    if not binding:
        raise ValueError("本地 PDF 任务没有 Zotero 绑定；不能猜测父条目。")
    api = api_factory(config_path(args.config or binding["config_path"]))
    if api.server_id != binding["server_id"]:
        raise ValueError("当前 Zotero 实例不匹配，停止。")
    parent = api.item(binding["parent_key"])
    if parent["data"].get("itemType") in {"attachment", "note"} or parent["data"].get("deleted"):
        raise ValueError("父条目不可用。")
    if parent.get("library", {}).get("id") != binding["library_id"]:
        raise ValueError("父条目所在文库变化，停止。")
    source = api.item(binding["source_key"])
    if source["data"].get("parentItem") != binding["parent_key"] or source["data"].get("deleted"):
        raise ValueError("原附件已移出对应条目。")
    if digest(api.file_path(binding["source_key"])) != manifest["source_sha256"]:
        raise ValueError("Zotero 中原 PDF 已改变，停止。")
    filename = f"GPT-zh-{binding['source_key']}-{q['output_sha256'][:12]}.pdf"
    title = manifest.get("attachment_title") or f"中文全文译文 · GPT · {binding['source_key']}"
    children = api.children(binding["parent_key"])
    same = [x for x in children if x["data"].get("filename") == filename]
    if len(same) > 1:
        raise ValueError("同名译文附件多于一个，停止以免重复。")
    journal_path = work / "attachment.json"
    journal = load(journal_path) if journal_path.exists() else None
    if journal and journal.get("output_sha256") != q["output_sha256"]:
        raise ValueError("本工作目录曾上传不同版本；请用新工作目录保留历史。")
    if journal and journal.get("state") == "creating" and not same:
        raise ValueError("上次创建结果未知，先 inspect 父条目核实，不自动重建。")
    key = same[0]["key"] if same else (journal.get("attachment_key") if journal else None)
    if key and key not in {x["key"] for x in children}:
        raise ValueError("日志中的附件已不存在或移走，停止。")
    if not key:
        save(journal_path, {"state": "creating", "output_sha256": q["output_sha256"], "filename": filename, "parent_key": binding["parent_key"]})
        record = {"itemType": "attachment", "parentItem": binding["parent_key"], "linkMode": "imported_file", "title": title, "contentType": "application/pdf", "filename": filename, "tags": [{"tag": "GPT中文全文译文"}], "relations": {}, "note": ""}
        _, result = api.write(api.root + "/items", json.dumps([record], ensure_ascii=False).encode(), {"Content-Type": "application/json", "Zotero-Write-Token": uuid.uuid4().hex})
        if result.get("failed") or "0" not in result.get("success", {}):
            raise ValueError("Zotero 创建附件失败；查看父条目后重试，不删除任何条目。")
        key = result["success"]["0"]
        save(journal_path, {"state": "created", "attachment_key": key, "output_sha256": q["output_sha256"], "filename": filename, "parent_key": binding["parent_key"]})
    data = api.item(key)["data"]
    if data.get("parentItem") != binding["parent_key"] or data.get("contentType") != "application/pdf" or data.get("linkMode") != "imported_file":
        raise ValueError("目标附件属性不匹配，停止。")
    raw = output.read_bytes()
    md5 = hashlib.md5(raw).hexdigest()
    if data.get("md5") and data["md5"].lower() != md5:
        raise ValueError("已有附件内容不同，不覆盖。")
    endpoint = f"{api.root}/items/{key}/file"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "If-None-Match": "*"}
    if not data.get("md5"):
        params = urllib.parse.urlencode({"md5": md5, "filename": filename, "filesize": len(raw), "mtime": int(output.stat().st_mtime*1000)}).encode()
        _, grant = api.write(endpoint, params, headers)
        if not grant.get("exists"):
            url = urllib.parse.urljoin(api.base+"/", grant["url"])
            api.local_url(url)
            body = grant.get("prefix", "").encode()+raw+grant.get("suffix", "").encode()
            status, _ = api.request("", "POST", body, {"Content-Type": grant["contentType"]}, url=url)
            if status != 201:
                raise ValueError("附件二进制上传失败。保留日志，避免重新创建。")
            status, _ = api.write(endpoint, urllib.parse.urlencode({"upload": grant["uploadKey"]}).encode(), headers)
            if status != 204:
                raise ValueError("附件上传登记失败，请核实后重试。")
    actual = api.item(key)["data"]
    if actual.get("parentItem") != binding["parent_key"] or actual.get("md5", "").lower() != md5:
        raise ValueError("附件父条目/MD5 核验失败，保留日志。")
    if digest(api.file_path(key)) != q["output_sha256"]:
        raise ValueError("附件本机文件 SHA256 核验失败。")
    result = {"state": "verified", "parent_key": binding["parent_key"], "attachment_key": key, "filename": filename, "output_sha256": q["output_sha256"]}
    save(journal_path, result)
    emit(result)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config")
    subs = p.add_subparsers(dest="command", required=True)
    subs.add_parser("doctor")
    s = subs.add_parser("connect"); s.add_argument("--workspace", default=".")
    s = subs.add_parser("fonts"); s.add_argument("--work", required=True)
    for command in ["draft", "compile"]:
        s = subs.add_parser(command); s.add_argument("--work", required=True)
    s = subs.add_parser("search"); s.add_argument("--query", required=True)
    s = subs.add_parser("inspect"); s.add_argument("--key", required=True)
    s = subs.add_parser("prepare")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--key"); g.add_argument("--pdf")
    s.add_argument("--work", required=True)
    for cmd in ["validate", "preflight", "build", "render", "review", "attach"]:
        s = subs.add_parser(cmd); s.add_argument("--work", required=True)
        if cmd == "build": s.add_argument("--output")
        if cmd == "review": s.add_argument("--note", required=True)
        if cmd == "attach": s.add_argument("--yes", action="store_true")
    args = p.parse_args()
    if args.command == "doctor":
        try:
            import fontTools
            fonttools_version = fontTools.__version__
        except ImportError:
            fonttools_version = None
        ready = fitz is not None and fonttools_version is not None
        emit({"python": sys.executable, "pymupdf": fitz.VersionBind if fitz else None, "fonttools": fonttools_version, "ready": ready})
        if not ready:
            sys.exit(1)
    elif args.command == "connect": connect(args)
    elif args.command == "fonts": emit(prepare_fonts(args.work))
    elif args.command in {"draft", "compile"}: emit(planning(args.work, args.command))
    elif args.command in {"search", "inspect"}:
        api = API(config_path(args.config))
        if args.command == "search":
            records = api.listing(api.root+"/items/top?q="+urllib.parse.quote(args.query)+"&qmode=titleCreatorYear")
        else:
            item = api.item(args.key)
            records = [item] if item["data"]["itemType"] == "attachment" else [item]+api.children(args.key)
        emit([{"key": x["key"], "itemType": x["data"]["itemType"], "title": x["data"].get("title"), "parentItem": x["data"].get("parentItem"), "contentType": x["data"].get("contentType")} for x in records])
    elif args.command == "prepare": prepare(args)
    elif args.command == "validate":
        _, m, _, _, prepared = validate(args.work)
        emit({"valid": True, "pages": m["page_count"], "paragraphs": sum(map(len, prepared))})
    else:
        globals()[args.command](args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
