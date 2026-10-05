# Y17: Asama 3 + hedef BALON -> hava araci modeli 10 karede 1, SADECE GOSTERIM.
# Hepsi-ya-hic: tum degisiklikler dogrulanmadan hicbir dosya yazilmaz.
import sys, shutil, py_compile
from pathlib import Path

kok = Path(sys.argv[1])
vp = kok / "backend/app/services/vision_pipeline.py"
tl = kok / "backend/app/services/tracking_loop.py"
ws = kok / "backend/app/api/routes_ws.py"

def degistir(metin, eski, yeni, ad):
    n = metin.count(eski)
    if n != 1:
        raise SystemExit(f"HATA {ad}: beklenen 1 eslesme, bulunan {n}. HICBIR DOSYA DEGISMEDI.")
    return metin.replace(eski, yeni)

def oku(p):
    return p.read_text(encoding="utf-8")

v = oku(vp); t = oku(tl); w = oku(ws)
if "y17_gosterim" in v or "y17_kontrol_olayi" in t or "y17_kontrol_olayi" in w:
    raise SystemExit("Y17 zaten kurulu, cikiliyor.")

# --- vision_pipeline.py ---
v = degistir(v,
    '        if target_policy == "BALLOON" and competition_stage not in {"STAGE_2", "STAGE2"}:\n',
    '        if target_policy == "BALLOON" and competition_stage not in {"STAGE_2", "STAGE2", "STAGE_3", "STAGE3"}:\n',
    "V1 model_specs")

v = degistir(v,
    '                if active_policy in {"BALLOON_AIRCRAFT", "STAGE_3"} or competition_stage in {"STAGE_3"}:\n'
    '                    # Aşama 3 (İHA + Balon)',
    '                y17_gosterim = active_policy == "BALLOON" and str(competition_stage or "").upper() in {"STAGE_3", "STAGE3"}\n'
    '                if y17_gosterim:\n'
    '                    # Y17: Asama 3 + hedef BALON -> govde modeli SADECE GOSTERIM icin 10 karede 1 calisir.\n'
    '                    if self._last_body_inference_mono > 0.0 and self._body_cadence_counter % 10 != 0:\n'
    '                        skip_body_inference = True\n'
    '                        body_skip_reason = "y17_gosterim_10kare"\n'
    '                elif active_policy in {"BALLOON_AIRCRAFT", "STAGE_3"} or competition_stage in {"STAGE_3"}:\n'
    '                    # Aşama 3 (İHA + Balon)',
    "V2 kadans")

v = degistir(v,
    '                elif active_policy == "BALLOON":\n'
    '                    body_detections = []\n',
    '                elif y17_gosterim:\n'
    '                    # Y17: govdeler yalniz ekranda; IFF / ROI / sahiplik YOK, tum balonlar serbest.\n'
    '                    body_detections = [b.model_copy(update={"target_team": "unknown"}) for b in body_detections]\n'
    '                    warnings.append(f"y17_gosterim:bodies={len(body_detections)},balloons={len(balloon_detections)}")\n'
    '                elif active_policy == "BALLOON":\n'
    '                    body_detections = []\n',
    "V3 politika dali")

v = degistir(v,
    '                if self.color_classifier is not None and body_detections and ran_body_inference:\n',
    '                if self.color_classifier is not None and body_detections and ran_body_inference and not y17_gosterim:\n',
    "V4 renk siniflandirici")

v = degistir(v,
    '                if self.stage3_range is not None and body_detections and ran_body_inference:\n',
    '                if self.stage3_range is not None and body_detections and ran_body_inference and not y17_gosterim:\n',
    "V5 menzil")

v = degistir(v,
    '            range_rules=getattr(getattr(getattr(self, "config", None), "decision", None), "range_rules", None),\n'
    '        )\n'
    '        event = VisionEvent(\n',
    '            range_rules=getattr(getattr(getattr(self, "config", None), "decision", None), "range_rules", None),\n'
    '        )\n'
    '        if active_policy == "BALLOON" and str(competition_stage or "").upper() in {"STAGE_3", "STAGE3"} and target_verdicts:\n'
    '            # Y17: govde etiketi notr; dost/dusman/ates karari gosterilmez.\n'
    '            try:\n'
    '                target_verdicts = [\n'
    '                    vd.model_copy(update={"label_tr": f"HAVA ARACI: {vd.target_class}", "fire_authorized": False})\n'
    '                    if vd.kind == "body" else vd\n'
    '                    for vd in target_verdicts\n'
    '                ]\n'
    '            except Exception:\n'
    '                pass\n'
    '        event = VisionEvent(\n',
    "V6 etiket")

v = degistir(v,
    '    def _active_target_policy(self) -> str:\n',
    '    def y17_gosterim_modu(self) -> bool:\n'
    '        """Y17: Asama 3 + hedef BALON -> govdeler yalniz gosterim."""\n'
    '        try:\n'
    '            st = self.operation.state()\n'
    '            stg = getattr(st, "competition_stage", None)\n'
    '            stg = stg.value if hasattr(stg, "value") else str(stg or "")\n'
    '            return str(st.target_policy.value) == "BALLOON" and str(stg).upper() in {"STAGE_3", "STAGE3"}\n'
    '        except Exception:\n'
    '            return False\n'
    '\n'
    '    def y17_kontrol_olayi(self, event):\n'
    '        """Kontrol/IFF/sahiplik yoluna govdesiz kopya verir (yalniz Y17 modunda)."""\n'
    '        if event is None or not getattr(event, "body_detections", None):\n'
    '            return event\n'
    '        if not self.y17_gosterim_modu():\n'
    '            return event\n'
    '        return event.model_copy(update={"body_detections": []})\n'
    '\n'
    '    def _active_target_policy(self) -> str:\n',
    "V7 yardimci")

# --- tracking_loop.py ---
t = degistir(t,
    '                vision_event = self.vision_pipeline.latest()\n'
    '                frame_width, frame_height = self._frame_size()\n',
    '                vision_event = self.vision_pipeline.latest()\n'
    '                # Y17: Asama3+BALON gosterim govdeleri takip/IFF/atis yoluna GIRMEZ.\n'
    '                if vision_event is not None and hasattr(self.vision_pipeline, "y17_kontrol_olayi"):\n'
    '                    vision_event = self.vision_pipeline.y17_kontrol_olayi(vision_event)\n'
    '                frame_width, frame_height = self._frame_size()\n',
    "T1 kontrol olayi")

# --- routes_ws.py (takip kapaliyken onizleme kimlik yolu) ---
w = degistir(w,
    '            if not runtime.auto_tracker.tracking_active:\n'
    '                preview_tracks = runtime.auto_tracker.multi_target_tracker.update(vision_event)\n'
    '                preview_associations = runtime.association.update(vision_event, preview_tracks)\n'
    '                preview_confirmations = runtime.hit_confirmation.update(vision_event, preview_tracks)\n'
    '                runtime.target_registry.update(\n'
    '                    vision_event,\n',
    '            if not runtime.auto_tracker.tracking_active:\n'
    '                # Y17: gosterim govdeleri kimlik/sahiplik yoluna girmez.\n'
    '                _y17_olay = runtime.vision_pipeline.y17_kontrol_olayi(vision_event) if hasattr(runtime.vision_pipeline, "y17_kontrol_olayi") else vision_event\n'
    '                preview_tracks = runtime.auto_tracker.multi_target_tracker.update(_y17_olay)\n'
    '                preview_associations = runtime.association.update(_y17_olay, preview_tracks)\n'
    '                preview_confirmations = runtime.hit_confirmation.update(_y17_olay, preview_tracks)\n'
    '                runtime.target_registry.update(\n'
    '                    _y17_olay,\n',
    "W1 onizleme")

# derleme kontrolu (gecici dosyalarla)
import tempfile, os
for ad, metin in (("vp", v), ("tl", t), ("ws", w)):
    fd, yol = tempfile.mkstemp(suffix=".py"); os.close(fd)
    Path(yol).write_text(metin, encoding="utf-8")
    py_compile.compile(yol, doraise=True)
    os.remove(yol)

for p, metin in ((vp, v), (tl, t), (ws, w)):
    yedek = p.with_name(p.name + ".k148_oncesi")
    if not yedek.exists():
        shutil.copy2(p, yedek)
    p.write_text(metin, encoding="utf-8")
    print("yazildi:", p, "yedek:", yedek.name)
print("Y17 KURULDU")
