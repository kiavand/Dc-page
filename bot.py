"""
Telegram driver for the reel builder.

  python bot.py poll    -> read new Telegram messages (stdlib only, fast)
  python bot.py build   -> build a reel for every pending message and send it back

Only messages from TELEGRAM_CHAT_ID are accepted.
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from make_reel import norm  # stdlib-only import, safe before pip install

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
STATE = Path("state/offset.txt")
WORK = Path("work")
OUT = Path("out")
MAX_TG_BYTES = 48 * 1024 * 1024

HELP = (
    "سلام! متن ریلز رو برام بفرست.\n\n"
    "• خط اول = تیتر کاور\n"
    "• هر خط بعدی = یک صحنه (اگه خواستی اولش عنوان بنویس: عنوان :: متن)\n"
    "• اگه یک متن پشت‌سرهم بفرستی، خودم تیتر و صحنه‌ها رو جدا می‌کنم.\n\n"
    "ویدیو و کپشن رو بعد از حدود ۵ تا ۱۵ دقیقه برات می‌فرستم "
    "(ربات هر ۱۰ دقیقه پیام‌ها رو چک می‌کنه)."
)

BASE_TAGS = ["#نتورک_مارکتینگ", "#رشد_فردی", "#آموزش"]
TAG_MAP = [
    (("پیگیری",), ["#پیگیری", "#پیگیری_حضوری"]),
    (("ثبتنام", "عضویت"), ["#ثبت_نام"]),
    (("تیمسازی", "تیم"), ["#تیم_سازی"]),
    (("رهبر", "لیدر"), ["#رهبری"]),
    (("اعتماد",), ["#اعتماد"]),
    (("گوش",), ["#مهارت_گوش_دادن"]),
    (("جلسه", "دیدار", "حضوری"), ["#جلسه_حضوری"]),
    (("هدف", "رشد", "موفقیت"), ["#موفقیت"]),
]


# ---------------------------------------------------------------- telegram
def api(method, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def say(text):
    for i in range(0, len(text), 3800):
        try:
            api("sendMessage", chat_id=CHAT_ID, text=text[i:i + 3800])
        except Exception as e:
            print("sendMessage failed:", type(e).__name__)


def curl_send(method, field, path, extra=()):
    cmd = ["curl", "-sS", "--max-time", "400", "-F", f"chat_id={CHAT_ID}",
           "-F", f"{field}=@{path}", *extra, f"{API}/{method}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(r.stdout).get("ok", False)
    except Exception:
        return False


def set_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    print(f"{key}={value}")


def run_url():
    s = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    r = os.environ.get("GITHUB_REPOSITORY", "")
    i = os.environ.get("GITHUB_RUN_ID", "")
    return f"{s}/{r}/actions/runs/{i}" if r and i else ""


# ---------------------------------------------------------------- text
def auto_format(text):
    """Turn a plain message into the '# hook' + one-scene-per-line format."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if any(l.startswith("#") for l in lines):
        return "\n".join(lines) + "\n"
    if len(lines) >= 3:
        hook, scenes = lines[0], lines[1:]
    else:
        sentences = [s.strip() for s in
                     re.split(r"(?<=[\.؟\?!…])\s+", " ".join(lines)) if s.strip()]
        if not sentences:
            sentences = [" ".join(lines)]
        first = sentences[0]
        if len(first) <= 60:
            hook, rest = first, sentences[1:]
        else:
            hook, rest = " ".join(first.split()[:7]), sentences
        rest = rest or [first]
        scenes = [" ".join(rest[i:i + 2]) for i in range(0, len(rest), 2)]
    hook = hook.strip().rstrip(".،")
    return "# " + hook + "\n" + "\n".join(scenes) + "\n"


def make_caption(job_text):
    hook, scenes = "", []
    for line in job_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#") and not hook:
            hook = line.lstrip("#").strip()
        else:
            scenes.append(line.split("::", 1)[1].strip() if "::" in line else line)
    teaser = ""
    if scenes:
        sents = re.split(r"(?<=[\.؟\?!…])\s+", scenes[0])
        teaser = " ".join(sents[:2]).strip()
    n = norm(job_text)
    tags = []
    for keys, hs in TAG_MAP:
        if any(k in n for k in keys):
            tags += [h for h in hs if h not in tags]
    tags = (tags + BASE_TAGS)[:8]
    return (f"{hook}\n\n{teaser}\n\n"
            "📌 ذخیره کن و برای هم‌تیمی‌ات بفرست.\n"
            "💬 نظرت رو زیر همین ریلز بنویس 👇\n\n" + " ".join(tags))


# ---------------------------------------------------------------- poll
def read_offset():
    try:
        return int(STATE.read_text().strip())
    except Exception:
        return 0


def poll():
    if not TOKEN or not CHAT_ID:
        sys.exit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret.")
    offset = read_offset()
    res = api("getUpdates", offset=offset, timeout=0,
              allowed_updates=json.dumps(["message"]))
    new_offset, jobs = offset, 0
    WORK.mkdir(exist_ok=True)
    for u in res.get("result", []):
        new_offset = max(new_offset, u["update_id"] + 1)
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != CHAT_ID:
            continue
        text = (m.get("text") or "").strip()
        if not text:
            say("فقط پیام متنی رو می‌فهمم. متن ریلز رو بفرست.")
            continue
        if text.startswith("/start") or text.startswith("/help"):
            say(HELP)
            continue
        if text.startswith("/"):
            say("این دستور رو نمی‌شناسم. /help رو بزن.")
            continue
        (WORK / f"job_{u['update_id']}.txt").write_text(auto_format(text), encoding="utf-8")
        jobs += 1
        say("متن دریافت شد ✅ ساخت ویدیو شروع شد. حدود ۵ تا ۱۰ دقیقه صبر کن.")
    if new_offset != offset:
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(str(new_offset))
    set_output("jobs", jobs)
    set_output("changed", "true" if new_offset != offset else "false")


# ---------------------------------------------------------------- build
def shrink_if_needed(path):
    if path.stat().st_size <= MAX_TG_BYTES:
        return path
    small = path.with_name("reel_small.mp4")
    subprocess.run(["ffmpeg", "-y", "-i", str(path), "-c:v", "libx264", "-crf", "30",
                    "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k", str(small)],
                   capture_output=True)
    return small if small.exists() else path


def send_results(job, video, log):
    video = shrink_if_needed(video)
    ok = curl_send("sendVideo", "video", video,
                   ["-F", "supports_streaming=true", "-F", "width=1080", "-F", "height=1920"])
    if not ok:
        say("⚠️ ویدیو ساخته شد ولی ارسالش به تلگرام نشد. این لینک رو باز کن و از Artifacts دانلودش کن:\n" + run_url())
        return
    cover = video.parent / "cover.png"
    if cover.exists():
        curl_send("sendDocument", "document", cover)
    fallbacks = len(re.findall(r"FALLBACK", log))
    note = f"\n\n⚠️ برای {fallbacks} صحنه تصویر هوش مصنوعی نیومد و پس‌زمینه ساده گذاشته شد." if fallbacks else ""
    say("کپشن پیشنهادی (روش نگه دار و کپی کن):" + note)
    say(make_caption(job.read_text(encoding="utf-8")))


def build():
    for job in sorted(WORK.glob("job_*.txt")):
        out = OUT / job.stem / "reel.mp4"
        r = subprocess.run([sys.executable, "make_reel.py", str(job), str(out)],
                           capture_output=True, text=True)
        log = (r.stdout or "") + "\n" + (r.stderr or "")
        print(log[-3000:])
        if r.returncode != 0 or not out.exists():
            tail = "\n".join(log.strip().splitlines()[-15:])
            say("❌ ساخت ویدیو خطا داد.\n" + run_url() + "\n\n" + tail[-1500:])
            continue
        send_results(job, out, log)


if __name__ == "__main__":
    {"poll": poll, "build": build}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit("usage: bot.py poll|build"))()
