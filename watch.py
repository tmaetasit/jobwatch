"""ตัวเช็คงานใหม่ → แจ้งเตือน Telegram

รัน: python watch.py   (ต้องตั้ง env TELEGRAM_BOT_TOKEN และ TELEGRAM_CHAT_ID)
     python watch.py --dry-run   (ไม่ส่ง Telegram ไม่บันทึก state แค่พิมพ์ผล)
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

from sites import ALL_SITES, Job

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml",
}


# ================================================================ ตัวกรอง
def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def title_matches(title: str, keywords: list[str]) -> str | None:
    """คืน keyword ที่ตรง (ไม่สนตัวพิมพ์และช่องว่าง) หรือ None"""
    t = _squash(title)
    return next((k for k in keywords if _squash(k) in t), None)


def is_excluded(title: str, words: list[str]) -> bool:
    for w in words:
        if re.search(r"[a-z]", w, re.I):
            # คำภาษาอังกฤษ เทียบเป็นคำ: "Lead" ไม่ไปโดน "Leadership"... แต่ "Sr." โดน
            if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", title, re.I):
                return True
        elif w in title:
            return True
    return False


def is_remote(job: Job, words: list[str]) -> bool:
    text = f"{job.title} {job.location} {job.extra}".lower()
    return any(w.lower() in text for w in words)


def location_ok(job: Job, allow: list[str], remote_words: list[str]) -> bool:
    if is_remote(job, remote_words):
        return True
    loc = job.location.strip()
    if not loc:
        return True  # ไม่ระบุพื้นที่ → ส่งมาให้ดู
    low = loc.lower()
    return any(a.lower() in low for a in allow)


def wanted(job: Job, cfg: dict) -> bool:
    return (title_matches(job.title, cfg["keywords"]) is not None
            and not is_excluded(job.title, cfg["exclude_title_words"])
            and location_ok(job, cfg["location_allow"], cfg["remote_words"]))


_CO_NOISE = re.compile(
    r"บริษัท|จำกัด|\(มหาชน\)|มหาชน|public|company|limited|co\.?|ltd\.?|corporation|corp\.?|inc\.?|\(thailand\)|thailand|[^\w฀-๿]",
    re.I,
)


def fingerprint(job: Job) -> str:
    """ใช้รวมงานเดียวกันที่ลงหลายเว็บ"""
    return f"{_CO_NOISE.sub('', job.company.lower())}|{_squash(job.title)}"


# ================================================================ state
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def normalize_state(st: dict) -> dict:
    st.setdefault("seen", {})          # job key -> วันที่เห็นครั้งแรก
    st.setdefault("fingerprints", {})  # fingerprint -> วันที่แจ้ง
    st.setdefault("baselined", [])     # "site|keyword" ที่บันทึกรอบแรกแล้ว
    st.setdefault("failures", {})      # site -> จำนวนรอบที่พังติดกัน
    st.setdefault("down_alerted", {})  # site -> True ถ้าแจ้งว่าพังไปแล้ว
    st.setdefault("stats", {})         # วันที่ -> site -> {ok, fail, sent}
    st.setdefault("last_summary", "")
    return st


def prune(st: dict, today: datetime) -> None:
    cutoff = (today - timedelta(days=60)).date().isoformat()
    for k in ("seen", "fingerprints"):
        st[k] = {key: d for key, d in st[k].items() if d >= cutoff}
    keep = (today - timedelta(days=7)).date().isoformat()
    st["stats"] = {d: v for d, v in st["stats"].items() if d >= keep}


def save_state(st: dict) -> None:
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1, sort_keys=True),
                          encoding="utf-8")


# ================================================================ Telegram
class Telegram:
    def __init__(self, token: str, chat_id: str, dry_run: bool = False):
        self.token, self.chat_id, self.dry_run = token, chat_id, dry_run

    def send(self, text: str, silent: bool = False) -> bool:
        if self.dry_run:
            print(("[เงียบ] " if silent else "") + text + "\n" + "-" * 40)
            return True
        for attempt in range(3):
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                          "disable_notification": silent,
                          "link_preview_options": {"is_disabled": True}},
                    timeout=20,
                )
                if r.status_code == 429:  # ส่งถี่เกิน รอตามที่ Telegram บอก
                    time.sleep(r.json().get("parameters", {}).get("retry_after", 5) + 1)
                    continue
                if r.ok:
                    return True
                print(f"Telegram error {r.status_code}: {r.text[:200]}", file=sys.stderr)
            except requests.RequestException as e:
                print(f"Telegram error: {e}", file=sys.stderr)
            time.sleep(2)
        return False


def format_job(job: Job, remote: bool) -> str:
    e = html.escape
    lines = [("🏠 <b>REMOTE</b> · " if remote else "🆕 ") + f"<b>{e(job.title)}</b>"]
    if job.company:
        lines.append(f"🏢 {e(job.company)}")
    if job.location:
        lines.append(f"📍 {e(job.location)}")
    if job.salary:
        lines.append(f"💰 {e(job.salary)}")
    lines.append(f"🌐 {job.site} · <a href=\"{e(job.url)}\">เปิดประกาศ / สมัคร</a>")
    return "\n".join(lines)


def in_quiet_hours(now: datetime, q: dict) -> bool:
    s, e = q["start"], q["end"]
    return (now.hour >= s or now.hour < e) if s > e else (s <= now.hour < e)


# ================================================================ main
FETCH_DEADLINE = 40  # วินาที: ต่อ 1 หน้า ถ้าเกินนี้ถือว่าล้มเหลว (กันค้าง)


def _get_once(url: str, session: requests.Session) -> str:
    start = time.monotonic()
    with session.get(url, headers=HEADERS, timeout=(10, 20), stream=True) as r:
        r.raise_for_status()
        buf = bytearray()
        for chunk in r.iter_content(65536):
            buf.extend(chunk)
            if time.monotonic() - start > FETCH_DEADLINE:
                raise TimeoutError(f"โหลดหน้าเว็บนานเกิน {FETCH_DEADLINE} วินาที")
        enc = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
        return buf.decode(enc, errors="replace")


def fetch(url: str, session: requests.Session) -> str:
    try:
        return _get_once(url, session)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (401, 403, 429):
            raise  # โดนบล็อก ลองซ้ำก็ไม่ช่วย
        time.sleep(3)
    except (requests.RequestException, TimeoutError):
        time.sleep(3)
    return _get_once(url, session)


def run(dry_run: bool = False) -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg.get("timezone", "Asia/Bangkok"))
    now = datetime.now(tz)
    today = now.date().isoformat()
    tg = Telegram(os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                  os.environ.get("TELEGRAM_CHAT_ID", ""), dry_run)
    if not dry_run and not (tg.token and tg.chat_id):
        print("ยังไม่ได้ตั้ง TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID", file=sys.stderr)
        return 2

    st = normalize_state(load_state())
    silent = in_quiet_hours(now, cfg["quiet_hours"])
    session = requests.Session()
    day_stats = st["stats"].setdefault(today, {})
    sent_total = 0

    for site_key, site in ALL_SITES.items():
        if not cfg["sites"].get(site_key, True):
            continue
        s = day_stats.setdefault(site.name, {"ok": 0, "fail": 0, "sent": 0})
        site_failed = False
        for kw in cfg["keywords"]:
            url = site.search_url(kw)
            t0 = time.monotonic()
            try:
                jobs = site.parse(fetch(url, session))
                print(f"[{site.name}] {kw}: โหลด {time.monotonic() - t0:.1f} วินาที")
                if not jobs:
                    raise ValueError("แยกประกาศงานไม่ได้เลย (หน้าเว็บอาจเปลี่ยน หรือโดนบล็อก)")
            except Exception as e:  # noqa: BLE001
                print(f"[{site.name}] {kw}: ล้มเหลว — {e}", file=sys.stderr)
                site_failed = True
                continue
            finally:
                time.sleep(2)  # สุภาพกับเว็บ

            base_key = f"{site_key}|{_squash(kw)}"
            # dry-run: แสดงงานที่ผ่านตัวกรองทั้งหมด (ไม่ข้ามรอบแรก)
            first_time = base_key not in st["baselined"] and not dry_run
            new = [j for j in jobs if j.key not in st["seen"]]
            print(f"[{site.name}] {kw}: เจอ {len(jobs)} งาน, ใหม่ {len(new)}"
                  + (" (รอบแรก บันทึกเฉยๆ)" if first_time else ""))

            for job in new:
                if first_time or not wanted(job, cfg):
                    st["seen"][job.key] = today
                    continue
                fp = fingerprint(job)
                if fp in st["fingerprints"]:  # ลงซ้ำจากเว็บอื่นแล้ว
                    st["seen"][job.key] = today
                    continue
                if tg.send(format_job(job, is_remote(job, cfg["remote_words"])), silent):
                    st["seen"][job.key] = today
                    st["fingerprints"][fp] = today
                    s["sent"] += 1
                    sent_total += 1
                # ส่งไม่สำเร็จ → ไม่บันทึก รอบหน้าจะลองส่งใหม่
            if first_time:
                st["baselined"].append(base_key)

        # ----- สุขภาพของเว็บ
        if site_failed:
            s["fail"] += 1
            st["failures"][site.name] = st["failures"].get(site.name, 0) + 1
            n = st["failures"][site.name]
            if n >= cfg["failure_alert_after"] and not st["down_alerted"].get(site.name):
                if tg.send(f"⚠️ <b>{site.name}</b> ดึงข้อมูลไม่ได้ {n} รอบติดกัน\n"
                           f"ระหว่างนี้งานจากเว็บนี้จะไม่เข้า — ลองเปิดดูเองหรือตั้ง Job Alert ของเว็บไว้ก่อน"):
                    st["down_alerted"][site.name] = True
        else:
            s["ok"] += 1
            if st["down_alerted"].get(site.name):
                tg.send(f"✅ <b>{site.name}</b> กลับมาดึงข้อมูลได้แล้ว", silent)
            st["failures"][site.name] = 0
            st["down_alerted"][site.name] = False

    # ----- สรุปรายวัน
    yday = (now - timedelta(days=1)).date().isoformat()
    ys = st["stats"].get(yday)
    if now.hour >= cfg["daily_summary_hour"] and st["last_summary"] != today and not ys:
        st["last_summary"] = today  # วันแรกที่เพิ่งเริ่มใช้ ยังไม่มีข้อมูลเมื่อวาน
    if now.hour >= cfg["daily_summary_hour"] and st["last_summary"] != today:
        lines = [f"📊 <b>สรุปตัวเช็คงาน เมื่อวาน ({yday})</b>"]
        for site_key, site in ALL_SITES.items():
            if not cfg["sites"].get(site_key, True):
                continue
            v = ys.get(site.name, {"ok": 0, "fail": 0, "sent": 0})
            total = v["ok"] + v["fail"]
            icon = "✅" if total and v["fail"] == 0 else ("⚠️" if v["ok"] else "❌")
            lines.append(f"{icon} {site.name}: สำเร็จ {v['ok']}/{total} รอบ · แจ้งงาน {v['sent']}")
        if tg.send("\n".join(lines), silent=False):
            st["last_summary"] = today

    print(f"ส่งแจ้งเตือนงานใหม่ {sent_total} รายการ")
    prune(st, now)
    if not dry_run:
        save_state(st)
    return 0


if __name__ == "__main__":
    sys.exit(run(dry_run="--dry-run" in sys.argv))
