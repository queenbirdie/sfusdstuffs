"""
Refresh SFUSD elementary/TK-8 tour times -> site/data/tours.json (+ tours.csv)

Run locally:  pip install -r requirements.txt && python scripts/refresh_tours.py
In GitHub:    runs on a schedule via .github/workflows/refresh.yml

Tours that drop off a school's form are KEPT and marked "full" (they filled, or
occasionally were cancelled). If a time reappears, it flips back to "open".
"""
import csv, json, re, sys, time
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TOURS_PAGE = "https://www.sfusd.edu/schools/enroll/discover/sfusd-school-tours"
DATA_DIR = Path(__file__).resolve().parent.parent / "site" / "data"
JSON_OUT, CSV_OUT = DATA_DIR / "tours.json", DATA_DIR / "tours.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (SF school tour tracker)"}
MIN_SCHOOLS = 30  # sanity check: if the SFUSD page parses to fewer, abort without writing
TZ = ZoneInfo("America/Los_Angeles")
SLOT_RE = re.compile(r"^\w{3,5},?\s+(\w{3})\w*\.?\s+(\d{1,2}),\s+(\d{4})\s*@\s*(.+)$")
TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?|a|p)?(?![a-z])", re.I)


def get_schools():
    html = requests.get(TOURS_PAGE, headers=HEADERS, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    heading = next((h for h in soup.find_all(["h2", "h3", "h4", "strong", "p"])
                    if "Elementary and TK-8" in h.get_text()), None)
    if heading is None:
        raise RuntimeError("Couldn't find the 'Elementary and TK-8' section on the SFUSD page")
    schools = []
    for tr in heading.find_next("table").find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        a = tds[2].find("a")
        note = tds[2].get_text(" ", strip=True)
        if a:
            note = note.replace(a.get_text(" ", strip=True), "", 1).strip(" .")
        schools.append({"name": tds[0].get_text(" ", strip=True),
                        "grades": tds[1].get_text(" ", strip=True),
                        "link": a["href"].strip() if a else "", "note": note})
    return schools


def get_form_options(url):
    """Read the 'Tour Time' question options from the form's embedded FB_PUBLIC_LOAD_DATA_ JSON."""
    html = requests.get(url, headers=HEADERS, timeout=30).text
    m = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\]);\s*</script>", html, re.S)
    if not m:
        raise ValueError("form data not found (closed, moved, or sign-in required)")
    data = json.loads(m.group(1))
    for item in data[1][1] or []:
        if "tour time" in (item[1] or "").lower() and item[4]:
            return [o[0] for o in item[4][0][1] if o and o[0]]
    return []


def _mer(x):
    return ("pm" if x.lower().startswith("p") else "am") if x else ""


def _guess(h):
    """No am/pm given anywhere: school tours run 7am-6pm."""
    return "am" if 7 <= h <= 11 else "pm"


def parse_times(text):
    """Parse every time range in a tour label.
    Returns (ranges, label): ranges = [('HH:MM','HH:MM'), ...] in 24h; label = tidy display text.
    Handles '8:30-9:30am', '10:00-11:00 am', '8:00-9:00a', '9:00-9:45', '12:45-1:45pm',
    '11am-12pm', '9:00-10:00, 11:00-12:00'. Returns ([], text) if it can't parse."""
    toks = [(int(h), int(m or 0), _mer(ap)) for h, m, ap in TIME_RE.findall(text)]
    if len(toks) < 2 or len(toks) % 2 or any(not (1 <= h <= 12) or m > 59 for h, m, _ in toks):
        return [], text
    ranges, parts = [], []
    for (h1, m1, a1), (h2, m2, a2) in zip(toks[::2], toks[1::2]):
        if not a2:
            if a1:
                a2 = a1 if (h2 > h1 and h2 != 12) or h1 == h2 else ("pm" if a1 == "am" else a1)
            else:
                a2 = _guess(h2)
        if not a1:
            if h1 == 12:
                a1 = "pm"
            elif a2 == "pm" and (h1 > h2 or h2 == 12):
                a1 = "am"
            else:
                a1 = a2
        to24 = lambda h, ap: (h % 12) + (12 if ap == "pm" else 0)
        s24, e24 = to24(h1, a1) * 60 + m1, to24(h2, a2) * 60 + m2
        if not (0 < e24 - s24 <= 240):
            return [], text
        ranges.append((f"{s24 // 60:02d}:{s24 % 60:02d}", f"{e24 // 60:02d}:{e24 % 60:02d}"))
        hm = lambda h, m: f"{h}:{m:02d}"
        parts.append(f"{hm(h1, m1)}\u2013{hm(h2, m2)} {a2.upper()}" if a1 == a2
                     else f"{hm(h1, m1)} {a1.upper()}\u2013{hm(h2, m2)} {a2.upper()}")
    extra = " ".join(re.findall(r"\([^)]*\)", text))
    return ranges, ", ".join(parts) + (f" {extra}" if extra else "")


def parse_slot(text):
    m = SLOT_RE.match(text.strip())
    if not m:
        return None
    mon, day, yr, t = m.groups()
    try:
        d = datetime.strptime(f"{mon[:3]} {day} {yr}", "%b %d %Y").date()
    except ValueError:
        return None
    return d, t.strip()


def tour_id(school, d, t):
    return "|".join([school, d, re.sub(r"\s+", "", t.lower())])


def main():
    now = datetime.now(TZ)
    today = now.date().isoformat()
    prev = json.loads(JSON_OUT.read_text()) if JSON_OUT.exists() else {}
    prev_tours = {t["id"]: t for t in prev.get("tours", [])}

    schools = get_schools()
    if len(schools) < MIN_SCHOOLS:
        sys.exit(f"Only parsed {len(schools)} schools from SFUSD page - layout may have changed. Not writing.")

    tours, out_schools, errors = {}, [], []
    for s in schools:
        status, opts, ok = "", [], True
        if not s["link"]:
            status = "No sign-up form posted yet"
        else:
            try:
                opts = get_form_options(s["link"])
            except Exception as e:
                ok, status = False, f"Couldn't read form ({e})"
                errors.append(f"{s['name']}: {e}")
            time.sleep(1)  # be polite

        seen, unparsed = set(), []
        for o in opts:
            low = o.lower()
            if low.startswith("i'd like to cancel"):
                continue
            if low.startswith("sign up here"):
                status = status or "Full: interest list only"
                continue
            p = parse_slot(o)
            if not p:
                unparsed.append(o)
                continue
            d, t = p
            tid = tour_id(s["name"], d.isoformat(), t)
            if tid in seen:
                continue
            seen.add(tid)
            ranges, label = parse_times(t)
            rng = ranges[0] if ranges else None
            old = prev_tours.get(tid, {})
            tours[tid] = {"id": tid, "school": s["name"], "grades": s["grades"], "link": s["link"],
                          "date": d.isoformat(), "time": label, "raw_time": t,
                          "start": rng[0] if rng else None, "end": rng[1] if rng else None,
                          "status": "open", "first_seen": old.get("first_seen", today),
                          "last_seen": today, "full_since": None}

        # Keep tours that fell off this school's form -> mark full.
        for tid, old in prev_tours.items():
            if old["school"] != s["name"] or tid in tours:
                continue
            if not ok:  # form unreadable this run: carry forward untouched
                tours[tid] = old
            else:
                tours[tid] = {**old, "status": "full", "full_since": old.get("full_since") or today}

        if unparsed:
            status = (status + ". " if status else "") + "Unrecognized options: " + "; ".join(unparsed)
        out_schools.append({**s, "status": status})
        print(f"{s['name']}: {len(seen)} open {status}")

    # Schools that vanished from the SFUSD page: keep their history as-is.
    names = {s["name"] for s in schools}
    for tid, old in prev_tours.items():
        if old["school"] not in names:
            tours[tid] = old

    ordered = sorted(tours.values(), key=lambda t: (t["date"], t["start"] or "99", t["school"]))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps({
        "updated": now.isoformat(timespec="minutes"),
        "first_run": prev.get("first_run", today),
        "source": TOURS_PAGE,
        "schools": out_schools,
        "tours": ordered,
    }, indent=1))
    with CSV_OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Day", "Time", "School", "Grades", "Status", "Full since", "First seen", "Sign-up form"])
        for t in ordered:
            d = date.fromisoformat(t["date"])
            w.writerow([t["date"], d.strftime("%a"), t["time"], t["school"], t["grades"],
                        t["status"], t["full_since"] or "", t["first_seen"], t["link"]])

    n_open = sum(t["status"] == "open" for t in ordered)
    print(f"\n{n_open} open | {len(ordered) - n_open} full | {len(errors)} form errors")
    for e in errors:
        print("  !", e)


if __name__ == "__main__":
    main()
