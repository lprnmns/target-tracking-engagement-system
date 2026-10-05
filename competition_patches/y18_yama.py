# Y18: Birlikte modda REENGAGE'de takili kalan (balonu gorunur) dusman/bilinmeyen hedefi 1.5 sn sonra serbest birak.
# Mod degistirince olan sifirlamanin yalniz o hedef icin otomatik hali. Dost hedeflere DOKUNMAZ.
import sys, shutil, py_compile, tempfile, os
from pathlib import Path
p = Path(sys.argv[1]) / "backend/app/services/target_engagement_registry.py"
m = p.read_text(encoding="utf-8")
if "_y18_takili_temizle" in m:
    raise SystemExit("Y18 zaten kurulu.")
eski = "        self._prune_candidates(observed_pairs, timestamp)\n        self._update_pending_states(confirmations, timestamp)\n"
if m.count(eski) != 1:
    raise SystemExit("HATA: baglanti noktasi bulunamadi, DOSYA DEGISMEDI")
m = m.replace(eski,
"        if str(target_policy or \"\").upper() == \"BALLOON_AIRCRAFT\":\n"
"            self._y18_takili_temizle(fresh_tracks, timestamp)\n" + eski)
eski2 = "    def status(self) -> TargetRegistryStatus:\n"
if m.count(eski2) != 1:
    raise SystemExit("HATA: status bulunamadi, DOSYA DEGISMEDI")
m = m.replace(eski2,
"    Y18_TAKILI_S = 1.5\n"
"\n"
"    def _y18_takili_temizle(self, fresh_tracks, timestamp: float) -> None:\n"
"        \"\"\"Y18: govde izi degisince eski kimlik REENGAGE'de kilitli kalip balonu\n"
"        sonsuza dek bloke ediyordu (mod degisimi duzeltiyordu). Balon gorunurken\n"
"        1.5 sn REENGAGE kalan dost-olmayan govde hedefi silinir; ayni cift\n"
"        yeniden READY olabilir. Dost hedefler korunur.\"\"\"\n"
"        if not hasattr(self, \"_y18_since\"):\n"
"            self._y18_since = {}\n"
"        for tid, target in list(self._targets.items()):\n"
"            meta = self._target_meta.get(tid, {})\n"
"            if (\n"
"                meta.get(\"body_key\") is None\n"
"                or target.state != LogicalTargetState.REENGAGE\n"
"                or self._normalize_team(target.target_team) == \"friend\"\n"
"                or target.consumed\n"
"            ):\n"
"                self._y18_since.pop(tid, None)\n"
"                continue\n"
"            since = self._y18_since.setdefault(tid, timestamp)\n"
"            if timestamp - since < self.Y18_TAKILI_S or target.balloon_track_id not in fresh_tracks:\n"
"                continue\n"
"            self._targets.pop(tid, None)\n"
"            self._target_meta.pop(tid, None)\n"
"            for key, val in list(self._body_target_by_key.items()):\n"
"                if val == tid:\n"
"                    self._body_target_by_key.pop(key, None)\n"
"            self._y18_since.pop(tid, None)\n"
"\n" + eski2)
fd, yol = tempfile.mkstemp(suffix=".py"); os.close(fd)
Path(yol).write_text(m, encoding="utf-8"); py_compile.compile(yol, doraise=True); os.remove(yol)
yedek = p.with_name(p.name + ".k151_oncesi")
if not yedek.exists():
    shutil.copy2(p, yedek)
p.write_text(m, encoding="utf-8")
print("Y18 KURULDU, yedek:", yedek.name)
