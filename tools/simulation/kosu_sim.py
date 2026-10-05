import json, numpy as np, atis_sim as S
L='/tmp/k110/k110.jsonl'
RUNS=[('V5',1790807061.634074,257,15.0,42.0),('V1',1790807238.6561172,17,0.0,25.0),('V2',1790807446.755532,6,0.0,22.0),('V4',1790807636.5156333,7,0.0,25.0)]
WD=420.0  # px*m (15 m -> ~30 px, 5 m -> ~80 px, isaretlere gore)
V=120.0
rng=np.random.default_rng(3)
def sim(D,f_deg,Dtest,k,lead,Te,disp_cm=1.5,bias_cm=1.5,cool=0.6,nmc=400):
    t,ex,ey,w=D['t'],D['ex'],D['ey'],D['w']
    vw,_,_=S.world_vel(D,f_deg)
    frad=f_deg*57.2958
    dist=WD/w
    wc=w*0.13/Dtest
    pxcm=frad*0.01/dist
    out=[];last=-9
    for i in range(len(t)):
        if t[i]-last<cool: continue
        if np.hypot(ex[i],ey[i])>max(4.0,k*wc[i]): continue
        last=t[i]; te=t[i]+Te
        if te>t[-1]: break
        d=np.interp(te,t,dist); Tf=d/V; L_=lead*vw[i]*Tf
        mx=np.interp(te,t,ex)+np.interp(te,t,vw)*Tf-L_; my=np.interp(te,t,ey)
        pc=np.interp(te,t,pxcm); r=np.interp(te,t,wc)/2
        bx,by=rng.normal(0,bias_cm*pc,(2,nmc)); dx=mx+bx+rng.normal(0,disp_cm*pc,nmc); dy=my+by+rng.normal(0,disp_cm*pc,nmc)
        out.append((t[i],d,np.mean(np.hypot(dx,dy)<=r),mx/pc,my/pc))
    return np.array(out)
DATA={ad:S.seg_data(L,B,1e12,tid,a,b) for ad,B,tid,a,b in RUNS}
if __name__=='__main__':
    for ad,B,tid,a,b in RUNS:
        D=DATA[ad]; print(ad,'kare',len(D['t']),'sure %.1f'%(D['t'][-1]-D['t'][0]),'mesafe %.1f->%.1f'%(WD/D['w'][:10].mean(),WD/D['w'][-10:].mean()))
    for f_deg,Dt,lab in [(41,0.179,'olcek 41 px/der (balon ~18 cm)'),(52,0.14,'olcek 52 px/der (balon 14 cm)')]:
        print('=====',lab)
        for k,lead in [(0.25,0),(0.25,1),(0.35,0),(0.35,1)]:
            row=[]
            for ad,*_ in RUNS:
                A=sim(DATA[ad],f_deg,Dt,k,lead,0.15)
                far=A[A[:,1]>=10]; near=A[A[:,1]<10]
                row.append(f"{ad}: {len(A):2d} atis {A[:,2].mean()*100:3.0f}% (uzak {far[:,2].mean()*100 if len(far) else float('nan'):3.0f}% / yakin {near[:,2].mean()*100 if len(near) else float('nan'):3.0f}%)")
            print(f'kapi {k} lead {lead}: '+' | '.join(row))
        # zamanlama kaynakli sapma
        for ad,*_ in RUNS:
            A=sim(DATA[ad],f_deg,Dt,0.25,0,0.15,disp_cm=0.01,bias_cm=0.01)
            print(f'  {ad} sadece takip: isabet {A[:,2].mean()*100:3.0f}%  yatay sapma med {np.median(abs(A[:,3])):.1f} cm p90 {np.percentile(abs(A[:,3]),90):.1f} cm')
