"""ดึงและแยกข้อมูลประกาศงานจากแต่ละเว็บ

แต่ละเว็บมีฟังก์ชัน search_url(keyword) และ parse(html) -> list[Job]
โครงสร้างหน้าเว็บตรวจสอบจริงเมื่อ พ.ย. 2026 — ถ้าเว็บเปลี่ยนหน้าตา ให้แก้ที่ไฟล์นี้
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup


@dataclass
class Job:
    site: str
    job_id: str
    title: str
    url: str
    company: str = ""
    location: str = ""
    salary: str = ""
    extra: str = ""  # ข้อความอื่นในการ์ด ใช้เช็ค Remote
    keyword: str = field(default="", compare=False)

    @property
    def key(self) -> str:
        return f"{self.site}:{self.job_id}"


def _leaf_texts(node) -> list[str]:
    """ข้อความในการ์ดเรียงตามลำดับ ตัดตัวที่ซ้ำติดกันออก"""
    out: list[str] = []
    for s in node.stripped_strings:
        s = " ".join(s.split())
        if s and (not out or out[-1] != s):
            out.append(s)
    return out


def _dashed(keyword: str) -> str:
    return "-".join(keyword.split())


_SALARY_RE = re.compile(r"บาท|฿|ตามตกลง|ตามประสบการณ์|ไม่ระบุ|negotiable", re.I)


# ---------------------------------------------------------------- JobThai
class JobThai:
    name = "JobThai"
    key = "jobthai"
    base = "https://www.jobthai.com"

    def search_url(self, keyword: str) -> str:
        return f"{self.base}/th/jobs?keyword={quote(keyword)}"

    def parse(self, html: str) -> list[Job]:
        soup = BeautifulSoup(html, "html.parser")
        jobs: dict[str, Job] = {}
        for a in soup.select('a[href*="/job/"]'):
            m = re.search(r"/job/(\d+)", a.get("href", ""))
            if not m or m.group(1) in jobs:
                continue
            parts = _leaf_texts(a)
            if not parts:
                continue
            # การ์ด: ตำแหน่ง, บริษัท, (บริษัทซ้ำ), สถานที่, เงินเดือน, ..., วันที่
            title = parts[0]
            rest = parts[1:]
            company = rest[0] if rest else ""
            rest = [p for p in rest[1:] if p != company]
            location = rest[0] if rest and not _SALARY_RE.search(rest[0]) else ""
            salary = next((p for p in rest if _SALARY_RE.search(p)), "")
            jobs[m.group(1)] = Job(
                site=self.name, job_id=m.group(1), title=title,
                url=urljoin(self.base, a["href"]), company=company,
                location=location, salary=salary, extra=" | ".join(rest),
            )
        return list(jobs.values())


# ---------------------------------------------------------------- JobsDB
class JobsDB:
    name = "JobsDB"
    key = "jobsdb"
    base = "https://th.jobsdb.com"

    def search_url(self, keyword: str) -> str:
        # sortmode=ListedDate → เรียงงานใหม่ล่าสุดก่อน
        return f"{self.base}/{quote(_dashed(keyword))}-jobs?sortmode=ListedDate"

    def parse(self, html: str) -> list[Job]:
        soup = BeautifulSoup(html, "html.parser")
        jobs = []
        for card in soup.select("article[data-job-id]"):
            jid = card.get("data-job-id", "").strip()

            def txt(sel):
                el = card.select_one(sel)
                return " ".join(el.get_text(" ", strip=True).split()) if el else ""

            title = txt('[data-automation="jobTitle"]')
            if not jid or not title:
                continue
            location = ", ".join(
                e.get_text(" ", strip=True) for e in card.select('[data-automation="jobLocation"]')
            )
            jobs.append(Job(
                site=self.name, job_id=jid, title=title,
                url=f"{self.base}/job/{jid}",
                company=txt('[data-automation="jobCompany"]'),
                location=location, salary=txt('[data-automation="jobSalary"]'),
                extra=card.get_text(" ", strip=True)[:500],
            ))
        return jobs


# ---------------------------------------------------------------- JobBKK
class JobBKK:
    """JobBKK ใช้ Next.js (App Router): ข้อมูลงานอยู่ใน self.__next_f.push(...)"""
    name = "JobBKK"
    key = "jobbkk"
    base = "https://www.jobbkk.com"

    _PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', re.S)
    _ROW_RE = re.compile(r"^([0-9a-f]+):(\{.*\})$")

    def search_url(self, keyword: str) -> str:
        path = f"หางาน,{keyword},ทุกจังหวัด,ทั้งหมด.html"
        return f"{self.base}/jobs/lists/1/{quote(path, safe=',.')}"

    def parse(self, html: str) -> list[Job]:
        chunks = []
        for m in self._PUSH_RE.finditer(html):
            try:
                chunks.append(json.loads(f'"{m.group(1)}"'))
            except json.JSONDecodeError:
                continue
        rows: dict[str, dict] = {}
        for line in "".join(chunks).split("\n"):
            m = self._ROW_RE.match(line.strip())
            if not m:
                continue
            try:
                rows[m.group(1)] = json.loads(m.group(2))
            except json.JSONDecodeError:
                continue

        def resolve(v):
            if isinstance(v, str) and v.startswith("$") and v[1:] in rows:
                return rows[v[1:]]
            return v

        jobs: dict[str, Job] = {}
        for row in rows.values():
            if "jobpost_id" not in row or "position" not in row:
                continue
            jid = str(row["jobpost_id"])
            sal = resolve(row.get("salary_amount"))
            salary = sal.get("range", "") if isinstance(sal, dict) else ""
            fmt = row.get("job_format_type") or ""
            jobs[jid] = Job(
                site=self.name, job_id=jid, title=row["position"].strip(),
                url=f"{self.base}/jobs/detail/{row.get('company_id', '')}/{jid}",
                company=(row.get("company_name") or "").strip(),
                location=row.get("location") or row.get("province_name") or "",
                salary=salary, extra=fmt,
            )
        return list(jobs.values())


# ---------------------------------------------------------------- JobTopGun
class JobTopGun:
    """รายการงาน 30 ตัวอยู่ใน JSON-LD (ItemList) ส่วนบริษัท/สถานที่ มีใน HTML แค่บางการ์ด"""
    name = "JobTopGun"
    key = "jobtopgun"
    base = "https://www.jobtopgun.com"

    def search_url(self, keyword: str) -> str:
        return f"{self.base}/th/jobs/{quote(_dashed(keyword))}-jobs"

    def parse(self, html: str) -> list[Job]:
        soup = BeautifulSoup(html, "html.parser")
        jobs: dict[str, Job] = {}

        for s in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(s.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("@type") != "ItemList":
                continue
            for item in data.get("itemListElement", []):
                url = item.get("url", "")
                m = re.search(r"/jobs/(j\d+-\d+)", url)
                if m and item.get("name"):
                    jobs[m.group(1)] = Job(site=self.name, job_id=m.group(1),
                                           title=item["name"].strip(), url=url)

        # เติมบริษัท/สถานที่จากการ์ดที่มีใน HTML
        for a in soup.select('a[href*="/jobs/j"]'):
            m = re.search(r"/jobs/(j\d+-\d+)", a.get("href", ""))
            if not m:
                continue
            jid = m.group(1)
            card = a
            for _ in range(8):
                if card.parent is None:
                    break
                card = card.parent
                if len(card.get_text(strip=True)) > 150:
                    break
            parts = _leaf_texts(card)
            if len(parts) < 3:
                continue
            job = jobs.get(jid) or Job(site=self.name, job_id=jid, title=parts[0],
                                       url=urljoin(self.base, a["href"]))
            if not job.company:
                job.company = parts[1]
                job.location = parts[2]
                job.salary = next((p for p in parts[3:] if _SALARY_RE.search(p)), "")
                job.extra = " | ".join(parts[3:])
            jobs[jid] = job
        return list(jobs.values())


ALL_SITES = {s.key: s for s in (JobThai(), JobsDB(), JobBKK(), JobTopGun())}
