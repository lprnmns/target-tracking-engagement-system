# Y19: Balonun kilitli oldugu govde izi 0.6 sn'den uzun yoksa (model sinif degistirip yeni iz actiginda)
# kilit birakilir; balon ayni ROI kuraliyla altindaki govdeye yeniden baglanir. Onceden sonsuza dek 'orphan' kaliyordu.
import sys, shutil, py_compile, tempfile, os
from pathlib import Path
p = Path(sys.argv[1]) / "backend/app/services/body_balloon_association_service.py"
m = p.read_text(encoding="utf-8")
if "Y19" in m:
    raise SystemExit("Y19 zaten kurulu.")
eski = "            locked_body_key = self._locked_body_by_track.get(track.track_id)\n            if locked_body_key is not None:\n"
if m.count(eski) != 1:
    raise SystemExit("HATA: baglanti noktasi yok, DOSYA DEGISMEDI")
yeni = (
"            # Y19: kilitli sahip govde izi coast suresinden (0.6 sn) uzun yoksa kilidi birak.\n"
"            # Model sinif degistirince govde yeni iz no aliyor; eski kilit balonu sonsuza dek\n"
"            # 'orphan' tutup birlikte modda atisi engelliyordu. Yeniden baglanma normal ROI +\n"
"            # belirsizlik + 3 kare kuralindan gecer.\n"
"            _y19_lk = self._locked_body_by_track.get(track.track_id)\n"
"            if _y19_lk is not None and not any(self._body_key(b) == _y19_lk for b in bodies):\n"
"                _y19_ls = self._locked_body_last_seen.get(track.track_id, 0.0)\n"
"                if _y19_ls <= 0.0 or (now - _y19_ls) > self.coast_ttl_s:\n"
"                    self._locked_body_by_track.pop(track.track_id, None)\n"
"                    self._clear_track(track.track_id)\n"
"                    if _y19_lk not in self._locked_body_by_track.values():\n"
"                        reserved_body_keys.discard(_y19_lk)\n"
+ eski)
m = m.replace(eski, yeni)
fd, yol = tempfile.mkstemp(suffix=".py"); os.close(fd)
Path(yol).write_text(m, encoding="utf-8"); py_compile.compile(yol, doraise=True); os.remove(yol)
yedek = p.with_name(p.name + ".k156_oncesi")
if not yedek.exists():
    shutil.copy2(p, yedek)
p.write_text(m, encoding="utf-8")
print("Y19 KURULDU, yedek:", yedek.name)
