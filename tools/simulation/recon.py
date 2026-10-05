import json, numpy as np
def load(fn, B, E):
    T=[]; P=[]; F=[]
    for l in open(fn):
        d=json.loads(l)
        if not (B-5<=d['ts']<=E+5): continue
        s=d['subsystem']
        if s=='SIM_POZ': P.append((d['details']['mono'], d['details']['pan'], d['ts']))
        elif s=='TRACKING_TIMEBASE' and d['details'].get('sim'): T.append(d)
        elif d['message'].startswith('Fire cand'): F.append(d)
    return T,P,F
def turret(T, P, spd=133.333):
    # gw = (speed_x, speed_y, mono) as sent; build piecewise-constant speed timeline
    ev={}
    for d in T:
        g=d['details']['sim'].get('gw')
        if g: ev[g[2]]=g[0]
    tm=np.array(sorted(ev)); sp=np.array([ev[t] for t in tm])
    return tm, sp
def integ(tm, sp, t0, th0, tq, spd=133.333):
    # angle at times tq by integrating piecewise-constant sp from t0 (th0)
    grid=np.concatenate([tm, tq]); grid=np.unique(grid[(grid>=min(t0,tq.min()))])
    th=np.zeros(len(grid)); 
    idx=np.searchsorted(tm, grid, side='right')-1
    v=np.where(idx>=0, sp[np.clip(idx,0,None)], 0.0)/spd
    dt=np.diff(grid, prepend=grid[0])
    cum=np.cumsum(np.concatenate([[0],v[:-1]*dt[1:]]))
    c0=np.interp(t0, grid, cum)
    return th0+np.interp(tq, grid, cum)-c0
