import json, numpy as np
def rows(fn,B,a,b,tid):
    R=[]
    for l in open(fn):
        d=json.loads(l)
        if d['subsystem']!='TRACKING_TIMEBASE': continue
        x=d['details']
        if not x.get('bbox') or x['track_id']!=tid or x['measurement_status']!='NEW': continue
        t=x['capture_timestamp_ms']/1000-B
        if a<=t<=b: bb=x['bbox']; R.append((t,bb['x']+bb['w']/2-640,bb['y']+bb['h']/2-338,bb['w']))
    R=np.array(sorted(R)); _,i=np.unique(R[:,0],return_index=True); return R[i]
def degerlendir(R,k=0.25,Te=0.15,cool=0.6,olcek=13/14):
    t,ex,ey,w=R.T; last=-9; s=[]
    for i in range(len(t)):
        if t[i]-last<cool or np.hypot(ex[i],ey[i])>max(4,k*w[i]*olcek): continue
        last=t[i]; te=t[i]+Te
        if te>t[-1]: break
        e=np.hypot(np.interp(te,t,ex),np.interp(te,t,ey)); r=np.interp(te,t,w)*olcek/2
        s.append(e/r)
    s=np.array(s); return len(s), (s<=0.5).mean(), (s<=1).mean(), len(t)
K=[('V1 zigzag-1 (23:28)','/tmp/k88/k88.jsonl',1790800105.654575,25.5,68,501),
   ('V1 zigzag-2 (23:28)','/tmp/k88/k88.jsonl',1790800105.654575,77.5,104,523),
   ('V5 yaklasma (23:51)','/tmp/k89/k89.jsonl',1790801490.330559,2.5,25,57),
   ('V5 Z2 (00:52)','/tmp/k101/k101.jsonl',1790805134.2112372,41.5,60,103),
   ('V5 Z3 (00:55)','/tmp/k103/k103.jsonl',1790805352.2064018,27.5,50,142)]
for ad,fn,B,a,b,tid in K:
    R=rows(fn,B,a,b,tid); n,ic,bal,nk=degerlendir(R)
    print(f'{ad:22s} sure {b-a:4.1f}s atis {n:3d} ({n/(b-a):.2f}/s)  cikista merkez yarisinda {ic*100:3.0f}%  balon icinde {bal*100:3.0f}%')
