#!/usr/bin/env python3
"""
TL UGC Editor v0.1.2 - tiny Tomodachi Life: Living the Dream UGC viewer/replacer.

Dependencies:
    python -m pip install pillow zstandard
    python -m pip install tkinterdnd2

Keep backups. This is a fan tool / save-editor helper.
"""
from __future__ import annotations

import math, re, shutil, tempfile, zipfile, traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, font as tkfont

# Optional drag/drop support. Install with: python -m pip install tkinterdnd2
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except Exception:
    TkinterDnD = None
    DND_FILES = None
    DND_AVAILABLE = False

try:
    import zstandard as zstd
    from PIL import Image, ImageTk
except Exception as e:
    raise SystemExit("Install dependencies first:\npython -m pip install pillow zstandard\n\n" + str(e))

# ---------------- Nintendo Switch block-linear swizzle ----------------

def div_round_up(n:int,d:int)->int: return (n+d-1)//d

def gob_address(x:int, y:int, width_in_gobs:int, bpe:int, block_height:int)->int:
    x_bytes = x * bpe
    gob_addr = ((y // (8 * block_height)) * 512 * block_height * width_in_gobs
                + (x_bytes // 64) * 512 * block_height
                + ((y % (8 * block_height)) // 8) * 512)
    xg = x_bytes % 64
    yg = y % 8
    return (gob_addr
            + ((xg % 64) // 32) * 256
            + ((yg % 8) // 2) * 64
            + ((xg % 32) // 16) * 32
            + (yg % 2) * 16
            + (xg % 16))

def deswizzle_block_linear(data:bytes, width:int, height:int, bpe:int, block_height:int)->bytes:
    width_in_gobs = div_round_up(width*bpe, 64)
    padded_h = div_round_up(height, 8*block_height) * (8*block_height)
    padded_size = width_in_gobs * padded_h * 64
    src = data + b"\x00" * max(0, padded_size-len(data))
    out = bytearray(width*height*bpe)
    for y in range(height):
        for x in range(width):
            s = gob_address(x,y,width_in_gobs,bpe,block_height)
            d = (y*width+x)*bpe
            out[d:d+bpe] = src[s:s+bpe]
    return bytes(out)

def swizzle_block_linear(data:bytes, width:int, height:int, bpe:int, block_height:int)->bytes:
    width_in_gobs = div_round_up(width*bpe,64)
    padded_h = div_round_up(height, 8*block_height)*(8*block_height)
    out = bytearray(width_in_gobs*padded_h*64)
    for y in range(height):
        for x in range(width):
            s=(y*width+x)*bpe
            d=gob_address(x,y,width_in_gobs,bpe,block_height)
            out[d:d+bpe]=data[s:s+bpe]
    return bytes(out)

# ---------------- color helpers ----------------

def rgb565_to_rgb(c:int):
    r=((c>>11)&31)*255//31; g=((c>>5)&63)*255//63; b=(c&31)*255//31
    return r,g,b

def rgb_to_565(r:int,g:int,b:int)->int:
    return ((r*31+127)//255)<<11 | ((g*63+127)//255)<<5 | ((b*31+127)//255)

def srgb_to_lin_u8(v:int)->int:
    f=v/255.0
    l=f/12.92 if f<=0.04045 else ((f+0.055)/1.055)**2.4
    return max(0,min(255,int(l*255+0.5)))

def lin_to_srgb_u8(v:int)->int:
    f=v/255.0
    s=f*12.92 if f<=0.0031308 else 1.055*(f**(1/2.4))-0.055
    return max(0,min(255,int(s*255+0.5)))

def prepare_image(img:Image.Image, w:int, h:int, mode:str='contain')->Image.Image:
    img = img.convert('RGBA')
    if mode in ('stretch', 'fit_stretch'):
        return img.resize((w,h), Image.Resampling.LANCZOS)

    out = Image.new('RGBA', (w,h), (0,0,0,0))
    if mode in ('cover', 'fit_cover'):
        # Fill target while keeping proportions, then center-crop. Good when you want no empty border.
        scale = max(w / img.width, h / img.height)
    else:
        # Default: fit inside target while keeping proportions, with transparent padding.
        scale = min(w / img.width, h / img.height)

    nw = max(1, int(round(img.width * scale)))
    nh = max(1, int(round(img.height * scale)))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    x = (w - nw) // 2
    y = (h - nh) // 2
    out.alpha_composite(resized, (x, y))
    return out

def to_game_linear_rgba(img:Image.Image, w:int, h:int, fit_mode:str='contain')->bytes:
    img = prepare_image(img, w, h, fit_mode)
    raw = bytearray(img.tobytes())
    for i in range(0,len(raw),4):
        raw[i]=srgb_to_lin_u8(raw[i]); raw[i+1]=srgb_to_lin_u8(raw[i+1]); raw[i+2]=srgb_to_lin_u8(raw[i+2])
    return bytes(raw)

def from_game_linear_rgba(raw:bytes)->bytes:
    out=bytearray(raw)
    for i in range(0,len(out),4):
        out[i]=lin_to_srgb_u8(out[i]); out[i+1]=lin_to_srgb_u8(out[i+1]); out[i+2]=lin_to_srgb_u8(out[i+2])
    return bytes(out)

# ---------------- BC1 / BC3 decode + simple encode ----------------

def bc1_decode(blocks:bytes, w:int, h:int)->bytes:
    out=bytearray(w*h*4); pos=0
    for by in range(0,h,4):
        for bx in range(0,w,4):
            c0=int.from_bytes(blocks[pos:pos+2],'little'); c1=int.from_bytes(blocks[pos+2:pos+4],'little')
            bits=int.from_bytes(blocks[pos+4:pos+8],'little'); pos+=8
            r0,g0,b0=rgb565_to_rgb(c0); r1,g1,b1=rgb565_to_rgb(c1)
            if c0>c1:
                pal=[(r0,g0,b0,255),(r1,g1,b1,255),((2*r0+r1)//3,(2*g0+g1)//3,(2*b0+b1)//3,255),((r0+2*r1)//3,(g0+2*g1)//3,(b0+2*b1)//3,255)]
            else:
                pal=[(r0,g0,b0,255),(r1,g1,b1,255),((r0+r1)//2,(g0+g1)//2,(b0+b1)//2,255),(0,0,0,0)]
            for py in range(4):
                for px in range(4):
                    if bx+px>=w or by+py>=h: continue
                    idx=(bits >> (2*(4*py+px))) & 3
                    d=((by+py)*w+(bx+px))*4
                    out[d:d+4]=bytes(pal[idx])
    return bytes(out)

def nearest_rgb(p, pal):
    pr,pg,pb,pa=p
    best=0; bestd=10**12
    for i,(r,g,b,a) in enumerate(pal):
        if pa<128 and a==0: return i
        if a==0: continue
        d=(pr-r)*(pr-r)+(pg-g)*(pg-g)+(pb-b)*(pb-b)
        if d<bestd: bestd=d; best=i
    return best

def block_minmax_rgb(pixels):
    opaque=[p for p in pixels if p[3]>=128]
    if not opaque: return (0,0,0),(0,0,0)
    lo=min(opaque, key=lambda p:p[0]*0.299+p[1]*0.587+p[2]*0.114)
    hi=max(opaque, key=lambda p:p[0]*0.299+p[1]*0.587+p[2]*0.114)
    return hi[:3], lo[:3]

def bc1_encode(rgba:bytes, w:int, h:int)->bytes:
    out=bytearray()
    for by in range(0,h,4):
        for bx in range(0,w,4):
            pix=[]
            for py in range(4):
                for px in range(4):
                    x=min(bx+px,w-1); y=min(by+py,h-1); i=(y*w+x)*4
                    pix.append(tuple(rgba[i:i+4]))
            transparent=any(p[3]<128 for p in pix)
            hi,lo=block_minmax_rgb(pix)
            c0=rgb_to_565(*hi); c1=rgb_to_565(*lo)
            if transparent:
                if c0>c1: c0,c1=c1,c0
            else:
                if c0<=c1: c0,c1=c1,c0
            r0,g0,b0=rgb565_to_rgb(c0); r1,g1,b1=rgb565_to_rgb(c1)
            if c0>c1:
                pal=[(r0,g0,b0,255),(r1,g1,b1,255),((2*r0+r1)//3,(2*g0+g1)//3,(2*b0+b1)//3,255),((r0+2*r1)//3,(g0+2*g1)//3,(b0+2*b1)//3,255)]
            else:
                pal=[(r0,g0,b0,255),(r1,g1,b1,255),((r0+r1)//2,(g0+g1)//2,(b0+b1)//2,255),(0,0,0,0)]
            bits=0
            for n,p in enumerate(pix): bits |= nearest_rgb(p,pal) << (2*n)
            out += c0.to_bytes(2,'little') + c1.to_bytes(2,'little') + bits.to_bytes(4,'little')
    return bytes(out)

def bc3_decode(blocks:bytes, w:int, h:int)->bytes:
    out=bytearray(w*h*4); pos=0
    for by in range(0,h,4):
        for bx in range(0,w,4):
            a0=blocks[pos]; a1=blocks[pos+1]; abits=int.from_bytes(blocks[pos+2:pos+8],'little')
            if a0>a1:
                ap=[a0,a1,(6*a0+1*a1)//7,(5*a0+2*a1)//7,(4*a0+3*a1)//7,(3*a0+4*a1)//7,(2*a0+5*a1)//7,(1*a0+6*a1)//7]
            else:
                ap=[a0,a1,(4*a0+1*a1)//5,(3*a0+2*a1)//5,(2*a0+3*a1)//5,(1*a0+4*a1)//5,0,255]
            colors=bc1_decode(blocks[pos+8:pos+16],4,4); pos+=16
            for py in range(4):
                for px in range(4):
                    if bx+px>=w or by+py>=h: continue
                    ci=(py*4+px)*4; d=((by+py)*w+(bx+px))*4
                    ai=(abits >> (3*(4*py+px))) & 7
                    out[d:d+4]=bytes([colors[ci],colors[ci+1],colors[ci+2],ap[ai]])
    return bytes(out)

def bc3_encode(rgba:bytes, w:int, h:int)->bytes:
    out=bytearray()
    for by in range(0,h,4):
        for bx in range(0,w,4):
            pix=[]
            for py in range(4):
                for px in range(4):
                    x=min(bx+px,w-1); y=min(by+py,h-1); i=(y*w+x)*4
                    pix.append(tuple(rgba[i:i+4]))
            alphas=[p[3] for p in pix]; a0=max(alphas); a1=min(alphas)
            if a0==a1: a0=min(255,a0); a1=max(0,a1-1) if a1 else 0
            ap=[a0,a1,(6*a0+1*a1)//7,(5*a0+2*a1)//7,(4*a0+3*a1)//7,(3*a0+4*a1)//7,(2*a0+5*a1)//7,(1*a0+6*a1)//7]
            abits=0
            for n,a in enumerate(alphas):
                idx=min(range(8), key=lambda k:(a-ap[k])*(a-ap[k]))
                abits |= idx << (3*n)
            colorpix=bytearray()
            for r,g,b,a in pix: colorpix += bytes([r,g,b,255])
            out += bytes([a0,a1]) + abits.to_bytes(6,'little') + bc1_encode(bytes(colorpix),4,4)
    return bytes(out)

# ---------------- file format ----------------

def zstd_decompress(data:bytes)->bytes:
    return zstd.ZstdDecompressor().decompress(data)

def zstd_compress(data:bytes)->bytes:
    return zstd.ZstdCompressor(level=3, write_content_size=True).compress(data)

class Format:
    def __init__(self, kind, px_w, px_h, sw_w, sw_h, bpe, bh, bc=None):
        self.kind=kind; self.px_w=px_w; self.px_h=px_h; self.sw_w=sw_w; self.sw_h=sw_h; self.bpe=bpe; self.bh=bh; self.bc=bc

def infer_format(path:Path, raw:bytes)->Format:
    name=path.name.lower()
    if '.canvas.zs' in name or name.endswith('.canvas.zs'):
        return Format('canvas RGBA8',256,256,256,256,4,16,None)
    if 'thumb' in name:
        return Format('thumb BC3',256,256,64,64,16,8,'bc3')
    if len(raw)==98304 or 'food' in name:
        return Format('ugctex BC1 Food',384,384,96,96,8,16,'bc1')
    return Format('ugctex BC1',512,512,128,128,8,16,'bc1')

def decode_texture(path:Path):
    raw=zstd_decompress(path.read_bytes())
    fmt=infer_format(path,raw)
    linear=deswizzle_block_linear(raw, fmt.sw_w, fmt.sw_h, fmt.bpe, fmt.bh)
    if fmt.bc=='bc1': rgba=bc1_decode(linear,fmt.px_w,fmt.px_h)
    elif fmt.bc=='bc3': rgba=bc3_decode(linear,fmt.px_w,fmt.px_h)
    else: rgba=linear
    rgba=from_game_linear_rgba(rgba)
    return Image.frombytes('RGBA',(fmt.px_w,fmt.px_h),rgba), fmt, raw

def encode_texture(img:Image.Image, fmt:Format, fit_mode:str='contain')->bytes:
    rgba=to_game_linear_rgba(img, fmt.px_w, fmt.px_h, fit_mode)
    if fmt.bc in (None,'bc1'):
        ba=bytearray(rgba)
        for i in range(3,len(ba),4): ba[i]=255 if ba[i]>=128 else 0
        rgba=bytes(ba)
    if fmt.bc=='bc1': linear=bc1_encode(rgba,fmt.px_w,fmt.px_h)
    elif fmt.bc=='bc3': linear=bc3_encode(rgba,fmt.px_w,fmt.px_h)
    else: linear=rgba
    sw=swizzle_block_linear(linear,fmt.sw_w,fmt.sw_h,fmt.bpe,fmt.bh)
    return zstd_compress(sw)

# ---------------- grouping ----------------

def item_key_for(path:Path)->str:
    n = path.name
    n = re.sub(r'_Thumb(?=\.)', '', n, flags=re.I)
    n = re.sub(r'\.canvas\.zs$', '', n, flags=re.I)
    n = re.sub(r'\.ugctex\.zs$', '', n, flags=re.I)
    return n

def subkind_for(path:Path)->str:
    n=path.name.lower()
    if '.canvas.zs' in n: return 'Canvas'
    if 'thumb' in n: return 'Thumbnail'
    return 'Main texture'

def category_for_key(key:str)->str:
    # Known TL:LDR UGC prefixes. Falls back cleanly if future names appear.
    known = [
        'Facepaint', 'Goods', 'Clothes', 'Exterior', 'Interior',
        'MapObject', 'MapFloor', 'Food'
    ]
    low = key.lower()
    for k in known:
        if low.startswith('ugc' + k.lower()):
            return k
    m = re.match(r'Ugc([A-Za-z]+)', key)
    return m.group(1) if m else 'Other'


def compact_item_label(key:str, files:list[Path])->str:
    # UgcFood000 -> 000 - C T TH
    m = re.search(r'(\d+)$', key)
    num = m.group(1) if m else key
    kinds = {subkind_for(f) for f in files}
    flags = []
    if 'Canvas' in kinds: flags.append('C')
    if 'Main texture' in kinds: flags.append('T')
    if 'Thumbnail' in kinds: flags.append('TH')
    return f"{num} - {' '.join(flags)}"

# ---------------- GUI ----------------

class MiniTLEditor((TkinterDnD.Tk if DND_AVAILABLE else tk.Tk)):
    def __init__(self):
        super().__init__()
        self.title('TL-UGC-Editor — v0.1.2')
        self.geometry('1080x700')
        self.workdir=None; self.loaded_from_zip=None
        self.files=[]; self.items={}; self.selected_item=None
        self.current_file=None; self.current_img=None; self.current_fmt=None; self.tk_img=None
        self.fit_mode=tk.StringVar(value='contain')
        self.bg_mode=tk.StringVar(value='dark')
        self._auto_selecting=False
        self._tree_indicator_click=False
        self.status=tk.StringVar(value='Ready. Open a save ZIP/folder to start.')
        self._build_ui()

    def _build_ui(self):
        top=ttk.Frame(self,padding=8); top.pack(fill=tk.X)
        ttk.Button(top,text='Open save ZIP',command=self.open_zip).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(top,text='Open save folder',command=self.open_folder).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(top,text='Save As ZIP',command=self.save_as_zip).pack(side=tk.LEFT,padx=(0,18))
        ttk.Button(top,text='Export Selected as PNG',command=self.export_png).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(top,text='Replace with PNG',command=self.import_png).pack(side=tk.LEFT,padx=(0,12))
        ttk.Label(top,text='Fit:').pack(side=tk.LEFT)
        self.fit_combo=ttk.Combobox(top,textvariable=self.fit_mode,values=['contain','cover','stretch'],width=9,state='readonly')
        self.fit_combo.pack(side=tk.LEFT,padx=(0,10))
        ttk.Label(top,text='BG:').pack(side=tk.LEFT)
        self.bg_combo=ttk.Combobox(top,textvariable=self.bg_mode,values=['dark','light','checker'],width=8,state='readonly')
        self.bg_combo.pack(side=tk.LEFT)
        self.bg_combo.bind('<<ComboboxSelected>>', lambda e: self.draw_preview())

        main=ttk.Panedwindow(self,orient=tk.HORIZONTAL); main.pack(fill=tk.BOTH,expand=True,padx=8,pady=8)
        left=ttk.Frame(main,padding=6); right=ttk.Frame(main,padding=6); main.add(left,weight=1); main.add(right,weight=3)
        ttk.Label(left,text='UGC items').pack(anchor=tk.W)
        self.tree=ttk.Treeview(left,show='tree',height=28)
        self.normal_tree_font=tkfont.nametofont('TkDefaultFont')
        self.bold_tree_font=self.normal_tree_font.copy()
        self.bold_tree_font.configure(weight='bold')
        self.underline_tree_font=self.normal_tree_font.copy()
        self.underline_tree_font.configure(underline=True)
        self.bold_underline_tree_font=self.normal_tree_font.copy()
        self.bold_underline_tree_font.configure(weight='bold', underline=True)
        self.tree.tag_configure('item', font=self.underline_tree_font)
        self.tree.tag_configure('selected_file', font=self.bold_tree_font)
        self.tree.tag_configure('selected_item', font=self.bold_underline_tree_font)
        self.tree.pack(fill=tk.BOTH,expand=True)
        self.tree.bind('<Button-1>', self.on_tree_mouse_down, add='+')
        self.tree.bind('<<TreeviewSelect>>',self.on_tree_select)
        self.info=tk.StringVar(value='Open a save ZIP/folder to start.')
        ttk.Label(left,textvariable=self.info,wraplength=360).pack(anchor=tk.W,pady=(8,0))
        ttk.Label(left,text='⚠ Back up your save first. This tool edits UGC files only.',wraplength=360).pack(anchor=tk.W,pady=(8,0))
        ttk.Label(right,text='Preview').pack(anchor=tk.W)
        self.canvas=tk.Canvas(right,background='#222')
        self.canvas.pack(fill=tk.BOTH,expand=True)
        self.canvas.bind('<Configure>',lambda e:self.draw_preview())

        statusbar=ttk.Label(self,textvariable=self.status,anchor=tk.W,padding=(8,4),relief=tk.SUNKEN)
        statusbar.pack(side=tk.BOTTOM,fill=tk.X)
        self.setup_drag_drop()


    def set_status(self, msg:str):
        self.status.set(msg)

    def handle_error(self, title:str, err:Exception, popup:bool=False):
        msg=f'{title}: {err}'
        self.set_status(msg)
        print(msg)
        traceback.print_exc()
        if popup:
            messagebox.showerror(title, str(err))

    def setup_drag_drop(self):
        if not DND_AVAILABLE:
            self.set_status('Ready. Drag/drop disabled unless tkinterdnd2 is installed.')
            return
        for widget in (self, self.tree, self.canvas):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind('<<Drop>>', self.on_drop)
            except Exception:
                pass

    def parse_drop_files(self, data:str):
        # Tk returns either normal paths or braced paths for names with spaces.
        return [Path(p) for p in self.tk.splitlist(data)]

    def on_drop(self, event):
        paths=self.parse_drop_files(event.data)
        if not paths:
            return
        first=paths[0]
        try:
            if first.suffix.lower()=='.zip':
                self.open_zip_path(first)
            elif first.suffix.lower() in ('.png','.jpg','.jpeg','.webp','.bmp'):
                self.replace_with_image(Image.open(first).convert('RGBA'), source_name=first.name)
            else:
                self.set_status(f'Dropped file type not supported: {first.name}')
        except Exception as e:
            self.handle_error('Drop failed', e)

    def cleanup_workdir(self):
        if self.workdir and self.loaded_from_zip and Path(self.workdir).exists(): shutil.rmtree(self.workdir,ignore_errors=True)
        self.workdir=None; self.loaded_from_zip=None
    def open_zip(self):
        p=filedialog.askopenfilename(filetypes=[('ZIP files','*.zip'),('All files','*.*')])
        if not p: return
        self.open_zip_path(Path(p))
    def open_zip_path(self, p:Path):
        self.cleanup_workdir(); tmp=Path(tempfile.mkdtemp(prefix='mini_tl_ugc_'))
        try:
            with zipfile.ZipFile(p,'r') as z: z.extractall(tmp)
        except Exception as e:
            self.handle_error('Open failed',e,popup=True); return
        self.workdir=tmp; self.loaded_from_zip=Path(p); self.scan()
        self.set_status(f'Loaded ZIP: {p.name}')
    def open_folder(self):
        p=filedialog.askdirectory()
        if not p: return
        self.cleanup_workdir(); self.workdir=Path(p); self.loaded_from_zip=None; self.scan(); self.set_status(f'Loaded folder: {Path(p).name}')
    def scan(self):
        self.files=sorted([p for p in self.workdir.rglob('Ugc*.zs') if any(s in p.name.lower() for s in ['.ugctex.zs','.canvas.zs'])])
        self.items={}
        for f in self.files:
            self.items.setdefault(item_key_for(f),[]).append(f)
        for k in self.items:
            self.items[k]=sorted(self.items[k], key=lambda p: {'Canvas':0,'Main texture':1,'Thumbnail':2}.get(subkind_for(p),9))
        self.current_img=None; self.current_file=None; self.current_fmt=None; self.selected_item=None
        self.refresh_tree()
        cats = sorted({category_for_key(k) for k in self.items})
        cat_text = ', '.join(cats) if cats else 'None'
        self.info.set(
            f'Found {len(self.items)} item(s), {len(self.files)} UGC file(s).\n'
            f'Categories: {cat_text}\n'
            'Select an item to replace all parts, or expand it to pick canvas/main/thumb individually.'
        )
        self.set_status(f'Found {len(self.items)} item(s), {len(self.files)} UGC file(s).')
        self.draw_preview()
    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        if not self.items:
            return
        grouped = {}
        for key in sorted(self.items.keys()):
            grouped.setdefault(category_for_key(key), []).append(key)
        for cat in sorted(grouped.keys()):
            cat_id = 'cat:' + cat
            self.tree.insert('', tk.END, iid=cat_id, text=cat, open=True)
            for key in grouped[cat]:
                label = compact_item_label(key, self.items[key])
                parent = self.tree.insert(cat_id, tk.END, iid='item:'+key, text=label, open=False, tags=('item',))
                # Expand the item to access individual files. Selecting the item itself still previews/replaces the whole item.
                for f in self.items[key]:
                    rel = str(f.relative_to(self.workdir))
                    short={'Canvas':'C canvas','Main texture':'T texture','Thumbnail':'TH thumb'}.get(subkind_for(f),subkind_for(f))
                    self.tree.insert(parent, tk.END, iid='file:'+rel, text=short)
    def pretty_item(self, key:str)->str:
        m=re.match(r'([A-Za-z]+)(\d+)$', key)
        if not m:
            return key
        return f"{category_for_key(key)} {int(m.group(2)):03d}"

    def pretty_kind(self, path:Path|None)->str:
        if path is None:
            return 'Item'
        return {
            'Canvas':'Canvas',
            'Main texture':'Texture',
            'Thumbnail':'Thumbnail',
        }.get(subkind_for(path), subkind_for(path))


    def on_tree_mouse_down(self, event):
        """Remember when the user clicked the expand/collapse indicator.
        Tk's Treeview selection event fires for that click too; without this guard,
        item auto-focus would immediately re-open the node and make the minus button
        look broken.
        """
        try:
            node = self.tree.identify_row(event.y)
            elem = self.tree.identify_element(event.x, event.y)
            self._tree_indicator_click = bool(node.startswith('item:') and 'indicator' in str(elem).lower())
        except Exception:
            self._tree_indicator_click = False
        self.after_idle(lambda: setattr(self, '_tree_indicator_click', False))

    def update_tree_highlight(self):
        for iid in self.tree.get_children(''):
            self.tree.item(iid, tags=())
            for item in self.tree.get_children(iid):
                self.tree.item(item, tags=('item',))
                for child in self.tree.get_children(item):
                    self.tree.item(child, tags=())
        sel=self.tree.selection()
        if not sel:
            return
        node=sel[0]
        if node.startswith('item:'):
            self.tree.item(node, tags=('selected_item',))
        elif node.startswith('file:'):
            self.tree.item(node, tags=('selected_file',))

    def on_tree_select(self,event=None):
        if self._auto_selecting:
            return
        sel=self.tree.selection()
        if not sel: return
        node=sel[0]
        self.update_tree_highlight()
        if node.startswith('cat:'):
            self.selected_item=None
            self.current_file=None; self.current_img=None; self.current_fmt=None
            self.info.set(f'Category: {node[4:]}\nExpand an item, or select an item to preview/replace it.')
            self.set_status(f'Viewing category: {node[4:]}')
            self.draw_preview()
            return
        if node.startswith('item:'):
            key=node[5:]; self.selected_item=key
            self.update_tree_highlight()
            if not self._tree_indicator_click:
                self.tree.item(node, open=True)
            files=self.items.get(key,[])
            chosen=next((f for f in files if subkind_for(f)=='Main texture'), files[0] if files else None)
            if chosen:
                # Parent item stays selected, but previews the default/main texture.
                # Replace with PNG targets the whole item while this node is selected.
                self.load_texture(chosen, item_key=key)
            self.update_tree_highlight()
            self.set_status(f'Editing: {self.pretty_item(key)} → Texture (replace targets C/T/TH)')
        elif node.startswith('file:'):
            rel=node[5:]; path=self.workdir / rel
            self.selected_item=item_key_for(path)
            self.load_texture(path, item_key=self.selected_item)
            self.set_status(f'Editing: {self.pretty_item(self.selected_item)} → {self.pretty_kind(path)}')
    def load_texture(self,path:Path,item_key=None):
        try: img,fmt,raw=decode_texture(path)
        except Exception as e: self.handle_error('Load failed',e); return
        self.current_file=path; self.current_img=img; self.current_fmt=fmt
        rel=path.relative_to(self.workdir) if self.workdir else path
        item_line=f'Item: {self.pretty_item(item_key)}\n' if item_key else ''
        self.info.set(f'{item_line}Selected: {subkind_for(path)}\n{rel}\nDecoded: {img.width} x {img.height}\nFormat: {fmt.kind}\nRaw: {len(raw)} bytes')
        self.draw_preview()
    def draw_checker_bg(self, cw:int, ch:int, size:int=16):
        c1='#d8d8d8'; c2='#9f9f9f'
        for y in range(0, ch, size):
            for x in range(0, cw, size):
                self.canvas.create_rectangle(x, y, x+size, y+size, fill=(c1 if ((x//size + y//size) % 2 == 0) else c2), outline='')

    def draw_preview(self):
        self.canvas.delete('all')
        cw=max(1,self.canvas.winfo_width()); ch=max(1,self.canvas.winfo_height())
        bg=self.bg_mode.get()
        if bg=='light':
            self.canvas.configure(background='#eee')
            text_fill='black'
        elif bg=='checker':
            self.canvas.configure(background='#bbb')
            self.draw_checker_bg(cw,ch)
            text_fill='black'
        else:
            self.canvas.configure(background='#222')
            text_fill='white'
        if self.current_img is None:
            self.canvas.create_text(20,20,anchor=tk.NW,fill=text_fill,text='No texture selected')
            return
        img=self.current_img; scale=min(cw/img.width,ch/img.height,4.0); nw=max(1,int(img.width*scale)); nh=max(1,int(img.height*scale))
        disp=img.resize((nw,nh),Image.Resampling.NEAREST)
        self.tk_img=ImageTk.PhotoImage(disp); self.canvas.create_image(cw//2,ch//2,image=self.tk_img)
    def export_png(self):
        if self.current_img is None or self.current_file is None:
            self.set_status('No texture selected. Select a UGC item/file first.'); return
        default=self.current_file.name.replace('.zs','.png')
        p=filedialog.asksaveasfilename(defaultextension='.png',initialfile=default,filetypes=[('PNG','*.png')])
        if p:
            self.current_img.save(p)
            self.set_status(f'Exported PNG: {Path(p).name}')
    def ask_image(self):
        p=filedialog.askopenfilename(filetypes=[('Images','*.png;*.jpg;*.jpeg;*.webp;*.bmp'),('All files','*.*')])
        if not p: return None
        img=Image.open(p).convert('RGBA')
        img.filename=p
        return img
    def import_image_to_item(self, img:Image.Image):
        if not self.selected_item:
            self.set_status('No item selected. Select an item first.')
            return
        failures=[]
        for f in self.items.get(self.selected_item,[]):
            try:
                _,fmt,_=decode_texture(f)
                f.write_bytes(encode_texture(img,fmt,self.fit_mode.get()))
            except Exception as e:
                failures.append(f'{f.name}: {e}')
        files=self.items.get(self.selected_item,[])
        preview=next((f for f in files if subkind_for(f)=='Main texture'), files[0] if files else None)
        if preview: self.load_texture(preview,self.selected_item)
        if failures:
            self.set_status('Some imports failed: ' + '; '.join(failures[:2]))
        else:
            self.set_status(f'Replaced {self.pretty_item(self.selected_item)} — C/T/TH. Use Save As ZIP when done.')

    def import_image_to_file(self, img:Image.Image, file_path:Path):
        _,fmt,_=decode_texture(file_path)
        file_path.write_bytes(encode_texture(img,fmt,self.fit_mode.get()))
        self.load_texture(file_path,self.selected_item)

    def replace_with_image(self, img:Image.Image, source_name:str='image'):
        sel=self.tree.selection()
        node=sel[0] if sel else ''
        if node.startswith('file:'):
            if self.current_file is None:
                self.set_status('No sub-file selected. Expand an item and select C/T/TH first.'); return
            try:
                self.import_image_to_file(img,self.current_file)
                self.set_status(f'Replaced {self.pretty_item(self.selected_item)} → {self.pretty_kind(self.current_file)} with {source_name}. Use Save As ZIP when done.')
            except Exception as e:
                self.handle_error('Import failed',e)
        elif node.startswith('item:'):
            self.import_image_to_item(img)
            self.set_status(f'Replaced {self.pretty_item(self.selected_item)} — C/T/TH with {source_name}. Use Save As ZIP when done.')
        else:
            self.set_status('Select an item for all layers, or select C/T/TH for one layer.')

    def import_png(self):
        img=self.ask_image()
        if img is None: return
        self.replace_with_image(img, source_name=Path(getattr(img, 'filename', '')).name or 'image')
    def save_as_zip(self):
        if self.workdir is None:
            self.set_status('Nothing open. Open a save first.'); return
        initial=(self.loaded_from_zip.stem+'_edited.zip') if self.loaded_from_zip else 'edited_tl_save.zip'
        out=filedialog.asksaveasfilename(defaultextension='.zip',initialfile=initial,filetypes=[('ZIP','*.zip')])
        if not out: return
        try:
            with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
                for f in self.workdir.rglob('*'):
                    if f.is_file(): z.write(f,f.relative_to(self.workdir).as_posix())
            self.set_status(f'Saved edited ZIP: {Path(out).name}')
        except Exception as e: self.handle_error('Save failed',e,popup=True)
    def destroy(self): self.cleanup_workdir(); super().destroy()

if __name__=='__main__':
    app=MiniTLEditor(); app.mainloop()
