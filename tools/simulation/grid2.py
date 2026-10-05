import atis_sim as S, json, numpy as np
r3=json.loads(open('/mnt/user-data/uploads/CLAUDE_CODE_YARISMA_ANALIZ/kopru/cevap/102_zigzag3.out').read().split('\n')[4])
r2=json.loads([l for l in open('/mnt/user-data/uploads/CLAUDE_CODE_YARISMA_ANALIZ/kopru/cevap/100_zigzag2.out')][4])
F=41.0; V=120.0; TEST_D=0.20; COMP=0.13
segs=[('Z3',S.seg_data('/tmp/k103/k103.jsonl',r3['bas'],r3['bit'],142,27.5,50)),('Z2',S.seg_data('/tmp/k101/k101.jsonl',r2['bas'],r2['bit'],103,41.5,60))]
rng=np.random.default_rng(7)
def sim(D,k,lead,Te,disp_cm=1.5,bias_cm=1.5,cool=0.6,nmc=400):
    t,ex,ey,w=D['t'],D['ex'],D['ey'],D['w']
    vw,_,_=S.world_vel(D,F)
    dist=TEST_D/np.radians(w/F)            # m
    wc=w*COMP/TEST_D                        # yarisma balonu px
    pxcm=F*np.degrees(0.01/dist)            # px per cm
    out=[];last=-9
    for i in range(len(t)):
        if t[i]-last<cool: continue
        if np.hypot(ex[i],ey[i])>max(4.0,k*wc[i]): continue
        last=t[i]; te=t[i]+Te
        if te>t[-1]: break
        d=np.interp(te,t,dist); Tf=d/V
        L=lead*vw[i]*Tf
        mx=np.interp(te,t,ex)+np.interp(te,t,vw)*Tf-L; my=np.interp(te,t,ey)
        pc=np.interp(te,t,pxcm); r=np.interp(te,t,wc)/2
        bx,by=rng.normal(0,bias_cm*pc,2*nmc).reshape(2,-1)
        dx=mx+bx+rng.normal(0,disp_cm*pc,nmc); dy=my+by+rng.normal(0,disp_cm*pc,nmc)
        out.append((t[i],d,np.mean(np.hypot(dx,dy)<=r),mx/pc,my/pc))
    return out
def ozet(k,lead,Te,**kw):
    A=[];
    for ad,D in segs: A+=sim(D,k,lead,Te,**kw)
    A=np.array(A); 
    if not len(A): return 0,0,0,{}
    b={}
    for lo,hi in [(10,20),(7,10),(3,7)]:
        m=(A[:,1]>=lo)&(A[:,1]<hi); b[f'{lo}-{hi}m']=(int(m.sum()),round(A[m,2].mean(),2) if m.sum() else None)
    return len(A),A[:,2].sum(),A[:,2].mean(),b
if __name__=='__main__':
    D=segs[0][1]; d=TEST_D/np.radians(D['w']/F); print('Z3 mesafe tahmini %.1f -> %.1f m'%(d[0],d[-1]))
    D=segs[1][1]; d=TEST_D/np.radians(D['w']/F); print('Z2 mesafe tahmini %.1f -> %.1f m'%(d[0],d[-1]))
    for Te in [0.10,0.15,0.20]:
      print('--- tetik+islem gecikmesi',Te)
      for k in [0.10,0.15,0.25,0.35,0.5]:
        for lead in [0,1.0]:
          n,h,p,b=ozet(k,lead,Te)
          print(f'kapi {k:.2f} lead {lead:.1f}: atis {n:3d} isabet {h:5.1f}  oran {p:.2f}  {b}')
