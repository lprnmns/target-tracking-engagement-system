from PIL import Image, ImageDraw, ImageFont
F=2520.0           # px/rad (~44 px/derece) - olculen kamera olcegi
GOV_W, GOV_H = 0.40, 0.20   # m: hedef govdesi (genislik 40 cm, yukseklik varsayim 20 cm)
IP, BALON = 0.22, 0.13      # m: ip ~22 cm, balon 13 cm
HTOL, VDEP = 0.25, 4.2      # stage3_roi.py sabitleri
W,H=1280,720
img=Image.new('RGB',(W,H),(28,34,40)); d=ImageDraw.Draw(img)
try:
    f=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',16); fb=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',20); fs=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',13)
except: f=fb=fs=ImageFont.load_default()
# kamera kadraji isaretleri
d.line([(W//2-12,H//2-22),(W//2+12,H//2-22)],fill=(245,158,11),width=2); d.line([(W//2,H//2-34),(W//2,H//2-10)],fill=(245,158,11),width=2)
d.text((12,10),"Kamera kadrajı 1280×720, gerçek piksel ölçeğinde (~44 px/derece)",fill=(220,230,240),font=fb)
d.text((12,36),"Gövde 40×20 cm (yükseklik varsayım) · ip ~22 cm · balon 13 cm · Turuncu: balon arama alanı (stage3_roi: yatay ±%25, aşağı 4,2×gövde boyu)",fill=(170,190,205),font=fs)
def ciz(cx, top, dist, etiket):
    k=F/dist/100.0   # px per cm
    gw,gh=GOV_W*100*k, GOV_H*100*k
    x=cx-gw/2; y=top
    # arama alani
    rx0=x-gw*HTOL; rx1=x+gw*(1+HTOL); ry0=y+gh; ry1=y+gh+gh*VDEP
    d.rectangle([rx0,ry0,rx1,ry1],outline=(245,158,11),width=2)
    for yy in range(int(ry0),int(ry1),6): d.point([(rx0+1,yy)],fill=(245,158,11))
    # govde
    d.rectangle([x,y,x+gw,y+gh],fill=(220,50,50),outline=(255,220,220),width=2)
    # ip + balon
    bc_y=y+gh+IP*100*k+BALON*100*k/2; br=BALON*100*k/2
    d.line([(cx,y+gh),(cx,bc_y-br)],fill=(200,200,200),width=1)
    d.ellipse([cx-br,bc_y-br,cx+br,bc_y+br],fill=(235,60,60),outline=(255,255,255))
    d.text((cx-60,y-48),etiket,fill=(255,255,255),font=fb)
    d.text((cx-60,y-26),f"gövde {gw:.0f}×{gh:.0f} px",fill=(220,230,240),font=fs)
    d.text((rx0,ry1+4),f"arama alanı {rx1-rx0:.0f}×{ry1-ry0:.0f} px",fill=(245,158,11),font=fs)
    d.text((rx0,ry1+20),f"= {(rx1-rx0)/k:.0f}×{(ry1-ry0)/k:.0f} cm",fill=(245,158,11),font=fs)
    d.text((cx+br+4,bc_y-8),f"balon {2*br:.0f} px",fill=(255,200,200),font=fs)
    return
ciz(170,150,15,"15 m")
ciz(470,150,10,"10 m")
ciz(930,130,5,"5 m")
img.save('/mnt/user-data/outputs/asama3_balon_arama_alani.png')
print('ok')
