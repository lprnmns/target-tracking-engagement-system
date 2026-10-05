import replay as r, numpy as np, bisect, sys
def fit(o, tids, taus=np.arange(-0.10,0.16,0.01), fs=np.arange(15,90,0.5), tmin=None):
    ts=np.array([a['ts'] for a in o]); th=np.array([a['th'] for a in o])
    rows=[(a['cap'],a['x']['bbox']['x']+a['x']['bbox']['w']/2-640,a['x']['track_id']) for a in o if a['x'].get('bbox') and a['x']['measurement_status']=='NEW' and a['x']['track_id'] in tids]
    cap=np.array([q[0] for q in rows]); ex=np.array([q[1] for q in rows]); tk=np.array([q[2] for q in rows])
    best=None
    for tau in taus:
        thc=np.interp(cap+tau,ts,th)
        for f in fs:
            phi=thc+ex/f
            res=0; 
            for t in set(tk):
                m=tk==t; res+=((phi[m]-phi[m].mean())**2).sum()
            rms=np.sqrt(res/len(phi))
            if best is None or rms<best[0]: best=(rms,tau,f)
    return best, (cap,ex,tk,ts,th)
