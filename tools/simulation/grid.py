import atis_sim as S, json, numpy as np
out=lambda f: json.loads(open(f).read().split('\n')[4])
r3=out('/mnt/user-data/uploads/CLAUDE_CODE_YARISMA_ANALIZ/kopru/cevap/102_zigzag3.out')
r2=json.loads([l for l in open('/mnt/user-data/uploads/CLAUDE_CODE_YARISMA_ANALIZ/kopru/cevap/100_zigzag2.out')][4])
F=41.0; V=120.0
segs=[('Z3',S.seg_data('/tmp/k103/k103.jsonl',r3['bas'],r3['bit'],142,27.5,50)),('Z2',S.seg_data('/tmp/k101/k101.jsonl',r2['bas'],r2['bit'],103,41.5,60))]
def run(k,lead,Te,sig=3.3):
    res=[]
    for ad,D in segs:
        # flight time from balloon angular size assuming 13 cm balloon
        Tf=np.clip((0.13/np.radians(np.median(D['w'])/F))/V,0.02,0.15)
        sh=S.simulate(D,F,k,lead,Te,Tf,sigma_px=sig)
        res.append((len(sh),sum(s[1] for s in sh)))
    n=sum(a for a,b in res); h=sum(b for a,b in res)
    return n,h
print('kapi  lead  Te   atis  bekl.isabet  isabet/atis')
for Te in [0.10,0.15,0.20]:
  for k in [0.10,0.15,0.25,0.35,0.5]:
    for lead in [0,0.5,1.0,1.5]:
      n,h=run(k,lead,Te)
      print(f'{k:.2f} {lead:4.1f} {Te:.2f} {n:5d} {h:8.1f} {h/max(n,1):8.2f}')
