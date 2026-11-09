"""Offline behavioral tests. Never contacts or changes the real Zotero library."""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import subprocess

sys.path.insert(0, os.environ["ZGT_DEPS"])
import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugin/skills/zotero-gpt-translate/scripts/translate.py"
spec = importlib.util.spec_from_file_location("translate", SCRIPT)
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)


def args(**kw):
    return argparse.Namespace(**{"config":None,"output":None,**kw})


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


def make_fixture(directory):
    source = directory / "source.pdf"
    doc = fitz.open()
    doc.new_page(width=600, height=800)
    doc.new_page(width=600, height=800)
    for i in range(2):
        p = doc[i]
        p.insert_text((40, 45), "Layout-preserving translation", fontsize=16)
        p.insert_textbox((40, 70, 280, 145), "A model predicts a motion sequence from text. The output must preserve all pages and hyperlinks.", fontsize=11)
        p.insert_textbox((320, 70, 560, 145), "We compare two people interacting. The method retains equations and the original table below.", fontsize=11)
        p.insert_text((50, 210), "E = m c^2", fontsize=13)
        p.draw_rect((320, 175, 550, 270), color=(0, 0, 0), fill=(0.9, 0.95, 1))
        p.draw_line((320, 210), (550, 210), color=(0, 0, 0))
        p.insert_text((335, 198), "Method     Score", fontsize=11)
        p.insert_text((335, 240), "Baseline    0.8", fontsize=11)
        p.insert_textbox((40, 310, 560, 350), "The project website links to further details and the next section.", fontsize=11, color=(0, 0.2, 0.6))
        p.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(40, 310, 200, 325), "uri": "https://example.org/paper?a=1&b=2"})
        p.insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(210, 310, 350, 325), "page": 1-i, "to": fitz.Point(40, 70)})
        p.insert_text((290, 770), str(i+1), fontsize=9)
        p.insert_link({'kind':fitz.LINK_URI,'from':fitz.Rect(288,760,315,773),'uri':'https://example.org/kept'})
        pix = fitz.Pixmap(fitz.csRGB, (0, 0, 20, 20))
        pix.clear_with(0x77)
        p.insert_image(fitz.Rect(40, 250, 100, 290), stream=pix.tobytes("png"))
    doc[1].set_rotation(90)
    doc.set_toc([[1, "Introduction", 1], [1, "Experiments", 2]])
    doc.save(source)
    doc.close()
    return source


def write_plan(work):
    for source in sorted((work / "pages").glob("*.source.json")):
        sp = t.load(source)
        plan = {"page": sp["page"], "items": [], "keep": [], "protected_regions": [{"bbox": [319, 174, 551, 271], "kind": "table", "reason": "保留原始表格及内部文字"}]}
        for block in sp["blocks"]:
            ss = block["spans"]
            ids = [s["id"] for s in ss]
            txt = " ".join(s["text"] for s in ss)
            if "E =" in txt or "Method" in txt or "Baseline" in txt or txt.isdigit():
                kind = "formula" if "E =" in txt else "page-number" if txt.isdigit() else "table"
                plan["keep"].append({"source_ids": ids, "kind": kind, "reason": "原样保留"})
            else:
                translation = "保持原页排版的全文翻译" if txt.startswith("Layout") else "模型根据文本预测动作序列，输出必须保留全部页面及超链接。" if txt.startswith("A model") else "我们比较两人的交互，保留公式及下方的原始表格。" if txt.startswith("We compare") else "项目网站链接提供更多细节，并可跳转至下一节。"
                box = list(t.union(ss)); box[3] += 7
                item = {"source_ids": ids, "text": translation, "bbox": box}
                if txt.startswith('The project'):
                    item['runs'] = []
                    original_goto=next(json.loads(l) for l in sp['links'] if json.loads(l)['kind']==fitz.LINK_GOTO)
                    for label, kind, link in [('项目网站','hyperlink',{'kind':fitz.LINK_URI,'uri':'https://example.org/paper?a=1&b=2'}),('下一节','crossref',t.link_target(original_goto))]:
                        start=translation.index(label)
                        item['runs'].append({'start':start,'end':start+len(label),'text':label,'kind':kind,'link':link})
                plan["items"].append(item)
        t.save(source.with_name(source.name.replace("source", "translation")), plan)


class Pipeline(unittest.TestCase):
    def test_native_equation_survives_nearby_body_removal(self):
        doc=fitz.open();page=doc.new_page(width=250,height=200)
        page.insert_text((80,100),'E',fontsize=48)
        page.insert_text((20,118),'body text below equation',fontsize=9)
        source=t.page_source(page)
        spans=[s for b in source['blocks'] for s in b['spans'] if 'body' in s['text']]
        items=[{'source_ids':[s['id'] for s in spans],'source_spans':spans}]
        original=[c['origin'] for b in page.get_text('rawdict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans'] for c in s['chars'] if c['c']=='E']
        t.guarded_remove_text(page,items,source)
        self.assertIn('E',page.get_text());self.assertNotIn('body',page.get_text())
        actual=[c['origin'] for b in page.get_text('rawdict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans'] for c in s['chars'] if c['c']=='E']
        self.assertEqual(actual,original)

    def test_unverified_image_is_not_a_cache_hit(self):
        original=fitz.open();original.new_page();original[0].insert_text((50,80),'x')
        glyph=next(c for b in original[0].get_text('rawdict')['blocks'] if b['type']==0 for l in b['lines'] for sp in l['spans'] for c in sp['chars'])
        crop={'id':'x','bbox':list(glyph['bbox']),'glyphs':[{'c':glyph['c'],'origin':list(glyph['origin'])}]}
        assets=self.directory/'proof-cache';assets.mkdir()
        name=t.math_asset(original,0,crop,assets,'fixture');record=(assets/name).with_suffix('.verified.json');proof=t.load(record);proof['verified']=False;t.save(record,proof)
        with patch.object(fitz.Page,'get_pixmap',side_effect=AssertionError('revalidation required')):
            with self.assertRaisesRegex(AssertionError,'revalidation'):t.math_asset(original,0,crop,assets,'fixture')
        t.math_asset(original,0,crop,assets,'fixture');self.assertTrue(t.load(record)['verified'])

    def test_reference_clipped_by_graphic_is_rejected_without_cache(self):
        original=fitz.open();original.new_page(width=200,height=150);original[0].insert_text((50,80),'x',fontsize=18);original[0].draw_line((0,75),(200,75))
        glyph=next(c for b in original[0].get_text('rawdict')['blocks'] if b['type']==0 for l in b['lines'] for sp in l['spans'] for c in sp['chars'])
        assets=self.directory/'blocked-crops';assets.mkdir()
        with self.assertRaises(ValueError):t.math_asset(original,0,{'id':'x','bbox':list(glyph['bbox']),'glyphs':[{'c':glyph['c'],'origin':list(glyph['origin'])}]},assets,'fixture')
        self.assertFalse(list(assets.glob('*.png')));self.assertFalse(list(assets.glob('*.verified.json')))

    def test_layout_preflight_checks_real_formula_images(self):
        path = self.work/'pages/0001.translation.json'
        plan = t.load(path)
        item = plan['items'][0]
        source = t.load(self.work/'pages/0001.source.json')
        span = next(s for b in source['blocks'] for s in b['spans'] if s['id'] == item['source_ids'][0])
        item['text'] = '标题{{math:probe}}'
        item['math'] = [{'id':'probe', 'bbox':span['bbox']}]
        t.save(path, plan)
        with patch.object(t, 'math_asset', wraps=t.math_asset) as checked:
            quiet(t.preflight, args(work=str(self.work)))
        report = t.load(self.work/'preflight.json')
        self.assertTrue(report['valid'])
        self.assertTrue(report['formula_isolation_checked'])
        self.assertGreater(checked.call_count,0)
        self.assertTrue(list((self.work/'math-assets').glob('math-*.verified.json')))
        self.assertFalse((self.work/'中文全文译文.pdf').exists())

    def test_preflight_collects_multiple_failures_without_changing_plans_or_pdf(self):
        paths = sorted((self.work/'pages').glob('*.translation.json'))
        first, second = (t.load(p) for p in paths)
        first['items'][0]['text'] = '此处故意放入过长译文以验证批量排版检查。'*500
        second['items'][0]['text'] = '另一页也必须在同一轮报告排版失败。'*500
        linked = next(i for i in first['items'] if i.get('runs'))
        linked['text'] = '改字'+linked['text']
        t.save(paths[0], first); t.save(paths[1], second)
        before = [t.digest(p) for p in paths]
        with self.assertRaisesRegex(ValueError, '排版预检'):
            quiet(t.build, args(work=str(self.work)))
        report = t.load(self.work/'preflight.json')
        self.assertFalse(report['valid'])
        self.assertEqual(report['checked_paragraphs'], len(first['items'])+len(second['items']))
        self.assertEqual({e['page'] for e in report['errors']}, {1, 2})
        self.assertTrue(any('引用区间' in e['error'] for e in report['errors']))
        self.assertGreaterEqual(sum('放不下' in e['error'] for e in report['errors']), 2)
        self.assertEqual(before, [t.digest(p) for p in paths])
        self.assertFalse((self.work/'中文全文译文.pdf').exists())
        self.assertFalse((self.work/'review.json').exists())

    def test_paragraph_redaction_preserves_foreign_text_in_gap(self):
        for foreign in (False, True):
            with self.subTest(foreign_text=foreign):
                original = fitz.open()
                original.new_page(width=300, height=200)
                original[0].insert_text((40, 45), 'alpha')
                if foreign:
                    original[0].insert_text((40, 70), 'KEEP')
                original[0].insert_text((40, 95), 'omega')
                source = t.page_source(original[0])
                spans = [s for b in source['blocks'] for s in b['spans']]
                selected = [s for s in spans if s['text'] in ('alpha', 'omega')]
                self.assertEqual(len(selected), 2)
                item = {'source_spans': selected, 'source_ids': [s['id'] for s in selected]}
                boxes = list(t.redaction_regions([item], source))
                self.assertEqual(len(boxes), 2 if foreign else 1)
                with fitz.open(stream=original.tobytes(), filetype='pdf') as legacy, fitz.open(stream=original.tobytes(), filetype='pdf') as batched:
                    for s in selected:
                        legacy[0].add_redact_annot(s['bbox'], fill=False, cross_out=False)
                    for box in boxes:
                        batched[0].add_redact_annot(box, fill=False, cross_out=False)
                    for doc in (legacy, batched):
                        doc[0].apply_redactions(images=0, graphics=0, text=0)
                    self.assertEqual(legacy[0].get_pixmap().samples, batched[0].get_pixmap().samples)
                    self.assertEqual(legacy[0].get_text(), batched[0].get_text())
                    self.assertEqual('KEEP' in batched[0].get_text(), foreign)
                    self.assertNotIn('alpha', batched[0].get_text())
                    self.assertNotIn('omega', batched[0].get_text())
                original.close()

    def test_selected_math_glyphs_exclude_neighbour_ink_and_cache(self):
        original=fitz.open();original.new_page(width=200,height=150)
        original[0].insert_text((50,80),'x',fontsize=18)
        original[0].insert_text((50,63),'g',fontsize=18)
        ref=fitz.open();ref.new_page(width=200,height=150);ref[0].insert_text((50,80),'x',fontsize=18)
        chars=[c for b in original[0].get_text('rawdict')['blocks'] if b['type']==0 for l in b['lines'] for sp in l['spans'] for c in sp['chars'] if c['c']=='x']
        glyph=chars[0];crop={'id':'variable','bbox':list(glyph['bbox']),'glyphs':[{'c':'x','origin':list(glyph['origin'])}]}
        raw=original[0].get_pixmap(matrix=fitz.Matrix(4,4),clip=fitz.Rect(crop['bbox']),alpha=False)
        expected=ref[0].get_pixmap(matrix=fitz.Matrix(4,4),clip=fitz.Rect(crop['bbox']),alpha=False)
        self.assertNotEqual(raw.samples,expected.samples)
        assets=self.directory/'isolated-math';assets.mkdir()
        name=t.math_asset(original,0,crop,assets,'synthetic-source')
        proof=t.load((assets/name).with_suffix('.verified.json'))
        expected=ref[0].get_pixmap(matrix=fitz.Matrix(4,4),clip=fitz.Rect(proof['bbox']),alpha=True)
        self.assertEqual(fitz.Pixmap(assets/name).samples,expected.samples)
        self.assertTrue(proof['reference_pixel_check'])
        self.assertTrue(proof['expanded_canvas_check'])
        with patch.object(fitz.Page,'get_pixmap',side_effect=AssertionError('Intact math cache rerendered')):
            self.assertEqual(t.math_asset(original,0,crop,assets,'synthetic-source'),name)
        bad=copy.deepcopy(crop);bad['glyphs'][0]['c']='z'
        with self.assertRaisesRegex(ValueError,'字形'):t.math_asset(original,0,bad,assets,'synthetic-source')

    def test_index_matches_native_intersection_at_boundaries(self):
        import random
        rng=random.Random(31)
        boxes=[]
        for n in range(250):
            x,y=rng.uniform(-50,600),rng.uniform(-50,800)
            boxes.append((n,[x,y,x+rng.uniform(.01,180),y+rng.uniform(.01,80)]))
        boxes.extend([(250,[32,32,64,64]),(251,[64,32,96,64])])
        index=t.RectIndex(boxes)
        for _,q in boxes:
            expected={n for n,b in boxes if (fitz.Rect(q)&fitz.Rect(b)).get_area()>.2}
            actual={n for n,b in index.candidates(q) if t.intersection(q,b)>.2}
            self.assertEqual(actual,expected)

    def test_identical_build_is_reused_but_changed_plan_is_checked(self):
        quiet(t.build,args(work=str(self.work)))
        original=(self.work/'中文全文译文.pdf').read_bytes()
        with patch.object(fitz.Page,'insert_htmlbox',side_effect=AssertionError('Unchanged PDF was rebuilt')):
            quiet(t.build,args(work=str(self.work),output='reused.pdf'))
        self.assertEqual((self.work/'reused.pdf').read_bytes(),original)
        self.assertTrue(t.load(self.work/'quality.json')['reused_complete_pdf'])
        path=self.work/'pages/0001.translation.json';plan=t.load(path);plan['items'][0]['text']='文字超出原位置。'*500;t.save(path,plan)
        with self.assertRaisesRegex(ValueError,'放不下'):
            quiet(t.build,args(work=str(self.work),output='changed.pdf'))
        self.assertFalse((self.work/'changed.pdf').exists())

    def test_modified_cached_pdf_is_not_reused(self):
        quiet(t.build,args(work=str(self.work)))
        (self.work/'中文全文译文.pdf').write_bytes(b'corrupt cache')
        quiet(t.build,args(work=str(self.work),output='repaired.pdf'))
        self.assertFalse(t.load(self.work/'quality.json')['reused_complete_pdf'])
        with fitz.open(self.work/'repaired.pdf') as doc:self.assertEqual(len(doc),2)

    def test_previews_are_reused_only_when_intact(self):
        quiet(t.build,args(work=str(self.work)));quiet(t.render,args(work=str(self.work)))
        with patch.object(fitz.Page,'get_pixmap',side_effect=AssertionError('Intact preview rerendered')):
            quiet(t.render,args(work=str(self.work)))
        (self.work/'translated-previews/0001.png').write_bytes(b'corrupt preview')
        quiet(t.render,args(work=str(self.work)))
        self.assertTrue((self.work/'translated-previews/0001.png').read_bytes().startswith(b'\x89PNG'))

    def test_compact_plan_roundtrip_keeps_real_destinations_and_fonts(self):
        compact=t.load(self.work/'segments.translation.json');catalog=t.load(self.work/'segments.source.json')
        for target,source in zip(compact['pages'],catalog['pages']):
            for edit,b in zip(target['blocks'],source['blocks']):
                words=b['text']
                if b['bbox'][1]>740 or words.strip().isdigit() or 'E =' in words or 'Method' in words or 'Baseline' in words:
                    edit.update(mode='keep',kind='page-number' if b['bbox'][1]>740 or words.strip().isdigit() else 'formula' if 'E =' in words else 'table',reason='保留原页码、公式或表格')
                else:
                    label='保持原页排版' if 'Layout' in words else '项目说明与相关链接：' if b['tokens'] else '模型根据文本预测动作序列，保留全部页面。'
                    edit.update(mode='translate',role='heading' if 'Layout' in words else 'body',text=label+' '.join('{{'+name+'}}' for name in b['tokens']))
                    if b['tokens']:
                        edit['bbox']=list(b['bbox']);edit['bbox'][3]+=20
        t.save(self.work/'segments.translation.json',compact)
        quiet(t.planning,self.work,'compile');t.prepare_fonts(self.work)
        quiet(t.build,args(work=str(self.work)))
        with fitz.open(self.source) as source,fitz.open(self.work/'中文全文译文.pdf') as output:
            for a,b in zip(source,output):
                self.assertEqual({t.target_key(l) for l in a.get_links()},{t.target_key(l) for l in b.get_links()})
                self.assertIn('模型',b.get_text())
        with self.assertRaisesRegex(ValueError,'已经存在'):t.planning(self.work,'draft')

    def test_relative_runtime_survives_relocation_and_other_cwd(self):
        moved=self.directory/'moved'/'scripts';moved.mkdir(parents=True)
        import shutil
        shutil.copyfile(SCRIPT,moved/'translate.py')
        t.save(moved/'runtime.local.json',{'deps':os.path.relpath(os.environ['ZGT_DEPS'],moved)})
        env={k:v for k,v in os.environ.items() if k!='ZGT_DEPS'}
        result=subprocess.run([sys.executable,'-I','-X','utf8',str(moved/'translate.py'),'doctor'],cwd=self.directory,env=env,capture_output=True,text=True,encoding='utf8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(json.loads(result.stdout)['ready'])

    def test_new_machine_connection_is_readonly_and_instance_bound(self):
        class Response(io.BytesIO):
            def __init__(self,identity):
                super().__init__(b'[]');self.headers={'Zotero-Server-ID':identity,'Content-Type':'application/json'};self.status=200
        class Local:
            identity='LABINSTANCE1'
            def open(self,request,**kw):
                self_test.assertEqual(request.get_method(),'GET')
                self_test.assertTrue(request.full_url.startswith('http://127.0.0.1:23119/api/users/0/items?'))
                return Response(self.identity)
        self_test=self;local=Local();workspace=self.directory/'lab-workspace'
        with patch.object(t.urllib.request,'build_opener',return_value=local):
            quiet(t.connect,args(workspace=str(workspace)))
            path=workspace/'.zotero/connection.json';data=t.load(path);before=path.read_bytes()
            self.assertEqual(data['server_id'],'LABINSTANCE1')
            self.assertEqual(data['access'],'read-only')
            quiet(t.connect,args(workspace=str(workspace)))
            self.assertEqual(path.read_bytes(),before)
            local.identity='OTHERINSTANCE'
            with self.assertRaises(ValueError):quiet(t.connect,args(workspace=str(workspace)))
            self.assertEqual(path.read_bytes(),before)

    def test_dense_citations_stay_blue_and_clickable(self):
        directory = ROOT / 'test-output' / uuid_name()
        directory.mkdir(parents=True)
        source = directory / 'dense.pdf'
        labels = [f'Ref{i:03}' for i in range(110)]
        text = ' '.join(labels)
        doc = fitz.open();page = doc.new_page(width=600,height=800)
        page.insert_textbox((40,50,560,400),text,fontsize=10)
        doc.save(source);doc.close()
        work = directory / 'job'
        quiet(t.prepare,args(pdf=str(source),key=None,work=str(work)))
        sp = t.load(work/'pages/0001.source.json')
        translated = '引用：'+text
        runs = [{'start':translated.index(label),'end':translated.index(label)+len(label),'text':label,'kind':'citation','link':{'kind':fitz.LINK_URI,'uri':'https://example.org/reference/'+label}} for label in labels]
        t.save(work/'pages/0001.translation.json',{'page':1,'items':[{'source_ids':[s['id'] for b in sp['blocks'] for s in b['spans']],'text':translated,'bbox':[40,50,560,450],'runs':runs}],'keep':[]})
        quiet(t.build,args(work=str(work)))
        result=fitz.open(work/'中文全文译文.pdf')
        references=[s for b in result[0].get_text('dict')['blocks'] if b['type']==0 for line in b['lines'] for s in line['spans'] if s['text'].startswith('Ref')]
        self.assertEqual(len(references),110)
        self.assertEqual(len(result[0].get_links()),110)
        for span in references:
            color=span['color'];red=(color>>16)&255;green=(color>>8)&255;blue=color&255
            self.assertGreater(blue,green)
            self.assertGreater(green,red)

    def setUp(self):
        # Keep test files in an isolated directory; no cleanup or deletion.
        directory = ROOT / "test-output" / uuid_name()
        directory.mkdir(parents=True)
        self.directory = directory
        self.source = make_fixture(directory)
        self.work = directory / "job"
        quiet(t.prepare, args(pdf=str(self.source), key=None, work=str(self.work)))
        write_plan(self.work)

    def test_geometry_links_graphics_and_coverage(self):
        quiet(t.build, args(work=str(self.work)))
        quiet(t.render, args(work=str(self.work)))
        with fitz.open(self.source) as original, fitz.open(self.work / "中文全文译文.pdf") as result:
            self.assertEqual(len(original), len(result))
            self.assertEqual(original.get_toc(), result.get_toc())
            for a, b in zip(original, result):
                self.assertEqual(sorted(t.target_key(l) for l in a.get_links()), sorted(t.target_key(l) for l in b.get_links()))
                old_moved = {l['xref'] for l in a.get_links() if l.get('uri')!='https://example.org/kept'}
                self.assertFalse(old_moved & {l['xref'] for l in b.get_links()})
                kept_before = next(l for l in a.get_links() if l.get('uri')=='https://example.org/kept')
                kept_after = next(l for l in b.get_links() if l.get('uri')=='https://example.org/kept')
                self.assertEqual(kept_before['from'],kept_after['from'])
                self.assertEqual(a.rect, b.rect)
                self.assertIn("E = m c^2", b.get_text())
                self.assertIn("Baseline", b.get_text())
                self.assertNotIn("A model predicts", b.get_text())
                self.assertIn("模型", b.get_text())
                # Original table pixels are exactly identical.
                clip = fitz.Rect(318, 174, 552, 272) * a.rotation_matrix
                self.assertEqual(a.get_pixmap(clip=clip).samples, b.get_pixmap(clip=clip).samples)
                clip = fitz.Rect(40, 250, 100, 290) * a.rotation_matrix
                self.assertEqual(a.get_pixmap(clip=clip).samples, b.get_pixmap(clip=clip).samples)
        q = t.load(self.work / "quality.json")
        self.assertTrue(q["coverage_ok"])
        self.assertGreaterEqual(q["min_scale"], 0.8)
        print("VISUAL_SAMPLE=" + str(self.work / "translated-previews/0001.png"))

    def test_missing_duplicate_overflow_and_protected(self):
        path = self.work / "pages/0001.translation.json"
        plan = t.load(path)
        broken = copy.deepcopy(plan); broken["items"].pop()
        t.save(path, broken)
        with self.assertRaisesRegex(ValueError, "尚未处理"):
            t.validate(self.work)
        broken = copy.deepcopy(plan); broken["items"].append(broken["items"][0])
        t.save(path, broken)
        with self.assertRaisesRegex(ValueError, "重复"):
            t.validate(self.work)
        broken = copy.deepcopy(plan); broken["protected_regions"].append({"bbox": plan["items"][0]["bbox"], "kind": "figure", "reason": "测试碰撞"})
        t.save(path, broken)
        with self.assertRaisesRegex(ValueError, "保护区"):
            t.validate(self.work)
        broken = copy.deepcopy(plan); broken["items"][0]["text"] = "此处故意超出允许空间，测试程序必须停止。" * 500
        t.save(path, broken)
        with self.assertRaisesRegex(ValueError, "放不下"):
            quiet(t.build, args(work=str(self.work)))
        self.assertFalse((self.work / "中文全文译文.pdf").exists())

    def test_review_invalidation(self):
        quiet(t.build, args(work=str(self.work)))
        quiet(t.review, args(work=str(self.work), note="已逐页核对正文、公式、表格、页码与链接热区。"))
        path = self.work / "pages/0001.translation.json"
        plan = t.load(path); plan["items"][0]["text"] += "修订"
        t.save(path, plan)
        with self.assertRaisesRegex(ValueError, "变化"):
            t.current_quality(self.work)

    def test_missing_link_mapping_stops_instead_of_restoring_ghost(self):
        path=self.work/'pages/0001.translation.json'
        plan=t.load(path)
        for item in plan['items']:
            item.pop('runs',None)
        t.save(path,plan)
        with self.assertRaisesRegex(ValueError,'原链接需要迁移但缺少译文映射'):
            quiet(t.build,args(work=str(self.work)))
        self.assertFalse((self.work/'中文全文译文.pdf').exists())

    def test_colored_citation_and_aligned_destination(self):
        path = self.work / 'pages/0001.translation.json'
        plan = t.load(path)
        item = plan['items'][1]
        item['text'] = '模型参见 Example et al., 2022，并保留原文引用。'
        label = 'Example et al., 2022'
        start = item['text'].index(label)
        item['runs'] = [{'start':start,'end':start+len(label),'text':label,'kind':'citation','link':{'page':1,'to':[40,70]}}]
        t.save(path, plan)
        quiet(t.build, args(work=str(self.work)))
        with fitz.open(self.work / '中文全文译文.pdf') as doc:
            colored = [s for b in doc[0].get_text('dict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans'] if s['color']==0x0060A0]
            self.assertTrue(colored)
            for span in colored:
                self.assertTrue(any(link.get('page')==1 and (fitz.Rect(link['from'])&fitz.Rect(span['bbox'])).get_area()>0.1 for link in doc[0].get_links()))
        self.assertGreater(t.load(self.work / 'quality.json')['added_reference_link_regions'], 0)

    @unittest.skipUnless(Path('C:/Windows/Fonts/simsun.ttc').is_file(), 'Requires local SimSun')
    def test_song_font_is_really_embedded(self):
        assets = self.work / 'math-assets'
        t.prepare_fonts(self.work)
        before=t.digest(assets/'font-simsun.ttf')
        t.prepare_fonts(self.work)
        self.assertEqual(t.digest(assets/'font-simsun.ttf'),before)
        path = self.work / 'pages/0001.translation.json'
        plan = t.load(path)
        item = plan['items'][1]
        item['typography'] = {'family':'song', 'line_height':1.45, 'indent_em':2, 'weight':'normal', 'align':'left'}
        t.save(path, plan)
        quiet(t.build, args(work=str(self.work)))
        with fitz.open(self.work / '中文全文译文.pdf') as doc:
            fonts = [s['font'] for b in doc[0].get_text('dict', clip=fitz.Rect(item['bbox']))['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans'] if '模型' in s['text']]
            self.assertTrue(fonts)
            self.assertTrue(all('SimSun' in name for name in fonts))
        self.assertLess((self.work / '中文全文译文.pdf').stat().st_size, 5_000_000)

    def test_inline_formula_crop(self):
        for number in [1, 2]:
            path = self.work / f"pages/{number:04}.translation.json"
            plan = t.load(path)
            item = plan["items"][1]
            source = t.load(self.work / f"pages/{number:04}.source.json")
            spans = {s["id"]: s for b in source["blocks"] for s in b["spans"]}
            box = spans[item["source_ids"][0]]["bbox"]
            item["math"] = [{"id": "m1", "bbox": [box[0], box[1], box[0]+12, box[3]]}]
            item["text"] = "模型根据 {{math:m1}} 预测动作序列，保留原始内嵌区域。"
            t.save(path, plan)
        quiet(t.build, args(work=str(self.work)))
        self.assertTrue(list((self.work / "math-assets").glob("*.png")))
        # Rotated page crops must have the same upright pixels as page 1.
        crops = sorted((self.work / "math-assets").glob("*.png"))
        self.assertEqual(crops[0].read_bytes(), crops[1].read_bytes())

    def test_attachment_retry_is_idempotent_and_parent_bound(self):
        quiet(t.build, args(work=str(self.work)))
        quiet(t.review, args(work=str(self.work), note="测试件已逐页核对正文、公式、表格与全部链接。"))
        path = self.work / "manifest.json"
        m = t.load(path)
        m["zotero"] = {"config_path": str(self.directory / "mock-config.json"), "server_id": "fixture", "parent_key": "PARENT01", "source_key": "SOURCE01", "library_id": 0}
        t.save(path, m)
        api = FakeAPI(self.work)
        quiet(t.attach, args(work=str(self.work), yes=True), lambda _: api)
        quiet(t.attach, args(work=str(self.work), yes=True), lambda _: api)
        self.assertEqual(api.creates, 1)
        self.assertEqual(api.uploads, 1)
        api.parent = "OTHER001"
        with self.assertRaisesRegex(ValueError, "移出"):
            quiet(t.attach, args(work=str(self.work), yes=True), lambda _: api)


class FakeAPI:
    def __init__(self, work):
        self.work = work; self.server_id = "fixture"; self.root = "/users/0"; self.base = "http://127.0.0.1:23119/api"
        self.creates = 0; self.uploads = 0; self.parent = "PARENT01"; self.child = None

    def item(self, key):
        if key == "PARENT01": return {"library": {"id": 0}, "data": {"itemType": "journalArticle"}}
        if key == "SOURCE01": return {"data": {"parentItem": self.parent}}
        return {"key": "TRANS001", "data": self.child}

    def children(self, key):
        return [{"key": "TRANS001", "data": self.child}] if self.child else []

    def file_path(self, key):
        return self.work / ("source.pdf" if key == "SOURCE01" else "中文全文译文.pdf")

    def local_url(self, url): t.API.local_url(url)

    def write(self, path, body, headers):
        if path == "/users/0/items":
            self.creates += 1; self.child = json.loads(body)[0]
            return 200, {"success": {"0": "TRANS001"}}
        data = dict(__import__('urllib.parse', fromlist=['parse_qsl']).parse_qsl(body.decode()))
        if "upload" in data:
            self.child["md5"] = hashlib.md5(self.file_path("TRANS001").read_bytes()).hexdigest()
            return 204, b""
        return 200, {"url": "http://127.0.0.1:23119/upload", "contentType": "application/pdf", "uploadKey": "mock"}

    def request(self, *a, **kw):
        self.uploads += 1
        return 201, b""


def uuid_name():
    import uuid
    return uuid.uuid4().hex[:10]


if __name__ == "__main__":
    unittest.main(verbosity=2)
