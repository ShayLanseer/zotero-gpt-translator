"""Move text underlines with their translation; never keep orphan source rules."""
from collections import Counter
import html
import re


def signature(d):
    return repr((d['items'], d.get('color'), d.get('fill'), d.get('width')))


def detect(page, api):
    f = api.fitz
    state = f.TOOLS.set_small_glyph_heights()
    try:
        f.TOOLS.set_small_glyph_heights(True)
        lines = []
        for bi, b in enumerate(page.get_text('rawdict')['blocks']):
            if b['type'] != 0: continue
            for li, line in enumerate(b['lines']):
                chars = [(f'p{page.number+1}-b{bi}-l{li}-s{si}', sp['size'], c)
                         for si, sp in enumerate(line['spans']) for c in sp['chars']]
                lines.append(chars)
        result = []
        for drawing in page.get_drawings():
            for op in drawing['items']:
                if op[0] == 'l':
                    a, b = op[1:3]
                    if abs(a.y-b.y) > .1: continue
                    x0, x1, y = min(a.x,b.x), max(a.x,b.x), (a.y+b.y)/2
                elif op[0] == 're' and drawing.get('fill') is not None and op[1].height <= 1.5:
                    x0, x1, y = op[1].x0, op[1].x1, (op[1].y0+op[1].y1)/2
                else: continue
                if x1-x0 < 2: continue
                matches = []
                for line in lines:
                    selected = [(key,size,c) for key,size,c in line
                                if x0-.5 <= (c['bbox'][0]+c['bbox'][2])/2 <= x1+.5
                                and -.1 <= y-c['origin'][1] <= size*.45]
                    while selected and not selected[0][2]['c'].strip(): selected.pop(0)
                    while selected and not selected[-1][2]['c'].strip(): selected.pop()
                    if selected and abs(selected[0][2]['bbox'][0]-x0)<1.5 and abs(selected[-1][2]['bbox'][2]-x1)<1.5:
                        matches.append(selected)
                if not matches: continue  # e.g. a table rule extending beyond its text
                if len(matches)!=1 or len(drawing['items'])!=1:
                    raise ValueError('下划线与复合图形无法安全分离，需人工核实。')
                selected=matches[0]
                result.append({'id':f'u{len(result)+1}', 'text':''.join(c['c'] for _,_,c in selected),
                               'source_ids':list(dict.fromkeys(key for key,_,c in selected if c['c'].strip())),
                               'bbox':[x0,y-.02,x1,y+.02], 'color':list(drawing.get('color') or drawing.get('fill') or (0,0,0)),
                               'width':max(.2,drawing.get('width') or .4), 'signature':signature(drawing)})
        # Underline annotations also carry old coordinates. Fail closed until mapped.
        if any(a.type[0] == f.PDF_ANNOT_UNDERLINE for a in (page.annots() or [])):
            raise ValueError('原页含下划线批注，需先核实并迁移批注文字范围，不能保留旧坐标。')
        return result
    finally:
        f.TOOLS.set_small_glyph_heights(state)


def checked(page, items, api):
    found=detect(page,api); by_id={u['id']:u for u in found}; used={}; affected=[]
    for u in found:
        touched=[i for i in items if set(i['source_ids']) & set(u['source_ids'])]
        if touched:
            if len(touched)!=1 or not set(u['source_ids'])<=set(touched[0]['source_ids']):
                raise ValueError('一条下划线跨越多个译文段落，需合并或显式拆分映射。')
            affected.append(u)
    for item in items:
        ranges=[]
        for mark in item.get('underlines',[]):
            uid=mark.get('source'); start,end=mark.get('start'),mark.get('end')
            if uid not in by_id or uid in used or by_id[uid] not in affected:
                raise ValueError('下划线来源未知、重复或不属于翻译区域。')
            if not set(by_id[uid]['source_ids'])<=set(item['source_ids']):
                raise ValueError('下划线映射到了错误的译文段落。')
            if not isinstance(start,int) or not isinstance(end,int) or not 0<=start<end<=len(item['text']) or item['text'][start:end]!=mark.get('text'):
                raise ValueError('下划线译文区间无效或文字不匹配。')
            if any(start<b and a<end for a,b in ranges) or any(start<m.end() and m.start()<end for m in re.finditer(r'\{\{math:[^}]+\}\}',item['text'])):
                raise ValueError('下划线区间重叠或涉及公式图片，需核实拆分。')
            ranges.append((start,end)); used[uid]=mark
    if set(used)!={u['id'] for u in affected}:
        raise ValueError('正文下划线缺少对应译文映射；请补齐 underlines 后生成，禁止沿用原坐标。')
    return affected


def remove(page, affected, api):
    if not affected: return
    f=api.fitz
    before=Counter(signature(d) for d in page.get_drawings())
    text=page.get_text(); images=page.get_images(full=True)
    for u in affected:
        r=f.Rect(u['bbox']); x=(r.x0+r.x1)/2; y=(r.y0+r.y1)/2
        page.add_redact_annot((x-.01,y-.01,x+.01,y+.01),fill=False,cross_out=False)
    page.apply_redactions(images=0,graphics=2,text=1)
    expected=before-Counter(u['signature'] for u in affected)
    if Counter(signature(d) for d in page.get_drawings())!=expected or page.get_text()!=text or page.get_images(full=True)!=images:
        raise ValueError('下划线移除影响了其他图形或文字，停止生成。')


def markup(item, run_links, api):
    """Segment ranges so underlines and linked references can overlap safely."""
    text=item['text']; runs=sorted(item.get('runs',[]),key=lambda r:r['start']); marks=item.get('underlines',[])
    cursor=0
    for r in runs:
        if r['start']<cursor or not 0<=r['start']<r['end']<=len(text) or text[r['start']:r['end']]!=r['text'] or r.get('kind') not in {'citation','crossref','hyperlink'}:
            raise ValueError('引用区间重叠或与原文不符。')
        cursor=r['end']
    bounds=sorted({0,len(text)}|{v for r in runs+marks for v in (r['start'],r['end'])})
    parts=[]
    for a,b in zip(bounds,bounds[1:]):
        r=next((r for r in runs if r['start']<=a and b<=r['end']),None)
        u=next((u for u in marks if u['start']<=a and b<=u['end']),None)
        value=html.escape(text[a:b])
        if r or u:
            n=len(run_links)
            if n>=150: raise ValueError('单页引用和下划线标注数量过多。')
            color=((0xB02020 if r['kind']=='crossref' else 0x0060A0)+((n%40)<<8)+n//40) if r else 0x010101+((n%5)<<16)+(((n//5)%5)<<8)+n//25
            run_links[color]={'__underline':True,'target':r.get('link') if r else None} if u else r.get('link')
            value=f'<span style="color:#{color:06x}">{value}</span>'
        parts.append(value)
    return ''.join(parts).replace('\n','<br>')


def paint(page, run_links, api):
    seen=set(); count=0
    for b in page.get_text('dict')['blocks']:
        if b['type']!=0: continue
        for line in b['lines']:
            for s in line['spans']:
                entry=run_links.get(s['color'])
                if isinstance(entry,dict) and entry.get('__underline'):
                    # Use actual Story output baseline and width, including line wraps.
                    y=s['origin'][1]+max(.6,s['size']*.10)
                    rgb=tuple(((s['color']>>shift)&255)/255 for shift in (16,8,0))
                    page.draw_line((s['bbox'][0],y),(s['bbox'][2],y),color=rgb,width=max(.3,s['size']*.045))
                    seen.add(s['color']);count+=1
    return seen,count
