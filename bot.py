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

from make_reel import UA, norm  # stdlib-only import, safe before pip install

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
STATE = Path("state/offset.txt")
WORK = Path("work")
OUT = Path("out")
MAX_TG_BYTES = 48 * 1024 * 1024

HELP = (
    "سلام! متن ریلز رو برام بفرست.\n\n"
    "• من متنت رو اصلاح و کامل می‌کنم و نسخه‌ی اصلاح‌شده رو با دو دکمه برات می‌فرستم.\n"
    "• با دکمه انتخاب می‌کنی ویدیو از «نسخه‌ی اصلاح‌شده» ساخته بشه یا از «نسخه‌ی اصلی» خودت.\n"
    "• اگه خواستی ساختار رو خودت مشخص کنی: خط اول با # تیتر کاور باشه و هر خط بعدی یک صحنه (عنوان :: متن).\n\n"
    "ربات هر چند دقیقه پیام‌ها رو چک می‌کنه، پس جواب‌ها فوری نیست."
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


def say(text, **extra):
    for i in range(0, len(text), 3800):
        try:
            api("sendMessage", chat_id=CHAT_ID, text=text[i:i + 3800], **(extra if i == 0 else {}))
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



# ---------------------------------------------------------------- AI editor
EXAMPLE_IN = ("پیگیری حضوری یعنی دیدار با ادم ها و حرف زدن باهاشون. اول گوش بده بعد ثبت نام رو توضیح بده "
              "و بعدش تیم سازی رو شروع کن و ولشون نکن")
EXAMPLE_OUT = (
    "# پیگیری حضوری؛ رابطه‌ای که می‌ماند\n"
    "دیدار حضوری :: پیگیری واقعی با یک دیدار حضوری شروع می‌شود. یک گفتگوی صمیمی، فرصتی برای شنیدن حرف‌های طرف مقابل است.\n"
    "گوش دادن :: اول بپرس و با دقت گوش بده. هدف‌ها و نگرانی‌های او را بشناس تا احساس کند دیده شده است.\n"
    "ثبت‌نام :: وقتی آماده بود، مسیر ثبت‌نام را ساده و روشن توضیح بده. اگر هنوز آماده نبود، یک زمان مشخص برای دیدار بعدی بگذار.\n"
    "تیم‌سازی :: ثبت‌نام پایان کار نیست، شروع تیم‌سازی است. همان روز اولین قدم‌ها را با او بنویس و جلسه‌ی بعدی را هماهنگ کن.\n"
    "ادامه رابطه :: بعد از ثبت‌نام او را رها نکن. یک پیام کوتاه یا یک دیدار دوباره نشان می‌دهد که تنها نیست.\n"
    "جمع‌بندی :: پیگیری حضوری یعنی اعتماد، گوش دادن و همراهی. اگر این ویدیو به کارت آمد، ذخیره‌اش کن و برای هم‌تیمی‌ات بفرست."
)

EDITOR_PROMPT = (
    "ROLE: You are a senior Persian script editor for short-form video (Instagram Reels) in the field of "
    "network-marketing education and team leadership. You edit like a professional copy editor and a "
    "spoken-word coach.\n\n"
    "GOAL: Take the author's rough text and return a polished script whose meaning reaches the audience "
    "exactly as the author intended: clear, warm, empathetic and easy to follow when heard only once.\n\n"
    "HARD RULES\n"
    "1. Preserve the author's ideas, their order and their intent. Never invent new claims, statistics, "
    "stories, promises or advice that the author did not state or clearly imply.\n"
    "2. If a sentence is incomplete or unclear, complete it using only what the surrounding text clearly implies.\n"
    "3. Never add income claims, guarantees, earnings, get-rich language, pressure tactics or hard selling. "
    "The theme is in-person follow-up, registration (ثبت‌نام) and team building built on trust.\n"
    "4. Fix all spelling, grammar, punctuation and half-space (نیم‌فاصله) errors.\n"
    "5. Write simple, correct, natural Persian that a male narrator can read aloud smoothly: standard "
    "spelling (not slang spellings), short sentences of about 8 to 16 words, one idea per sentence, and "
    "punctuation exactly where a speaker would pause.\n"
    "6. Address the viewer directly (تو), in a supportive, never preachy tone.\n"
    "7. Total length: 90 to 140 words.\n\n"
    "STRUCTURE (exact)\n"
    "Line 1: '# ' followed by a hook title of at most 8 words that makes a viewer stop scrolling (a relatable "
    "feeling, a common mistake or a promise of clarity).\n"
    "Next 4 to 6 lines: one scene per line in the form 'title :: two or three sentences'. Each title is 1 to 3 words.\n"
    "The last scene is a warm wrap-up that ends with one gentle call to action (save, share with a teammate "
    "or comment), phrased naturally.\n"
    "Output ONLY the script: no explanations, no markdown, no bold, no quotes, no numbering.\n\n"
    "EXAMPLE\n"
    "AUTHOR TEXT:\n" + EXAMPLE_IN + "\n"
    "SCRIPT:\n" + EXAMPLE_OUT
)

REVIEW_PROMPT = (
    "You are the final quality reviewer of a Persian Reels script. You receive the AUTHOR TEXT and an edited "
    "DRAFT. Check: (1) every idea of the author is preserved, and nothing new or promotional was added; "
    "(2) all sentences are complete, fluent, grammatically correct and natural to hear; (3) each scene has one "
    "clear idea and the order is logical; (4) the format is exact: first line '# hook', then lines of the form "
    "'title :: sentences'; (5) the total is 90 to 140 words. Fix any problem directly and return the improved "
    "script in the same format. If the draft is already good, return it unchanged. Output ONLY the script."
)

BANNED = ("درآمد", "میلیون", "ثروت", "تضمین", "پولدار", "فروش", "سود")


def llm(system, user, temperature=0.4):
    payload = json.dumps({"model": "openai", "temperature": temperature,
                          "messages": [{"role": "system", "content": system},
                                       {"role": "user", "content": user}]}).encode()
    for url in ("https://text.pollinations.ai/openai",
                "https://gen.pollinations.ai/v1/chat/completions"):
        try:
            req = urllib.request.Request(url, data=payload, headers={
                "Content-Type": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=75) as r:
                data = json.loads(r.read().decode())
            out = (data["choices"][0]["message"]["content"] or "").strip()
            if out:
                return out
        except Exception as e:
            print("editor service failed:", url.split("/")[2], type(e).__name__)
    return None


def clean_script(out):
    if not out:
        return None
    out = out.replace("```", "").replace("**", "").strip()
    lines = out.splitlines()
    for i, l in enumerate(lines):
        if l.strip().startswith("#"):
            return "\n".join(x.rstrip() for x in lines[i:] if x.strip())
    return None


def valid_script(s):
    lines = [l.strip() for l in s.splitlines() if l.strip()]
    if not (4 <= len(lines) <= 9) or not lines[0].startswith("#"):
        return False
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return False
    persian = sum("\u0600" <= c <= "\u06FF" for c in letters)
    return persian / len(letters) > 0.7 and 35 <= len(s.split()) <= 240


def rules_ok(out, original):
    o, n = norm(original), norm(out)
    return not any(w in n and w not in o for w in BANNED)


def improve(text):
    """Two passes: edit, then review. Returns the script or None."""
    for attempt in range(2):
        note = "" if attempt == 0 else (
            "\n\nIMPORTANT: your previous answer broke the format or the rules. "
            "Follow the structure and rules exactly.")
        draft = clean_script(llm(EDITOR_PROMPT + note, "AUTHOR TEXT:\n" + text))
        if draft and valid_script(draft) and rules_ok(draft, text):
            review = clean_script(llm(REVIEW_PROMPT, f"AUTHOR TEXT:\n{text}\n\nDRAFT:\n{draft}", 0.2))
            if review and valid_script(review) and rules_ok(review, text):
                return review + "\n"
            return draft + "\n"
    return None


# ---------------------------------------------------------------- poll
def read_offset():
    try:
        return int(STATE.read_text().strip())
    except Exception:
        return 0


BAR = "━━━━━━━━━━"


def save_job(update_id, script):
    WORK.mkdir(exist_ok=True)
    (WORK / f"job_{update_id}.txt").write_text(script, encoding="utf-8")


def on_text(m, update_id):
    text = (m.get("text") or "").strip()
    reply_to = {"reply_to_message_id": m["message_id"]} if m.get("message_id") else {}
    if not text:
        say("فقط پیام متنی رو می‌فهمم. متن ریلز رو بفرست.")
        return
    if text.startswith("/start") or text.startswith("/help"):
        say(HELP)
        return
    if text.startswith("/"):
        say("این دستور رو نمی‌شناسم. /help رو بزن.")
        return
    edited = improve(text)
    if edited:
        keyboard = {"inline_keyboard": [
            [{"text": "ساخت نسخه‌ی اصلاح‌شده ✨", "callback_data": "edit"}],
            [{"text": "ساخت نسخه‌ی اصلی 📝", "callback_data": "orig"}]]}
        say("✍️ متنت رو اصلاح و کامل کردم. بخونش و یکی از دکمه‌ها رو بزن:\n"
            f"{BAR}\n{edited.strip()}\n{BAR}",
            reply_markup=json.dumps(keyboard), **reply_to)
    else:
        keyboard = {"inline_keyboard": [[{"text": "ساخت نسخه‌ی اصلی 📝", "callback_data": "orig"}]]}
        say("ویرایش خودکار الان در دسترس نبود. اگه می‌خوای ویدیو از همین متن خودت ساخته بشه، دکمه رو بزن.",
            reply_markup=json.dumps(keyboard), **reply_to)


def on_callback(cq, update_id):
    msg = cq.get("message") or {}
    if str(msg.get("chat", {}).get("id")) != CHAT_ID:
        return 0
    try:
        api("answerCallbackQuery", callback_query_id=cq["id"], text="دریافت شد ✅")
    except Exception:
        pass
    data = cq.get("data")
    script = None
    if data == "edit":
        found = re.search(BAR + r"\n(.*?)\n" + BAR, msg.get("text") or "", re.S)
        script = found.group(1).strip() + "\n" if found else None
    elif data == "orig":
        original = (msg.get("reply_to_message") or {}).get("text")
        script = auto_format(original) if original else None
    if not script:
        say("نتونستم متن رو پیدا کنم. لطفاً متن رو دوباره برام بفرست.")
        return 0
    try:
        api("editMessageReplyMarkup", chat_id=CHAT_ID, message_id=msg["message_id"],
            reply_markup=json.dumps({"inline_keyboard": []}))
    except Exception:
        pass
    save_job(update_id, script)
    say("ساخت ویدیو شروع شد ✅ حدود ۵ تا ۱۰ دقیقه صبر کن.")
    return 1


def poll():
    if not TOKEN or not CHAT_ID:
        sys.exit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret.")
    offset = read_offset()
    res = api("getUpdates", offset=offset, timeout=0,
              allowed_updates=json.dumps(["message", "callback_query"]))
    new_offset, jobs = offset, 0
    for u in res.get("result", []):
        new_offset = max(new_offset, u["update_id"] + 1)
        if u.get("callback_query"):
            jobs += on_callback(u["callback_query"], u["update_id"])
            continue
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) == CHAT_ID:
            on_text(m, u["update_id"])
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
    fallbacks = len(re.findall(r"FALLBACK", log))
    note = f"\n\n⚠️ برای {fallbacks} تصویر، عکس هوش مصنوعی نیومد و پس‌زمینه ساده گذاشته شد." if fallbacks else ""
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
