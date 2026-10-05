import numpy as np, atis_sim as S, kosu_sim as K
K.DATA['BASE']=S.seg_data('/tmp/k115/k115.jsonl',1790808946.922114,1e12,8,0.5,22.0)
K.DATA['E21']=S.seg_data('/tmp/k115/k115.jsonl',1790809124.1150675,1e12,12,6.5,22.0)
order=['V5','V1','V2','V4','BASE','E21']
f,Dt=44.0,0.16
for ad in order:
    D=K.DATA[ad]; print(ad,'kare',len(D['t']),'sure %.1f'%(D['t'][-1]-D['t'][0]),'mesafe %.1f->%.1f'%(K.WD/D['w'][:10].mean(),K.WD/D['w'][-10:].mean()))
for k,lead in [(0.25,0),(0.25,1),(0.35,0)]:
    print(f'--- kapi {k} lead {lead}')
    for ad in order:
        ps=[]; 
        for seed in range(5):
            K.rng=np.random.default_rng(seed); A=K.sim(K.DATA[ad],f,Dt,k,lead,0.15); ps.append(A)
        A=np.vstack(ps); far=A[A[:,1]>=10]; near=A[A[:,1]<10]
        n=len(ps[0])
        print(f"  {ad:5s} atis {n:2d}  isabet {A[:,2].mean()*100:3.0f}%   uzak(10-15m) {far[:,2].mean()*100 if len(far) else float('nan'):3.0f}% [{len(far)//5}]   yakin(5-10m) {near[:,2].mean()*100 if len(near) else float('nan'):3.0f}% [{len(near)//5}]")
print('--- sadece takip (namlu kusursuz), kapi 0.25')
for ad in order:
    A=K.sim(K.DATA[ad],f,Dt,0.25,0,0.15,disp_cm=0.01,bias_cm=0.01)
    print(f'  {ad:5s} isabet {A[:,2].mean()*100:3.0f}%  yatay sapma med {np.median(abs(A[:,3])):.1f} cm  p90 {np.percentile(abs(A[:,3]),90):.1f} cm')
