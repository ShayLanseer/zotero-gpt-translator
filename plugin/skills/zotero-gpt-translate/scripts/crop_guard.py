"""Verified native-glyph crops. Never treat layout boxes as visible ink bounds."""
import hashlib,json
from pathlib import Path

ALGORITHM='native-ink-v2'
IMPLEMENTATION_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

def signature(c):
    return c['c'],tuple(round(v,3) for v in c['origin'])

def ink_box(pix):
    if not pix.alpha:raise ValueError('字形完整性检查需要透明参考图。')
    alpha=bytes(pix.samples_mv[pix.n-1::pix.n])
    xmin,ymin,xmax,ymax=pix.width,pix.height,0,0
    for y in range(pix.height):
        row=alpha[y*pix.width:(y+1)*pix.width]
        if row.strip(b'\0'):
            xmin=min(xmin,len(row)-len(row.lstrip(b'\0')))
            xmax=max(xmax,len(row.rstrip(b'\0')))
            ymin=min(ymin,y);ymax=y+1
    if not xmax:raise ValueError('裁图没有可见笔画，停止。')
    return xmin,ymin,xmax,ymax


def guarded_asset(original,pno,crop,assets,source_hash,api):
    f=api.fitz;page=original[pno];rotation=page.rotation
    inputs=[ALGORITHM,IMPLEMENTATION_SHA,source_hash,pno,crop,f.VersionBind]
    key=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()
    path=assets/('math-'+key+'.png');record=path.with_suffix('.verified.json')
    if path.is_file() and record.is_file():
        try:
            proof=api.load(record)
            if proof['algorithm']==ALGORITHM and proof['input_key']==key and proof['verified'] is True and proof['png_sha256']==api.digest(path):
                pix=f.Pixmap(path);bounds=ink_box(pix)
                if list(bounds)==proof['ink_pixels'] and min(bounds[:2])>=1 and bounds[2]<pix.width and bounds[3]<pix.height:
                    return path.name
        except (ValueError,KeyError,TypeError,RuntimeError):pass
    small=f.TOOLS.set_small_glyph_heights();isolated=None
    try:
        page.set_rotation(0)
        f.TOOLS.set_small_glyph_heights(False)
        chars=[c for b in page.get_text('rawdict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans'] for c in s['chars'] if c['c'].strip()]
        requested={signature(c) for c in crop.get('glyphs',[])}
        if not requested:
            # Legacy crops: only complete characters with origins inside the
            # declared region are selected; actual ink is still checked below.
            requested={signature(c) for c in chars if f.Rect(crop['bbox']).contains(f.Point(c['origin']))}
        selected=[c for c in chars if signature(c) in requested]
        if not requested or {signature(c) for c in selected}!=requested:
            raise ValueError('公式原文字形选择缺失或与源 PDF 不符。')
        full=api.union(selected)
        # Keep the original region too, e.g. fraction bars and formula rules.
        canvas=(full|f.Rect(crop['bbox']))+(-2,-2,2,2)
        if not page.rect.contains(canvas):raise ValueError('公式参考画布触及页面边界，不能证明完整。')
        unwanted=[c for c in chars if signature(c) not in requested and api.intersection(c['bbox'],canvas+(-2,-2,2,2))>.001]
        isolated=f.open();isolated.insert_pdf(original,from_page=pno,to_page=pno,links=False,annots=False)
        target=isolated[0]
        for c in unwanted:
            box=f.Rect(c['bbox']);probes=[]
            for fy in [.5,.2,.8,.05,.95]:
                for fx in [.5,.2,.8,.05,.95]:
                    x,y=box.x0+box.width*fx,box.y0+box.height*fy;r=f.Rect(x-.05,y-.05,x+.05,y+.05)
                    if not any(api.intersection(r,s['bbox'])>0 for s in selected):probes.append(r)
            if not probes:raise ValueError('公式与邻字无法安全分离，停止缓存。')
            for r in probes:target.add_redact_annot(r,fill=False,cross_out=False)
        if unwanted:target.apply_redactions(images=0,graphics=0,text=0)
        survived={signature(c) for b in target.get_text('rawdict')['blocks'] if b['type']==0 for l in b['lines'] for s in l['spans'] for c in s['chars'] if c['c'].strip()}
        if not requested<=survived or any(signature(c) in survived for c in unwanted):raise ValueError('公式隔离改变字形或残留邻字，停止缓存。')
        matrix=f.Matrix(4,4)
        reference=target.get_pixmap(matrix=matrix,clip=canvas,alpha=True,annots=False)
        larger=target.get_pixmap(matrix=matrix,clip=canvas+(-2,-2,2,2),alpha=True,annots=False)
        bounds=ink_box(reference);wide=ink_box(larger)
        world=lambda p,b:(p.x+b[0],p.y+b[1],p.x+b[2],p.y+b[3])
        if world(reference,bounds)!=world(larger,wide):raise ValueError('扩大参考画布后出现新笔画，裁取范围不完整，停止缓存。')
        if min(bounds[:2])<1 or bounds[2]>=reference.width or bounds[3]>=reference.height:raise ValueError('参考笔画贴边，停止缓存。')
        gx0,gy0,gx1,gy1=world(reference,bounds)
        actual=f.Rect((gx0-2)/4,(gy0-2)/4,(gx1+2)/4,(gy1+2)/4)
        final=target.get_pixmap(matrix=matrix,clip=actual,alpha=True,annots=False)
        # Independent final render must reproduce every reference pixel, not
        # merely retain character records in the PDF text layer.
        final_data=final.samples_mv
        reference_data=reference.samples_mv
        for y in range(final.height):
            ry=final.y+y-reference.y
            x0=final.x-reference.x
            expected=reference_data[(ry*reference.width+x0)*4:(ry*reference.width+x0+final.width)*4]
            if bytes(expected)!=bytes(final_data[y*final.stride:y*final.stride+final.width*4]):raise ValueError('最终裁图像素与原字形参考不一致，停止缓存。')
        final_bounds=ink_box(final)
        if min(final_bounds[:2])<1 or final_bounds[2]>=final.width or final_bounds[3]>=final.height:raise ValueError('最终裁图笔画贴边，停止缓存。')
        # Publish image first and its proof last. Without a matching proof the
        # image is never reusable, including after an interrupted operation.
        final.save(path)
        api.save(record,{'algorithm':ALGORITHM,'input_key':key,'verified':True,'source_sha256':source_hash,'png_sha256':api.digest(path),'bbox':list(actual),'ink_pixels':list(final_bounds),'native_glyphs':len(requested),'reference_pixel_check':True,'expanded_canvas_check':True})
        return path.name
    finally:
        page.set_rotation(rotation);f.TOOLS.set_small_glyph_heights(small)
        if isolated is not None:isolated.close()
