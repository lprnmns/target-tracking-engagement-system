import json, numpy as np
SPD=133.333
def seg_data(fn, B, E, tid, a, b):
    rows=[]
    for l in open(fn):
        d=json.loads(l)
        if d['subsystem']!='TRACKING_TIMEBASE': continue
        x=d['details']
        if not x.get('bbox') or not x.get('sim') or x['track_id']!=tid: continue
        tc=x['capture_timestamp_ms']/1000-B
        if not (a<=tc<=b): continue
        bb=x['bbox']; s=x['sim']
        rows.append((tc, bb['x']+bb['w']/2-640, bb['y']+bb['h']/2-338, bb['w'], x['measurement_status']=='NEW', s['gw'][0] if s.get('gw') else np.nan, d['ts']-B))
    r=np.array(sorted(rows))
    new=r[r[:,4]==1]
    # de-dup capture times
    _,iu=np.unique(new[:,0],return_index=True); new=new[iu]
    t=new[:,0]; ex=new[:,1]; ey=new[:,2]; w=new[:,3]
    # turret speed (px/s) at capture time: gw speed sent at log time, pan deg/s * f
    gt=r[:,6]; gv=r[:,5]; m=~np.isnan(gv)
    return dict(t=t,ex=ex,ey=ey,w=w,gt=gt[m],gv=gv[m])
def world_vel(D, f, lag=0.06, win=0.15):
    t=D['t']; ex=D['ex']
    # smoothed d(ex)/dt
    dex=np.zeros_like(ex)
    for i in range(len(t)):
        m=(t>=t[i]-win)&(t<=t[i]+win)
        if m.sum()>=3: dex[i]=np.polyfit(t[m],ex[m],1)[0]
    # turret velocity at capture time: command sent ~lag earlier has taken effect
    tv=np.interp(t-lag+ lag, D['gt'], D['gv'])/SPD*f   # px/s (turret right => +)
    return dex+tv, dex, tv
def simulate(D, f, k_gate, lead_frac, T_exit, T_flight, sigma_px=4.0, cooldown=0.6, n_mc=300, rng=np.random.default_rng(1), lag=0.06):
    t=D['t']; ex=D['ex']; ey=D['ey']; w=D['w']
    vw,_,_=world_vel(D,f,lag)
    shots=[]; last=-9
    for i in range(len(t)):
        if t[i]-last<cooldown: continue
        L=lead_frac*vw[i]*T_flight   # lead px (in direction of motion)
        # controller would center target at aim point shifted by L; approximate: aim error at decision
        e=np.hypot(ex[i]+L, ey[i]) if False else np.hypot(ex[i]-(-L)*0, ey[i])
        # gate on raw error to the (lead-shifted) aim point: target should sit at -L relative to crosshair... simplified: gate on |ex_i| as live system
        if np.hypot(ex[i], ey[i]) > max(4.0, k_gate*w[i]): continue
        last=t[i]
        te=t[i]+T_exit
        if te>t[-1]: break
        exe=np.interp(te,t,ex); eye=np.interp(te,t,ey); we=np.interp(te,t,w); ve=np.interp(te,t,vw)
        # target relative to pellet line at arrival: ex at exit + motion during flight - lead
        mx=exe+ve*T_flight - L; my=eye
        dx=mx+rng.normal(0,sigma_px,n_mc); dy=my+rng.normal(0,sigma_px,n_mc)
        p=np.mean(np.hypot(dx,dy)<=we/2*0.9)
        shots.append((t[i],p,mx,my,we,ve))
    return shots
