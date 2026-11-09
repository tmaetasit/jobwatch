import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sites import JobBKK, JobsDB, JobThai, JobTopGun, Job  # noqa: E402
import watch  # noqa: E402

CFG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text(encoding="utf-8"))


def J(title, location="กรุงเทพมหานคร", extra="", company="ACME"):
    return Job(site="X", job_id="1", title=title, url="u", company=company, location=location, extra=extra)


# ---------------- ตัวกรองชื่อตำแหน่ง
def test_keyword_variants():
    for t in ["Business Analyst", "IT Business Analyst", "business analyst (Banking)",
              "Cybersecurity Analyst", "Cyber Security Officer", "CYBER SECURITY & GRC"]:
        assert watch.wanted(J(t), CFG), t


def test_non_matching_titles():
    for t in ["Data Analyst", "System Analyst", "Information Security", "Project Coordinator"]:
        assert not watch.wanted(J(t), CFG), t


def test_seniority_excluded():
    for t in ["Senior Business Analyst", "Sr. Business Analyst", "Business Analyst Lead",
              "Business Analyst Manager", "Head of Cyber Security", "Network and Cybersecurity Leader",
              "Principal Cyber Security Engineer", "ผู้จัดการ Business Analyst", "หัวหน้าทีม Cyber Security",
              "เจ้าหน้าที่ Cyber Security อาวุโส"]:
        assert not watch.wanted(J(t), CFG), t


def test_exclusion_is_word_based():
    # "Leadership" / "Headquarters" ไม่ควรโดนตัด
    assert watch.wanted(J("Business Analyst (Leadership Program)"), CFG)
    assert watch.wanted(J("Business Analyst - Headquarters"), CFG)


# ---------------- ตัวกรองพื้นที่
def test_locations():
    ok = ["กรุงเทพมหานคร", "เขตพญาไท กรุงเทพมหานคร", "Chatuchak, Bangkok", "Bangkok Metropolitan Region",
          "Mueang Samut Prakan, Samut Prakan", "อ.บางพลี จ.สมุทรปราการ", "BTS อารีย์", "กรุงเทพและปริมณฑล", ""]
    bad = ["Pak Kret, Nonthaburi", "อ.ปากเกร็ด จ.นนทบุรี", "Si Racha, Chon Buri", "เชียงใหม่"]
    for loc in ok:
        assert watch.wanted(J("Business Analyst", loc), CFG), loc
    for loc in bad:
        assert not watch.wanted(J("Business Analyst", loc), CFG), loc


def test_remote_passes_anywhere_and_flagged():
    j = J("Business Analyst", "เชียงใหม่", extra="Work from home 100%")
    assert watch.wanted(j, CFG)
    assert watch.is_remote(j, CFG["remote_words"])
    assert not watch.is_remote(J("Business Analyst"), CFG["remote_words"])


def test_fingerprint_merges_company_variants():
    a = J("Business Analyst", company="บริษัท เอบีซี จำกัด (มหาชน)")
    b = J("Business  Analyst", company="บริษัท เอบีซี จำกัด")
    assert watch.fingerprint(a) == watch.fingerprint(b)


def test_quiet_hours():
    q = CFG["quiet_hours"]
    at = lambda h: datetime(2026, 1, 1, h)
    assert watch.in_quiet_hours(at(23), q) and watch.in_quiet_hours(at(2), q)
    assert not watch.in_quiet_hours(at(7), q) and not watch.in_quiet_hours(at(22), q)


# ---------------- ตัวแยกข้อมูลแต่ละเว็บ (จำลองโครงสร้างที่เห็นจริง)
def test_parse_jobthai():
    html = """<a href="/th/company/job/1980598"><div><h2>Cyber Security &amp; GRC</h2>
      <span>K Stone Corporation Ltd.</span><span>K Stone Corporation Ltd.</span>
      <span>BTS อารีย์</span><span>25,000 - 35,000 บาท</span><span>Hybrid Work</span><span>9 พ.ย. 69</span></div></a>"""
    [j] = JobThai().parse(html)
    assert (j.job_id, j.title, j.company, j.location, j.salary) == (
        "1980598", "Cyber Security & GRC", "K Stone Corporation Ltd.", "BTS อารีย์", "25,000 - 35,000 บาท")
    assert j.url == "https://www.jobthai.com/th/company/job/1980598"


def test_parse_jobsdb():
    html = """<article data-job-id="95772589"><a data-automation="jobTitle" href="/job/95772589?x">Business Analyst</a>
      <a data-automation="jobCompany">First One Systems</a><a data-automation="jobLocation">Bang Kapi</a>,
      <a data-automation="jobLocation">Bangkok</a><span data-automation="jobSalary">฿40,000 – ฿50,000 per month</span></article>"""
    [j] = JobsDB().parse(html)
    assert j.title == "Business Analyst" and j.location == "Bang Kapi, Bangkok"
    assert j.url == "https://th.jobsdb.com/job/95772589" and "40,000" in j.salary


def test_parse_jobbkk():
    row1 = {"company_name": "บริษัท โค้ด เดอ แบร์ จำกัด", "position": "Business Analyst",
            "jobpost_id": 1395529, "company_id": 227014, "location": "กรุงเทพมหานคร (เขตห้วยขวาง)",
            "salary_amount": "$44", "job_format_type": "สัญญาจ้าง"}
    rsc = '44:{"range":"15,000 - 80,000 บาท"}\n38:' + json.dumps(row1, ensure_ascii=False) + "\n"
    html = "<script>self.__next_f.push([1," + json.dumps(rsc, ensure_ascii=False) + "])</script>"
    [j] = JobBKK().parse(html)
    assert j.url == "https://www.jobbkk.com/jobs/detail/227014/1395529"
    assert j.salary == "15,000 - 80,000 บาท" and j.company.startswith("บริษัท โค้ด")


def test_parse_jobtopgun():
    ld = {"@type": "ItemList", "itemListElement": [
        {"url": "https://www.jobtopgun.com/th/jobs/j16093-203", "name": "Business Analyst"},
        {"url": "https://www.jobtopgun.com/th/jobs/j2366-96", "name": "นักวิเคราะห์ธุรกิจ"}]}
    html = (f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>'
            '<div><a href="/th/jobs/j16093-203">Business Analyst</a><p>บริษัท ไทยเมจิฟาร์มาซิวติคัล จำกัด</p>'
            '<p>กรุงเทพมหานคร</p><p>งานประจำ</p><p>มีประสบการณ์ 1–3 ปี สนับสนุนงานด้าน IT และการพัฒนาระบบธุรกิจ ทำงานร่วมกับทีม</p></div>')
    jobs = {j.job_id: j for j in JobTopGun().parse(html)}
    assert set(jobs) == {"j16093-203", "j2366-96"}
    assert jobs["j16093-203"].location == "กรุงเทพมหานคร"
    assert jobs["j2366-96"].location == ""


def test_search_urls():
    assert JobsDB().search_url("Cyber Security").endswith("/Cyber-Security-jobs?sortmode=ListedDate")
    assert JobTopGun().search_url("Business Analyst").endswith("/th/jobs/Business-Analyst-jobs")
    assert "Business%20Analyst" in JobThai().search_url("Business Analyst")
    assert "/jobs/lists/1/" in JobBKK().search_url("Business Analyst")


# ---------------- ทั้งระบบ: รอบแรกไม่แจ้ง, รอบถัดไปแจ้งเฉพาะงานใหม่ที่ผ่านตัวกรอง, กันซ้ำข้ามเว็บ
def test_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(watch.time, "sleep", lambda s: None)
    sent = []
    monkeypatch.setattr(watch.Telegram, "send", lambda self, text, silent=False: sent.append(text) or True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")

    listings = {"JobThai": [Job("JobThai", "1", "Business Analyst", "u1", "บริษัท เอ จำกัด", "กรุงเทพมหานคร")]}
    for name in ("JobsDB", "JobBKK", "JobTopGun"):
        listings[name] = [Job(name, "0", "Other job", "u", "Z", "Bangkok")]

    class FakeSite:
        def __init__(self, name): self.name = name
        def search_url(self, kw): return self.name
        def parse(self, html): return list(listings[self.name])

    monkeypatch.setattr(watch, "ALL_SITES", {n.lower(): FakeSite(n) for n in listings})
    monkeypatch.setattr(watch, "fetch", lambda url, s: "")

    watch.run()
    assert [t for t in sent if "Business Analyst" in t] == []  # รอบแรกบันทึกเฉยๆ

    listings["JobThai"].append(Job("JobThai", "2", "Junior Cyber Security", "u2", "บริษัท บี จำกัด", "Bangkok"))
    listings["JobThai"].append(Job("JobThai", "3", "Senior Business Analyst", "u3", "C", "Bangkok"))
    listings["JobsDB"].append(Job("JobsDB", "9", "Junior Cyber Security", "u9", "บริษัท บี จำกัด (มหาชน)", "Bangkok"))
    sent.clear()
    watch.run()
    jobs_sent = [t for t in sent if "🆕" in t or "🏠" in t]
    assert len(jobs_sent) == 1 and "Junior Cyber Security" in jobs_sent[0]

    sent.clear()
    watch.run()
    assert not [t for t in sent if "🆕" in t]  # ไม่แจ้งซ้ำ


def test_failure_alert_after_three(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(watch.time, "sleep", lambda s: None)
    sent = []
    monkeypatch.setattr(watch.Telegram, "send", lambda self, text, silent=False: sent.append(text) or True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")

    class Broken:
        name = "JobsDB"
        def search_url(self, kw): return "x"
        def parse(self, html): return []

    monkeypatch.setattr(watch, "ALL_SITES", {"jobsdb": Broken()})
    monkeypatch.setattr(watch, "fetch", lambda url, s: "")
    for _ in range(2):
        watch.run()
    assert not [t for t in sent if "⚠️" in t]
    watch.run()
    assert len([t for t in sent if "⚠️" in t]) == 1
    watch.run()
    assert len([t for t in sent if "⚠️" in t]) == 1  # ไม่เตือนซ้ำทุกรอบ
