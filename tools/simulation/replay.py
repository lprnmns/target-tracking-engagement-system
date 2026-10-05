import json, sys, math, time, dataclasses
sys.path.insert(0, '/tmp/sim')
import app.services.light_tracking_controller as ltc
PID = json.loads('''{"kp_x": 12.0,"ki_x": 0.0,"kd_x": 1.25,"kp_y": 8.0,"ki_y": 0.0,"kd_y": 2.0,"invert_x": false,"invert_y": false}''')
class Clock:
    t = 0.0
ltc.time.monotonic = lambda: Clock.t
def make(mode, cap=True, ff=0.25, pid=True):
    c = ltc.LightTrackingController(mode)
    upd = {}
    if pid: upd.update({k: v for k, v in PID.items()})
    if cap: upd.update(max_speed=min(c.params.max_speed, 3333.0), tilt_max_speed=min(c.params.tilt_max_speed, 2222.0))
    if ff is not None: upd['feedforward_gain'] = ff
    c.params = dataclasses.replace(c.params, **upd)
    return c
def load(fn, B, E):
    R = []
    for l in open(fn):
        d = json.loads(l)
        if d['subsystem'] != 'TRACKING_TIMEBASE': continue
        if not (B <= d['ts'] <= E): continue
        R.append((d['ts'], d['details']))
    R.sort(key=lambda a: a[0])
    return R
def replay(R, mode, cap=True, ff=0.25, offy=-22.0, ramp=8000.0, spd=133.333, tid=None):
    c = make(mode, cap, ff)
    out = []
    th = 0.0; v = 0.0; prev = None; prev_tid = None
    for ts, x in R:
        dt = 0.017 if prev is None else max(0.001, min(0.1, ts - prev))
        # integrate turret over dt with current velocity
        th += v / spd * dt
        prev = ts
        Clock.t = ts
        b = x.get('bbox')
        if not b or (tid and x['track_id'] not in tid):
            cmd = 0.0
            c.reset()
        else:
            if prev_tid is not None and x['track_id'] != prev_tid: c.reset()
            prev_tid = x['track_id']
            cx = b['x'] + b['w'] / 2; cy = b['y'] + b['h'] / 2
            o = c.update(target_x=cx, target_y=cy - offy, frame_width=1280, frame_height=720, dt=dt, bbox_width=b['w'], bbox_height=b['h'], is_fresh=x['measurement_status'] == 'NEW')
            cmd = float(o.speed_x)
        v = max(v - ramp * dt, min(v + ramp * dt, cmd))
        out.append(dict(ts=ts, cap=x['capture_timestamp_ms'] / 1000, th=th, v=v, x=x))
    return out
