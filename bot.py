"""Automatic Telegram-to-Reels driver (v6, matches make_reel.py v6).

Usage:
  python bot.py poll   # receive text, edit it, queue job
  python bot.py build  # render queued jobs and send them to Telegram

Required environment variables: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Optional:
  HOOK_BOOST=0     keep the author's first sentence as the hook (default 1: an AI-written
                   punchy hook is added when the author did not write a "# hook" line)

Pipeline per message
  1. editor prompt   -> spelling, punctuation, pronunciation marks (content is never shortened)
  2. review prompt   -> second pass that restores anything the first pass dropped
  3. hook booster    -> short, curiosity-driven hook (headline spoken first + shown on the cover)
  4. local polish    -> Persian typography, closing punctuation for natural pauses
  5. make_reel.py    -> numbers/Latin terms are converted for the voice there (۹۰٪ -> «نود درصد»)
  6. Telegram        -> video + cover image + ready-to-paste caption
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from make_reel import UA, fix_persian, norm, strip_marks

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"

STATE = Path("state/offset.txt")
HISTORY = Path("state/history")        # icon / cover history shared by all videos
WORK = Path("work")
FAILED = WORK / "failed"
OUT = Path("out")

MAX_TG_BYTES = 48 * 1024 * 1024
MAX_TRIES = 3                          # a broken job is moved to work/failed after this many runs
HOOK_BOOST = os.environ.get("HOOK_BOOST", "1").strip() != "0"

BASE_TAGS = ["#ریلز", "#رشد_فردی", "#آموزش"]

TAG_MAP = [
    (("نتورک", "شبکه سازی", "ارتباطات"), ["#نتورک_مارکتینگ", "#ارتباط_موثر"]),
    (("پیگیری",), ["#پیگیری"]),
    (("تیمسازی", "تیم"), ["#تیم_سازی", "#کار_تیمی"]),
    (("رهبر", "لیدر", "مدیریت"), ["#رهبری", "#مدیریت"]),
    (("انگیزه", "موفقیت", "هدف", "شکست", "اشتباه"), ["#انگیزشی", "#موفقیت", "#انگیزه"]),
    (("داستان",), ["#داستان"]),
    (("کتاب", "مطالعه"), ["#کتاب", "#کتابخوانی"]),
    (("اعتماد",), ["#اعتماد", "#اعتماد_به_نفس"]),
    (("پول", "درآمد", "ثروت", "سرمایه", "پس انداز", "هزینه"), ["#ثروت", "#مدیریت_مالی", "#استقلال_مالی"]),
    (("کسب و کار", "کسبوکار", "فروش", "مشتری", "بازاریابی"), ["#کسب_و_کار", "#بازاریابی", "#فروش"]),
    (("عادت", "استمرار", "تداوم", "پشتکار"), ["#عادت", "#استمرار", "#پشتکار"]),
    (("تمرکز", "ذهن", "فکر", "ذهنیت"), ["#تمرکز", "#ذهن_قوی", "#ذهنیت_رشد"]),
    (("زمان", "وقت", "برنامه ریزی"), ["#مدیریت_زمان", "#برنامه_ریزی"]),
    (("هوش مصنوعی", "ربات", "تکنولوژی"), ["#هوش_مصنوعی", "#تکنولوژی"]),
    (("ارز دیجیتال", "بیت کوین", "کریپتو", "ترید"), ["#ارز_دیجیتال", "#کریپتو"]),
]

HELP = (
    "سلام! متن ریلز رو بفرست.\n\n"
    "متن به‌صورت خودکار ویرایش می‌شه، یک هوک قوی برای شروع ویدیو ساخته می‌شه، "
    "ویدیو رندر می‌شه و ویدیو + کاور + کپشن برات ارسال می‌شه.\n"
    "نیازی به انتخاب دکمه یا تأیید دستی نیست.\n\n"
    "نکته‌ها:\n"
    "• هر پاراگراف (با یک خط خالی) یک صحنه می‌شه.\n"
    "• اگه می‌خوای هوک رو خودت بنویسی، خط اول رو با # شروع کن: # هوک من\n"
    "• اگه عنوان یک صحنه عدد باشه (مثل ۹۰٪)، عدد بزرگ روی صفحه نشون داده می‌شه:\n"
    "  ۹۰٪ :: بیشتر آدم‌ها این اشتباه رو می‌کنند."
)

# =========================================================
# Prompts
# =========================================================

FORMAT_GUIDE = """قالب خروجی (دقیقاً رعایت شود):
- خط اول: # هوک | برچسب
  (هوک همان تیتر یا جمله اول است؛ برچسب یک یا دو کلمه است مثل آموزشی، انگیزشی، مالی)
- هر خط بعدی یک صحنه است. اگر عنوان کوتاه مناسب وجود دارد به شکل «عنوان :: متن صحنه» بنویس
  و اگر عنوان لازم نیست فقط متن صحنه را بنویس.
- عنوان صحنه حداکثر سه کلمه است و از کلمات خود متن ساخته می‌شود.
- هر صحنه یک تا سه جمله دارد. جمله‌های بسیار طولانی را با ویرگول یا نقطه
  در محل مکث طبیعی تقسیم کن، بدون اینکه چیزی حذف یا اضافه شود.
- هر صحنه باید با نقطه، علامت سؤال یا علامت تعجب تمام شود."""

NARRATION_GUIDE = """راهنمای گویندگی حرفه‌ای (برای صدای fa-IR-FaridNeural):
- جمله‌ها را کوتاه و قابل‌نفس‌گیری نگه دار؛ هر جمله ترجیحاً کمتر از ۱۸ کلمه.
- ویرگول را دقیقاً جایی بگذار که گوینده باید مکث کوتاه کند؛ نقطه برای مکث کامل.
- پرسش‌های بلاغی را با «؟» تمام کن تا لحن پرسشی درست خوانده شود.
- عددها را به همان شکل ورودی (رقم) بنویس؛ سیستم خودش آن‌ها را برای صدا به حروف تبدیل می‌کند.
- کلمات انگلیسی را همان‌طور که نویسنده نوشته نگه دار؛ سیستم تلفظ آن‌ها را تنظیم می‌کند.
- اعراب‌گذاری هدفمند: فقط برای واژه‌هایی که بدون آن احتمال خوانش اشتباه دارند
  (مثل کسره اضافه در ترکیب‌های مبهم، تشدید، همزه، واژه‌های هم‌نویسه).
  واژه‌های ساده و روشن را اعراب‌گذاری نکن و هرگز اعراب حدسی نگذار."""

EDITOR_PROMPT = f"""تو ویراستار و کارگردان صدا برای متن‌های ریلز فارسی هستی. صدای گوینده
fa-IR-FaridNeural است. وظیفه تو بازنویسی یا خلاصه‌سازی نیست؛
فقط باید همان متن نویسنده را برای خواندن صحیح، طبیعی و حرفه‌ای آماده کنی.

قوانین قطعی و غیرقابل نقض:

۱. حفظ کامل محتوا:
- هیچ جمله، مثال، توضیح، دلیل، نکته، نتیجه‌گیری یا عددی را حذف نکن.
- متن را خلاصه یا کوتاه نکن و چیزی از خودت اضافه نکن.
- ترتیب جمله‌ها و ایده‌ها را حفظ کن.
- جمله‌ها را ادغام نکن اگر معنا کم‌رنگ می‌شود.
- هیچ جمله‌ای را به بهانه تکراری بودن حذف نکن.
- اگر اصلاح جمله‌ای معنا را تغییر می‌دهد، همان ساختار اصلی را نگه دار.

۲. املا و نگارش:
- غلط‌های املایی و تایپی را اصلاح کن.
- کلمات را با مترادف جایگزین نکن، مگر اینکه کلمه اصلی غلط واضح داشته باشد.
- از نیم‌فاصله استفاده نکن؛ فاصله معمولی به کار ببر (سیستم نیم‌فاصله را خودش درست می‌کند).

{NARRATION_GUIDE}

{FORMAT_GUIDE}

۳. کنترل نهایی:
- خروجی باید از نظر محتوا برابر ورودی باشد؛ تعداد کلمات نباید محسوس کم شود.
- پیش از ارائه، خروجی را با ورودی مقایسه کن.

فقط متن نهایی را خروجی بده. هیچ توضیحی ننویس و از Markdown اضافی استفاده نکن.
"""

REVIEW_PROMPT = f"""تو بازبین نهایی متن ریلز فارسی برای گویندگی با صدای fa-IR-FaridNeural هستی.

متن اصلی مرجع قطعی است. پیش‌نویس را با متن اصلی مقایسه کن و فقط املا، نگارش،
نشانه‌گذاری و تلفظ را اصلاح کن.

قوانین قطعی:
- تمام جمله‌ها، مثال‌ها، دلایل، نکته‌ها، عددها و نتیجه‌گیری‌های متن اصلی باید باقی بمانند.
- هیچ بخشی را خلاصه، کوتاه یا حذف نکن و چیزی اضافه نکن.
- اگر پیش‌نویس بخشی را حذف کرده یا معنایی را تغییر داده، به حالت اصلی برگردان.
- تکرارهای معنادار را حذف نکن. کوتاه شدن متن هدف نیست.
- اگر بین روان‌خوانی و حفظ محتوا تعارض بود، حفظ محتوا اولویت دارد.
- از نیم‌فاصله استفاده نکن.

{NARRATION_GUIDE}

{FORMAT_GUIDE}

فقط متن نهایی را با همان ساختار هوک و صحنه خروجی بده.
"""

HOOK_PROMPT = """تو نویسنده هوک ریلزهای وایرال فارسی هستی. فقط براساس متن زیر
یک هوک بنویس که در سه ثانیه اول بیننده را نگه دارد.

قوانین:
- فقط یک خط، بین ۴ تا ۱۰ کلمه، فارسی محاوره‌ای روان و قابل‌خواندن توسط گوینده.
- فقط از محتوای همین متن استفاده کن؛ آمار، ادعا یا عدد جدید نساز.
- یکی از این الگوها را انتخاب کن (هرکدام که برای متن قوی‌تر است):
  ۱) شکاف کنجکاوی: «چرا … که هیچ‌کس بهت نمی‌گه؟»
  ۲) باور غلط: «اشتباهی که همه درباره … می‌کنند»
  ۳) نتیجه + زمان: «با این عادت ساده، … رو عوض کن»
  ۴) پرسش مستقیم به بیننده: «تو هم همین اشتباه رو می‌کنی؟»
  ۵) عدد واقعی از خود متن: «۳ اشتباه که …»
- بدون ایموجی، بدون گیومه، بدون هشتگ، بدون توضیح؛ پایان جمله فقط ؟ یا ! یا بدون علامت.
- دروغ‌پراکنی و تیتر اغواگر نساز؛ هوک باید با محتوای متن هم‌خوان باشد.

فقط خود هوک را خروجی بده.
"""

CAPTION_PROMPT = """برای یک ریلز فارسی کپشن حرفه‌ای اینستاگرام بنویس.
ساختار:
۱) خط اول: هوک کنجکاوی‌برانگیز، حداکثر ۱۲ کلمه، بدون تکرار عین هوک ویدیو.
۲) دو تا سه جمله توصیف مفهومی ارزشمند با کلمات کلیدی موضوع (بدون کپی کردن جمله‌های متن).
۳) یک دعوت به اقدام مشخص که کامنت یا ذخیره یا ارسال برای دوست را بخواهد
   (مثلاً یک سؤال کوتاه که بیننده بخواهد جواب بدهد).
۴) یک خط خالی و بعد ۷ تا ۸ هشتگ فارسی مرتبط؛ ترکیبی از هشتگ پرجستجو و هشتگ تخصصی،
   با زیرخط بین کلمات.
حداکثر دو ایموجی، بدون اعراب، لحن صمیمی و حرفه‌ای. خروجی فقط خود کپشن.
"""


# =========================================================
# Telegram API
# =========================================================

def api(method, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)

    with urllib.request.urlopen(req, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error in {method}: {result}")
    return result


def say(text, **extra):
    text = str(text)
    for i in range(0, len(text), 3800):
        try:
            api("sendMessage", chat_id=CHAT_ID, text=text[i:i + 3800],
                **(extra if i == 0 else {}))
        except Exception as exc:
            print("sendMessage failed:", type(exc).__name__, str(exc)[:300])


def curl_send(method, field, path, extra=()):
    cmd = ["curl", "-sS", "--max-time", "400", "-F", f"chat_id={CHAT_ID}",
           "-F", f"{field}=@{path}", *extra, f"{API}/{method}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=420)
        return bool(json.loads(result.stdout).get("ok"))
    except Exception as exc:
        print("Telegram upload failed:", type(exc).__name__)
        return False


def set_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    print(f"{key}={value}")


def run_url():
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""


# =========================================================
# AI connection (with retries)
# =========================================================

def llm(system, user, temperature=0.3, attempts=2):
    payload = json.dumps({
        "model": "openai",
        "temperature": temperature,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()

    urls = ("https://text.pollinations.ai/openai",
            "https://gen.pollinations.ai/v1/chat/completions")

    for attempt in range(attempts):
        for url in urls:
            try:
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json", "User-Agent": UA})
                with urllib.request.urlopen(req, timeout=75) as response:
                    data = json.loads(response.read().decode("utf-8"))
                result = (data["choices"][0]["message"]["content"] or "").strip()
                if result:
                    return result
            except Exception as exc:
                print("AI service failed:", url.split("/")[2], type(exc).__name__)
        if attempt + 1 < attempts:
            time.sleep(3 * (attempt + 1))
    return None


# =========================================================
# Script helpers
# =========================================================

def sentences_of(text):
    parts = re.split(r"(?<=[.!؟?…])\s+", " ".join(text.split()))
    return [p.strip() for p in parts if p.strip()]


def ensure_end(line):
    """A closing mark gives the voice a natural pause and makes chunking reliable."""
    line = line.rstrip()
    return line if not line or re.search(r"[.!؟?…؛:،,]$", line) else line + "."


def polish_script(script):
    """Typography + closing punctuation for every scene; keeps the '# hook | tag' header."""
    out = []
    for raw in script.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            out.append(fix_persian(line))
            continue
        if "::" in line:
            title, body = [x.strip() for x in line.split("::", 1)]
            out.append(f"{fix_persian(title)} :: {ensure_end(fix_persian(body))}")
        else:
            out.append(ensure_end(fix_persian(line)))
    return "\n".join(out) + "\n"


def auto_format(text):
    """Local fallback: structure the author's text without cutting a single word."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return ""

    # the author already structured it: keep it
    if any(l.startswith("#") for l in lines):
        return polish_script("\n".join(lines))

    if any("::" in l for l in lines):
        first_body = lines[0].split("::", 1)[1] if "::" in lines[0] else lines[0]
        hook = (sentences_of(first_body) or [first_body])[0]
        return polish_script("# " + hook.rstrip(".،") + " | آموزشی\n" + "\n".join(lines))

    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    first = sentences_of(paragraphs[0]) or [paragraphs[0]]
    hook = first[0]

    scenes = []
    if len(paragraphs) > 1:                        # each paragraph = one scene
        rest = " ".join(first[1:]).strip()
        scenes = ([rest] if rest else []) + paragraphs[1:]
    else:                                          # group sentences (<= 2 and <= ~150 chars)
        pool, cur = first[1:] or first, []
        for s in pool:
            if cur and (len(cur) >= 2 or len(" ".join(cur + [s])) > 150):
                scenes.append(" ".join(cur))
                cur = []
            cur.append(s)
        if cur:
            scenes.append(" ".join(cur))
    if not scenes:
        scenes = [" ".join(paragraphs)]

    return polish_script("# " + hook.rstrip(".،") + " | آموزشی\n" + "\n".join(scenes))


def clean_script(output):
    if not output:
        return None
    output = output.replace("```", "").replace("**", "").strip()
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith("#"):
            return "\n".join(x.rstrip() for x in lines[index:] if x.strip())
    return None


def valid_script(script):
    lines = [l.strip() for l in script.splitlines() if l.strip()]
    if len(lines) < 2 or not lines[0].startswith("#"):
        return False

    letters = [c for c in script if c.isalpha()]
    if not letters:
        return False
    persian = sum("\u0600" <= c <= "\u06ff" for c in letters)

    word_count = len(strip_marks(script).split())
    return persian / len(letters) > 0.60 and word_count >= 15


def digits(script):
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return set(re.findall(r"[0-9]+", script.translate(table)))


def words(text):
    return len(re.findall(r"\S+", text))


def hook_and_scenes(script):
    hook, tag, scenes = "", "", []
    for line in script.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#") and not hook:
            body = line.lstrip("#").strip()
            hook, _, tag = [x.strip() for x in body.partition("|")]
        else:
            scenes.append(line)
    return hook, tag, scenes


# =========================================================
# Text improvement, review and hook boost
# =========================================================

def improve(text):
    draft = clean_script(llm(EDITOR_PROMPT, "متن نویسنده:\n" + text, 0.20))

    if not draft or not valid_script(draft) or not digits(draft).issubset(digits(text)):
        return None

    source_words = words(text)

    # guard against silent summarising
    if source_words >= 25 and words(draft) < source_words * 0.90:
        print(f"Editor output too short; using formatting fallback ({words(draft)}/{source_words} words).")
        return None

    reviewed = clean_script(llm(
        REVIEW_PROMPT,
        "متن اصلی (تمام محتوا باید حفظ شود):\n" + text +
        "\n\nپیش‌نویس برای اصلاح املا و تلفظ:\n" + draft,
        0.10))

    if reviewed and valid_script(reviewed) and digits(reviewed).issubset(digits(text)):
        if source_words < 25 or words(reviewed) >= source_words * 0.90:
            return polish_script(reviewed)
        print(f"Review output too short; keeping longer draft ({words(reviewed)}/{source_words} words).")

    return polish_script(draft)


def boost_hook(script, source_text):
    """Replace a plain first-sentence hook by a punchy one; the old hook stays as scene 1."""
    hook, tag, scenes = hook_and_scenes(script)
    if not hook or not scenes:
        return script

    raw = llm(HOOK_PROMPT, "متن ریلز:\n" + strip_marks(script), 0.65)
    if not raw:
        return script

    new = strip_marks(raw.replace("```", "").replace("**", "")).strip().splitlines()[0].strip(' "«»“”#*-')
    new = fix_persian(new)
    letters = [c for c in new if c.isalpha()]
    persian_ratio = (sum("\u0600" <= c <= "\u06ff" for c in letters) / len(letters)) if letters else 0
    n_words = len(new.split())

    ok = (4 <= n_words <= 12 and len(new) <= 80 and persian_ratio > 0.85
          and digits(new).issubset(digits(source_text))
          and "#" not in new and "http" not in new)
    if not ok or norm(new) == norm(hook):
        print("Hook booster rejected; keeping the original hook.")
        return script

    old_hook = ensure_end(hook)
    lines = [f"# {new} | {tag or 'آموزشی'}", old_hook, *scenes]
    return "\n".join(lines) + "\n"


# =========================================================
# Caption helpers
# =========================================================

def script_parts(job_text):
    hook, _, lines = hook_and_scenes(job_text)
    scenes = [l.split("::", 1)[1].strip() if "::" in l else l for l in lines]
    return strip_marks(hook), [strip_marks(item) for item in scenes]


def fallback_caption(job_text):
    hook, _ = script_parts(job_text)
    normalized = norm(job_text)
    tags = []
    for keys, hashtags in TAG_MAP:
        if any(norm(key) in normalized for key in keys):
            tags.extend(t for t in hashtags if t not in tags)
    tags = (tags + BASE_TAGS)[:8]

    return (f"{hook}\n\n"
            "📌 ذخیره کن و برای دوستت بفرست.\n"
            "💬 نظرت رو بنویس 👇\n\n" + " ".join(tags))


def make_caption(job_text):
    hook, scenes = script_parts(job_text)
    output = llm(CAPTION_PROMPT, hook + "\n" + "\n".join(scenes), 0.6)

    if output:
        output = strip_marks(output.replace("```", "").replace("**", "").strip())
        letters = [c for c in output if c.isalpha()]
        persian = sum("\u0600" <= c <= "\u06ff" for c in letters)
        if (letters and persian / len(letters) > 0.6
                and 80 <= len(output) <= 2000 and "#" in output):
            return output

    return fallback_caption(job_text)


# =========================================================
# Queue and Telegram message processing
# =========================================================

def read_offset():
    try:
        return int(STATE.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def save_job(update_id, script):
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / f"job_{update_id}.txt"
    if not path.exists():
        path.write_text(script, encoding="utf-8")
    return path


def on_text(message, update_id):
    text = (message.get("text") or "").strip()
    reply_to = ({"reply_to_message_id": message["message_id"]}
                if message.get("message_id") else {})

    if not text:
        say("فقط پیام متنی رو می‌فهمم. متن ریلز رو بفرست.", **reply_to)
        return 0

    command = text.split(maxsplit=1)[0].split("@")[0].lower()

    if command in ("/start", "/help"):
        say(HELP, **reply_to)
        return 0

    if text.startswith("/"):
        say("این دستور رو نمی‌شناسم. /help رو بزن.", **reply_to)
        return 0

    say("متنت دریافت شد؛ دارم ویرایشش می‌کنم، هوک می‌سازم و ویدیو رو آماده می‌کنم.", **reply_to)

    try:
        edited = improve(text)
    except Exception as exc:
        print("Text editing error:", type(exc).__name__)
        edited = None

    # if the AI shortens or breaks the text, the author's own text is formatted locally
    script = edited if edited else auto_format(text)

    if not script.strip():
        say("نتونستم متن رو آماده کنم. لطفاً متن رو دوباره بفرست.", **reply_to)
        return 0

    # the author wrote his own "# hook" -> respect it; otherwise try a stronger hook
    author_wrote_hook = any(l.strip().startswith("#") for l in text.splitlines())
    if HOOK_BOOST and not author_wrote_hook:
        try:
            script = boost_hook(script, text)
        except Exception as exc:
            print("Hook booster error:", type(exc).__name__)

    save_job(update_id, script)
    print(f"JOB_CREATED: job_{update_id}.txt")
    return 1


# =========================================================
# Telegram polling
# =========================================================

def poll():
    if not TOKEN or not CHAT_ID:
        sys.exit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret.")

    offset = read_offset()
    result = api("getUpdates", offset=offset, timeout=0,
                 allowed_updates=json.dumps(["message"]))

    new_offset, jobs = offset, 0

    for update in result.get("result", []):
        new_offset = max(new_offset, update["update_id"] + 1)
        message = update.get("message") or {}

        if str(message.get("chat", {}).get("id")) != CHAT_ID:
            continue

        # bot messages and non-text messages are ignored
        if message.get("from", {}).get("is_bot") or not message.get("text"):
            continue

        jobs += on_text(message, update["update_id"])

    if new_offset != offset:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(str(new_offset), encoding="utf-8")

    set_output("jobs", jobs)
    set_output("changed", "true" if new_offset != offset else "false")


# =========================================================
# Video size and upload
# =========================================================

def shrink_if_needed(path):
    if path.stat().st_size <= MAX_TG_BYTES:
        return path

    small = path.with_name("reel_small.mp4")
    for crf, scale in (("27", None), ("30", "720:-2")):
        cmd = ["ffmpeg", "-y", "-i", str(path)]
        if scale:
            cmd += ["-vf", f"scale={scale}"]
        cmd += ["-c:v", "libx264", "-crf", crf, "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(small)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and small.exists() and small.stat().st_size <= MAX_TG_BYTES:
            return small
    return path


def send_results(job, video):
    video = shrink_if_needed(video)

    ok = curl_send("sendVideo", "video", video,
                   ["-F", "supports_streaming=true", "-F", "width=1080", "-F", "height=1920"])
    if not ok:
        say("⚠️ ویدیو ساخته شد ولی ارسالش به تلگرام ناموفق بود.\n" + run_url())
        return False

    cover = video.with_name("cover.png")
    if not cover.exists():
        cover = video.parent / "cover.png"
    if cover.exists():
        curl_send("sendPhoto", "photo", cover,
                  ["-F", "caption=🖼 کاور پیشنهادی (برای انتخاب به‌عنوان کاور ریلز)"])

    say("کپشن پیشنهادی (برای کپی):")
    say(make_caption(job.read_text(encoding="utf-8")))
    return True


# =========================================================
# Video build
# =========================================================

def tries_path(job):
    return WORK / f"{job.stem}.tries"


def bump_tries(job):
    try:
        n = int(tries_path(job).read_text().strip()) + 1
    except Exception:
        n = 1
    tries_path(job).write_text(str(n))
    return n


def give_up_if_needed(job):
    """After MAX_TRIES failed runs the job is parked in work/failed (no endless error messages)."""
    n = bump_tries(job)
    if n >= MAX_TRIES:
        shutil.move(str(job), str(FAILED / job.name))
        tries_path(job).unlink(missing_ok=True)
        say(f"⛔ ساخت {job.name} بعد از {MAX_TRIES} تلاش ناموفق بود و کنار گذاشته شد.\n"
            "متن رو (کوتاه‌تر یا ساده‌تر) دوباره بفرست.\n" + run_url())
        return True
    return False


def build():
    WORK.mkdir(parents=True, exist_ok=True)
    FAILED.mkdir(parents=True, exist_ok=True)
    HISTORY.mkdir(parents=True, exist_ok=True)

    jobs = sorted(WORK.glob("job_*.txt"))
    if not jobs:
        print("NO_PENDING_JOBS")
        return

    env = {**os.environ, "REEL_HISTORY_DIR": str(HISTORY)}

    for job in jobs:
        out = OUT / job.stem / "reel.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)

        try:
            result = subprocess.run(
                [sys.executable, "make_reel.py", str(job), str(out)],
                capture_output=True, text=True, timeout=2400, env=env)

            log = (result.stdout or "") + "\n" + (result.stderr or "")
            print(log[-4000:])

            if result.returncode != 0 or not out.exists() or out.stat().st_size == 0:
                if not give_up_if_needed(job):
                    tail = "\n".join(log.strip().splitlines()[-15:])
                    say("❌ ساخت ویدیو خطا داد؛ اجرای بعدی دوباره تلاش می‌کنه.\n"
                        + run_url() + "\n\n" + tail[-1200:])
                continue

            if send_results(job, out):
                job.unlink(missing_ok=True)
                tries_path(job).unlink(missing_ok=True)
            else:
                give_up_if_needed(job)

        except Exception as exc:
            print("BUILD_ERROR:", type(exc).__name__, str(exc)[:500])
            if not give_up_if_needed(job):
                say("❌ هنگام ساخت یا ارسال ویدیو خطا رخ داد.\n" + run_url())


# =========================================================
# Main
# =========================================================

if __name__ == "__main__":
    commands = {"poll": poll, "build": build}
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    commands.get(command, lambda: sys.exit("usage: python bot.py poll|build"))()
