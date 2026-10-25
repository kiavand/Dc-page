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

from make_reel import UA, norm, strip_marks  # stdlib-only import, safe before pip install

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

BASE_TAGS = ["#ریلز", "#رشد_فردی", "#آموزش"]
TAG_MAP = [
    (("نتورک",), ["#نتورک_مارکتینگ"]),
    (("پیگیری",), ["#پیگیری"]),
    (("تیمسازی", "تیم"), ["#تیم_سازی"]),
    (("رهبر", "لیدر"), ["#رهبری"]),
    (("انگیزه", "موفقیت", "هدف", "شکست"), ["#انگیزشی", "#موفقیت"]),
    (("داستان",), ["#داستان"]),
    (("کتاب",), ["#کتاب", "#کتابخوانی"]),
    (("اعتماد",), ["#اعتماد"]),
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


CAPTION_PROMPT = (
    "You write Persian Instagram captions for short videos. You receive the video script. Write: "
    "1) a first line (at most 12 words) that creates curiosity; 2) a conceptual summary in 2 to 3 "
    "sentences, in your own words, explaining the key idea and why it matters to the viewer. Do NOT copy "
    "or closely paraphrase sentences from the script; 3) one call-to-action line that fits the topic "
    "(save, share or comment); 4) a blank line, then 6 to 8 relevant Persian hashtags (use underscores "
    "inside multi-word tags, for example #رشد_فردی). Do not use diacritics. Use at most two emojis. "
    "Output only the caption."
)


def script_parts(job_text):
    hook, scenes = "", []
    for line in job_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#") and not hook:
            hook = line.lstrip("#").split("|")[0].strip()
        else:
            scenes.append(line.split("::", 1)[1].strip() if "::" in line else line)
    return strip_marks(hook), [strip_marks(x) for x in scenes]


def fallback_caption(job_text):
    hook, _ = script_parts(job_text)
    n = norm(job_text)
    tags = []
    for keys, hs in TAG_MAP:
        if any(k in n for k in keys):
            tags += [h for h in hs if h not in tags]
    tags = (tags + BASE_TAGS)[:8]
    return (f"{hook}\n\n📌 ذخیره کن و برای دوستت بفرست.\n"
            "💬 نظرت رو زیر همین ریلز بنویس 👇\n\n" + " ".join(tags))


def make_caption(job_text):
    hook, scenes = script_parts(job_text)
    plain = hook + "\n" + "\n".join(scenes)
    out = llm(CAPTION_PROMPT, plain, 0.6)
    if out:
        out = strip_marks(out.replace("```", "").replace("**", "").strip())
        letters = [c for c in out if c.isalpha()]
        persian = sum("\u0600" <= c <= "\u06FF" for c in letters)
        sentences = [x.strip() for sc in scenes for x in re.split(r"(?<=[\.؟\?!…])\s+", sc)
                     if len(x.strip()) >= 25]
        copied = any(x in out for x in sentences)
        if letters and persian / len(letters) > 0.6 and 80 <= len(out) <= 1800 and "#" in out and not copied:
            return out
    return fallback_caption(job_text)


# ---------------------------------------------------------------- AI editor
EXAMPLE_IN = ("ادمایی ک موفق میشن اولش خیلی شکست خوردن ولی ول نکردن هر روز یه قدم کوچیک برداشتن "
              "و صبر کردن")
EXAMPLE_OUT = (
    "# شکست، پایانِ راه نیست | انگیزشی\n"
    "شروعِ سخت :: همهٔ آدم‌هایِ موفق، در ابتدایِ مسیر بارها شکست خورده‌اند. امّا آن‌ها تسلیم نشدند.\n"
    "ادامه دادن :: هر روز فقط یک قدمِ کوچک بردار. پیروزی، نتیجهٔ تکرارِ قدم‌هایِ ساده است.\n"
    "باور :: اگر امروز زمین خوردی، یعنی در حالِ حرکت هستی. کسی که تلاش می‌کند، همیشه از دیروزِ خودش جلوتر است.\n"
    "صبر :: نتیجه‌ها همیشه سریع نمی‌آیند. گاهی ریشه‌ها زیرِ خاک رشد می‌کنند، پیش از آنکه درخت دیده شود.\n"
    "جمع‌بندی :: شکست، پایانِ راه نیست؛ دعوتی برایِ دوباره شروع کردن است. اگر این ویدیو به کارت آمد، ذخیره‌اش کن و برایِ دوستت بفرست."
)

EDITOR_PROMPT = (
    "ROLE: You are a senior Persian script editor for short-form video (Instagram Reels). The page covers "
    "any topic: motivation, success stories, storytelling, excerpts from books, education, personal "
    "development, business and more. You edit like a professional copy editor and a spoken-word coach.\n\n"
    "GOAL: Take the author's rough text and return a polished script whose meaning reaches the audience "
    "exactly as the author intended: clear, emotionally engaging and easy to follow when heard only once.\n\n"
    "HARD RULES\n"
    "1. Preserve the author's ideas, their order, their tone and their intent. Never invent facts, names, "
    "numbers, quotes, stories or promises that the author did not write.\n"
    "2. If the text is a story or an excerpt from a book, keep the author's own wording as much as possible: "
    "only fix mistakes, complete broken sentences and split it into scenes.\n"
    "3. If a sentence is incomplete or unclear, complete it using only what the surrounding text clearly implies.\n"
    "4. Do not add claims, guarantees or promotional language that the author did not write.\n"
    "5. Fix all spelling, grammar, punctuation and half-space (نیم‌فاصله) errors.\n"
    "6. Write natural Persian that a male narrator can read aloud smoothly: short sentences of about 8 to 18 "
    "words, one idea per sentence, punctuation exactly where a speaker would pause. Match the author's "
    "register; address the viewer as تو only if it suits the text.\n"
    "7. Length: 80 to 160 words in total. If the source is longer, condense it faithfully, keeping the "
    "main idea and the most important lines.\n"
    "8. PRONUNCIATION MARKS (very important): write the hook and every scene sentence with Persian "
    "diacritics so the narrator pronounces them correctly: mark every ezafe (kasra after a consonant, "
    "like پایانِ راه; «یِ» after ی or ا, like مسیرِ... and آدم‌هایِ; and the hamza sign after a silent ه, "
    "like همهٔ and نتیجهٔ), add tashdid (like امّا), and add short vowels on words that are easy to "
    "mispronounce or ambiguous (like مَرد and مُرد). Do not mark every letter. Do not put diacritics in "
    "scene titles.\n\n"
    "STRUCTURE (exact)\n"
    "Line 1: '# ' + a hook title of at most 8 words that makes a viewer stop scrolling, then ' | ' and a "
    "label of one or two words that names the type of the video (for example: داستان, انگیزشی, آموزشی, "
    "از یک کتاب, تجربه).\n"
    "Next 4 to 6 lines: one scene per line in the form 'title :: two or three sentences'. Titles are 1 to 3 words.\n"
    "When it fits the text (not for a book excerpt), the last scene is a short wrap-up with one gentle call "
    "to action such as save, share or comment.\n"
    "Output ONLY the script: no explanations, no markdown, no bold, no quotes, no numbering.\n\n"
    "EXAMPLE\n"
    "AUTHOR TEXT:\n" + EXAMPLE_IN + "\n"
    "SCRIPT:\n" + EXAMPLE_OUT
)

REVIEW_PROMPT = (
    "You are the final quality reviewer of a Persian Reels script. You receive the AUTHOR TEXT and an edited "
    "DRAFT. Check: (1) every idea of the author is preserved, in the author's tone, and nothing new "
    "(facts, numbers, quotes, promises) was invented; (2) all sentences are complete, fluent, "
    "grammatically correct and natural to hear; (3) each scene has one clear idea and the order is "
    "logical; (4) the hook and all sentences carry correct pronunciation marks: ezafe kasra, the hamza "
    "sign after silent ه, tashdid and short vowels on ambiguous words; titles have none; (5) the format "
    "is exact: first line '# hook | label', then lines 'title :: sentences'; (6) the total is 80 to 160 "
    "words. Fix any problem directly and return the improved script in the same format. If the draft is "
    "already good, return it unchanged. Output ONLY the script."
)


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
    return persian / len(letters) > 0.7 and 35 <= len(strip_marks(s).split()) <= 220


def _digits(s):
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return set(re.findall(r"[0-9]+", s.translate(table)))


def rules_ok(out, original):
    """The editor may not invent numbers (dates, statistics, amounts)."""
    return _digits(out) <= _digits(original)


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
