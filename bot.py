"""Automatic Telegram-to-Reels driver.

Usage:
  python bot.py poll   # receive text, edit it, queue job
  python bot.py build  # render queued jobs and send them to Telegram

Required environment variables: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from make_reel import UA, norm, strip_marks

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"

STATE = Path("state/offset.txt")
WORK = Path("work")
FAILED = WORK / "failed"
OUT = Path("out")

MAX_TG_BYTES = 48 * 1024 * 1024
BAR = "━━━━━━━━━━"

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

HELP = (
    "سلام! متن ریلز رو بفرست.\n\n"
    "متن به‌صورت خودکار اصلاح می‌شه، ویدیو ساخته می‌شه و نتیجه برات ارسال می‌شه.\n"
    "نیازی به انتخاب دکمه یا تأیید دستی نیست.\n\n"
    "برای مشخص کردن صحنه‌ها، می‌تونی هر صحنه رو در یک خط بنویسی."
)

# =========================================================
# Persian text editing prompts
# =========================================================

EDITOR_PROMPT = """تو ویراستار تخصصی متن فارسی برای گویندگی با صدای
fa-IR-FaridNeural هستی. وظیفه تو بازنویسی یا خلاصه‌سازی نیست؛
فقط باید همان متن نویسنده را برای خواندن صحیح و طبیعی آماده کنی.

قوانین قطعی و غیرقابل نقض:

۱. حفظ کامل محتوا:
- هیچ جمله‌ای را حذف نکن.
- هیچ مثال، توضیح، دلیل، نکته، نتیجه‌گیری یا جزئیاتی را حذف نکن.
- متن را خلاصه نکن و کوتاه نکن.
- جمله‌ها را با یکدیگر ادغام نکن، اگر باعث حذف یا کم‌رنگ شدن بخشی از معنا می‌شود.
- ترتیب جمله‌ها و ترتیب ایده‌های نویسنده را حفظ کن.
- چیزی از خودت به متن اضافه نکن.
- هیچ جمله‌ای را صرفاً به دلیل تکراری به نظر رسیدن حذف نکن.
- هیچ بخش از متن را فدای کوتاه‌تر شدن تیتر یا روان‌تر شدن ظاهری متن نکن.
- اگر اصلاح یک جمله باعث تغییر معنا می‌شود، ساختار اصلی آن را حفظ کن.

۲. اصلاح املایی و نگارشی:
- غلط‌های املایی و تایپی را اصلاح کن.
- نشانه‌گذاری را برای خواندن طبیعی تنظیم کن.
- از ویرگول، نقطه و علامت سؤال برای ایجاد مکث درست استفاده کن.
- کلمات را با مترادف جایگزین نکن، مگر اینکه کلمه اصلی غلط واضح داشته باشد.
- ساختار جمله را فقط در حدی تغییر بده که خواندن آن راحت‌تر شود و معنا کاملاً ثابت بماند.

۳. آماده‌سازی برای تلفظ فرید:
- کسره اضافه را در ترکیب‌هایی که برای خوانش درست لازم است درج کن.
- همزه را مطابق املای صحیح فارسی بنویس.
- تشدید را در واژه‌هایی که نیاز دارند درست درج کن.
- فتحه، ضمه و سایر اعراب را فقط در واژه‌هایی اضافه کن که بدون آن‌ها احتمال خوانش اشتباه وجود دارد.
- از اعراب‌گذاری افراطی خودداری کن.
- واژه‌های ساده و دارای یک تلفظ روشن را بی‌دلیل اعراب‌گذاری نکن.
- در واژه‌های حساس، مبهم، مرکب یا دارای تلفظ دشوار، نشانه‌های لازم را درج کن.
- اگر تلفظ یک کلمه ممکن است با حرکت‌گذاری اشتباه شود، از حرکت‌گذاری حدسی خودداری کن.
- هدف، تلفظ صحیح و طبیعی است؛ نه پر کردن متن از علامت‌های اعراب.

۴. قالب‌بندی:
- اگر متن تیتر دارد، تیتر را حفظ کن.
- اگر متن صحنه‌بندی دارد، ترتیب و محتوای تمام صحنه‌ها را حفظ کن.
- اگر نیاز به صحنه‌بندی دارد، متن را به بخش‌های خوانا تقسیم کن.
- هیچ جمله‌ای را برای کوتاه کردن تیتر حذف نکن.
- برای تیتر از کلمات خود متن استفاده کن.
- از نیم‌فاصله استفاده نکن؛ فاصله معمولی به کار ببر.

۵. کنترل نهایی:
- متن خروجی باید از نظر محتوا با متن ورودی برابر باشد.
- تعداد کلمات نباید به شکل محسوس کاهش پیدا کند.
- پیش از ارائه خروجی، متن را با ورودی مقایسه کن.
- مطمئن شو تمام جمله‌ها، مثال‌ها و نکات متن اصلی باقی مانده‌اند.
- اگر نمی‌توانی بخشی را بدون تغییر معنا اصلاح کنی، همان بخش را با کمترین تغییر نگه دار.

فقط متن نهایی را خروجی بده.
هیچ توضیحی درباره اصلاحات ننویس.
از Markdown اضافی استفاده نکن.
"""

REVIEW_PROMPT = """تو بازبین نهایی متن فارسی برای گویندگی با صدای
fa-IR-FaridNeural هستی.

متن اصلی مرجع قطعی است. پیش‌نویس را با متن اصلی مقایسه کن.

وظیفه تو فقط اصلاح املا، نگارش، نشانه‌گذاری و تلفظ است.

قوانین قطعی:

- تمام جمله‌های متن اصلی باید در نسخه نهایی باقی بمانند.
- تمام مثال‌ها، توضیحات، دلایل، نکته‌ها و نتیجه‌گیری‌ها باید حفظ شوند.
- هیچ بخشی را خلاصه نکن.
- هیچ جمله‌ای را کوتاه نکن.
- هیچ محتوایی را حذف نکن.
- چیزی به متن اصلی اضافه نکن.
- ترتیب جمله‌ها و ایده‌ها را تغییر نده.
- اگر پیش‌نویس بخشی از متن اصلی را حذف کرده، آن بخش را برگردان.
- اگر پیش‌نویس معنای جمله‌ای را تغییر داده، آن را به معنای اصلی برگردان.
- تکرارهای معنادار را حذف نکن.
- کوتاه شدن متن به‌هیچ‌وجه هدف نیست.

اصلاحات مجاز:
- اصلاح غلط‌های املایی و تایپی
- اصلاح نشانه‌گذاری
- تنظیم مکث‌ها برای گویندگی
- درج کسره اضافه در موارد لازم
- اصلاح همزه و تشدید
- درج فتحه و ضمه فقط برای جلوگیری از تلفظ اشتباه
- اصلاح جمله‌بندی فقط در حدی که معنا تغییر نکند

برای تلفظ:
- اعراب‌گذاری باید هدفمند باشد.
- کلمات ساده و روشن را بی‌دلیل اعراب‌گذاری نکن.
- برای کلمات مبهم یا دارای تلفظ حساس، حرکت لازم را درج کن.
- از اعراب‌گذاری حدسی خودداری کن.

از نیم‌فاصله استفاده نکن و فاصله معمولی به کار ببر.

در پایان، متن نهایی باید تمام محتوای متن اصلی را داشته باشد.
اگر بین روان‌خوانی و حفظ محتوا تعارض وجود داشت، حفظ کامل محتوا اولویت دارد.

فقط متن نهایی را با ساختار تیتر و صحنه خروجی بده.
"""

CAPTION_PROMPT = """برای یک ریلز فارسی کپشن بنویس:
خط اول کنجکاوی‌برانگیز و حداکثر 12 کلمه؛
سپس خلاصه مفهومی در 2 تا 3 جمله، بدون کپی کردن جمله‌های متن؛
یک دعوت به اقدام متناسب؛
سپس خط خالی و 6 تا 8 هشتگ فارسی مرتبط با زیرخط بین کلمات.
حداکثر دو ایموجی، بدون اعراب، خروجی فقط کپشن.
"""


# =========================================================
# Telegram API
# =========================================================

def api(method, **params):
    data = urllib.parse.urlencode(params).encode()

    req = urllib.request.Request(
        f"{API}/{method}",
        data=data
    )

    with urllib.request.urlopen(req, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error in {method}: {result}"
        )

    return result


def say(text, **extra):
    text = str(text)

    for i in range(0, len(text), 3800):
        try:
            api(
                "sendMessage",
                chat_id=CHAT_ID,
                text=text[i:i + 3800],
                **(extra if i == 0 else {})
            )

        except Exception as exc:
            print(
                "sendMessage failed:",
                type(exc).__name__,
                str(exc)[:300]
            )


def curl_send(method, field, path, extra=()):
    cmd = [
        "curl",
        "-sS",
        "--max-time",
        "400",
        "-F",
        f"chat_id={CHAT_ID}",
        "-F",
        f"{field}=@{path}",
        *extra,
        f"{API}/{method}"
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=420
        )

        data = json.loads(result.stdout)
        return bool(data.get("ok"))

    except Exception as exc:
        print(
            "Telegram upload failed:",
            type(exc).__name__
        )
        return False


def set_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")

    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")

    print(f"{key}={value}")


def run_url():
    server = os.environ.get(
        "GITHUB_SERVER_URL",
        "https://github.com"
    )

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")

    return (
        f"{server}/{repo}/actions/runs/{run_id}"
        if repo and run_id
        else ""
    )


# =========================================================
# AI connection
# =========================================================

def llm(system, user, temperature=0.3):
    payload = json.dumps({
        "model": "openai",
        "temperature": temperature,
        "messages": [
            {
                "role": "system",
                "content": system
            },
            {
                "role": "user",
                "content": user
            }
        ],
    }).encode()

    urls = (
        "https://text.pollinations.ai/openai",
        "https://gen.pollinations.ai/v1/chat/completions"
    )

    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": UA
                }
            )

            with urllib.request.urlopen(req, timeout=75) as response:
                data = json.loads(
                    response.read().decode("utf-8")
                )

            result = (
                data["choices"][0]["message"]["content"] or ""
            ).strip()

            if result:
                return result

        except Exception as exc:
            print(
                "AI service failed:",
                url.split("/")[2],
                type(exc).__name__
            )

    return None


# =========================================================
# Text formatting
# =========================================================

def auto_format(text):
    """
    قالب‌بندی متن بدون کوتاه کردن عنوان یا حذف جمله‌ها.
    """

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if not lines:
        return ""

    # اگر کاربر خودش تیتر یا صحنه‌بندی نوشته،
    # ساختار اصلی او حفظ می‌شود.
    if any(line.startswith("#") for line in lines):
        return "\n".join(lines) + "\n"

    joined = " ".join(lines)

    sentences = [
        s.strip()
        for s in re.split(
            r"(?<=[.!؟?…])\s+",
            joined
        )
        if s.strip()
    ]

    if not sentences:
        sentences = [joined]

    # عنوان از جمله اول ساخته می‌شود؛
    # هیچ کلمه‌ای از عنوان حذف نمی‌شود.
    hook = sentences[0]

    scenes = sentences[1:] or sentences

    chunks = [
        " ".join(scenes[i:i + 2])
        for i in range(0, len(scenes), 2)
    ]

    if not chunks:
        chunks = [joined]

    result = (
        "# "
        + hook.rstrip(".،")
        + " | آموزشی\n"
    )

    result += "\n".join(
        f"صحنه {i + 1} :: {chunk}"
        for i, chunk in enumerate(chunks)
    )

    return result + "\n"


def clean_script(output):
    if not output:
        return None

    output = (
        output
        .replace("```", "")
        .replace("**", "")
        .strip()
    )

    lines = output.splitlines()

    for index, line in enumerate(lines):
        if line.strip().startswith("#"):
            return "\n".join(
                x.rstrip()
                for x in lines[index:]
                if x.strip()
            )

    return None


def valid_script(script):
    lines = [
        line.strip()
        for line in script.splitlines()
        if line.strip()
    ]

    # محدودیت حداکثر تعداد خط برداشته شده است.
    # متن‌های بلند نباید صرفاً به دلیل طولشان رد شوند.
    if len(lines) < 2 or not lines[0].startswith("#"):
        return False

    letters = [
        char
        for char in script
        if char.isalpha()
    ]

    if not letters:
        return False

    persian = sum(
        "\u0600" <= char <= "\u06ff"
        for char in letters
    )

    persian_ratio = persian / len(letters)

    # حداقل 15 کلمه برای جلوگیری از خروجی‌های ناقص یا نامعتبر.
    # محدودیت حداکثر تعداد کلمات برداشته شده است.
    word_count = len(strip_marks(script).split())

    return persian_ratio > 0.60 and word_count >= 15


def digits(script):
    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )

    return set(
        re.findall(
            r"[0-9]+",
            script.translate(table)
        )
    )


# =========================================================
# Text improvement and review
# =========================================================

def improve(text):
    draft = clean_script(
        llm(
            EDITOR_PROMPT,
            "متن نویسنده:\n" + text,
            0.20
        )
    )

    if (
        not draft
        or not valid_script(draft)
        or not digits(draft).issubset(digits(text))
    ):
        return None

    source_words = len(
        re.findall(r"\S+", text)
    )

    draft_words = len(
        re.findall(r"\S+", draft)
    )

    # کنترل اولیه برای جلوگیری از خلاصه‌سازی محسوس.
    # این کنترل جایگزین بررسی معنایی جمله‌به‌جمله نیست.
    if (
        source_words >= 25
        and draft_words < source_words * 0.90
    ):
        print(
            "Editor output too short; "
            f"using formatting fallback ({draft_words}/{source_words} words)."
        )
        return None

    reviewed = clean_script(
        llm(
            REVIEW_PROMPT,
            (
                "متن اصلی (تمام محتوا باید حفظ شود):\n"
                + text
                + "\n\nپیش‌نویس برای اصلاح املا و تلفظ:\n"
                + draft
            ),
            0.10
        )
    )

    if (
        reviewed
        and valid_script(reviewed)
        and digits(reviewed).issubset(digits(text))
    ):
        reviewed_words = len(
            re.findall(r"\S+", reviewed)
        )

        if (
            source_words < 25
            or reviewed_words >= source_words * 0.90
        ):
            return reviewed + "\n"

        print(
            "Review output too short; "
            f"keeping longer draft ({reviewed_words}/{source_words} words)."
        )

    return draft + "\n"


# =========================================================
# Caption helpers
# =========================================================

def script_parts(job_text):
    hook = ""
    scenes = []

    for line in job_text.splitlines():
        line = line.strip()

        if not line:
            continue

        if line.startswith("#") and not hook:
            hook = (
                line
                .lstrip("#")
                .split("|")[0]
                .strip()
            )

        else:
            scenes.append(
                line.split("::", 1)[1].strip()
                if "::" in line
                else line
            )

    # حذف اعراب فقط برای متن کپشن است.
    # این تابع مسیر تولید صدای ویدیو نیست.
    return (
        strip_marks(hook),
        [strip_marks(item) for item in scenes]
    )


def fallback_caption(job_text):
    hook, _ = script_parts(job_text)

    normalized = norm(job_text)
    tags = []

    for keys, hashtags in TAG_MAP:
        if any(key in normalized for key in keys):
            tags.extend(
                tag
                for tag in hashtags
                if tag not in tags
            )

    tags = (tags + BASE_TAGS)[:8]

    return (
        f"{hook}\n\n"
        "📌 ذخیره کن و برای دوستت بفرست.\n"
        "💬 نظرت رو بنویس 👇\n\n"
        + " ".join(tags)
    )


def make_caption(job_text):
    hook, scenes = script_parts(job_text)

    plain = hook + "\n" + "\n".join(scenes)

    output = llm(
        CAPTION_PROMPT,
        plain,
        0.55
    )

    if output:
        output = strip_marks(
            output
            .replace("```", "")
            .replace("**", "")
            .strip()
        )

        letters = [
            c
            for c in output
            if c.isalpha()
        ]

        persian = sum(
            "\u0600" <= c <= "\u06ff"
            for c in letters
        )

        if (
            letters
            and persian / len(letters) > 0.6
            and 80 <= len(output) <= 1800
            and "#" in output
        ):
            return output

    return fallback_caption(job_text)


# =========================================================
# Queue and Telegram message processing
# =========================================================

def read_offset():
    try:
        return int(
            STATE.read_text(encoding="utf-8").strip()
        )

    except Exception:
        return 0


def save_job(update_id, script):
    WORK.mkdir(
        parents=True,
        exist_ok=True
    )

    path = WORK / f"job_{update_id}.txt"

    if not path.exists():
        path.write_text(
            script,
            encoding="utf-8"
        )

    return path


def on_text(message, update_id):
    text = (
        message.get("text") or ""
    ).strip()

    reply_to = (
        {"reply_to_message_id": message["message_id"]}
        if message.get("message_id")
        else {}
    )

    if not text:
        say(
            "فقط پیام متنی رو می‌فهمم. متن ریلز رو بفرست.",
            **reply_to
        )
        return 0

    command = (
        text
        .split(maxsplit=1)[0]
        .split("@")[0]
        .lower()
    )

    if command in ("/start", "/help"):
        say(HELP, **reply_to)
        return 0

    if text.startswith("/"):
        say(
            "این دستور رو نمی‌شناسم. /help رو بزن.",
            **reply_to
        )
        return 0

    say(
        "متنت دریافت شد؛ دارم اصلاحش می‌کنم و ویدیو رو می‌سازم.",
        **reply_to
    )

    try:
        edited = improve(text)

    except Exception as exc:
        print(
            "Text editing error:",
            type(exc).__name__
        )
        edited = None

    # اگر هوش مصنوعی متن را ناقص یا کوتاه کند،
    # نسخه قالب‌بندی‌شده از متن اصلی ساخته می‌شود.
    script = (
        edited
        if edited
        else auto_format(text)
    )

    if not script.strip():
        say(
            "نتونستم متن رو آماده کنم. لطفاً متن رو دوباره بفرست.",
            **reply_to
        )
        return 0

    save_job(
        update_id,
        script
    )

    print(
        f"JOB_CREATED: job_{update_id}.txt"
    )

    return 1


# =========================================================
# Telegram polling
# =========================================================

def poll():
    if not TOKEN or not CHAT_ID:
        sys.exit(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret."
        )

    offset = read_offset()

    result = api(
        "getUpdates",
        offset=offset,
        timeout=0,
        allowed_updates=json.dumps(["message"])
    )

    new_offset = offset
    jobs = 0

    for update in result.get("result", []):
        new_offset = max(
            new_offset,
            update["update_id"] + 1
        )

        message = update.get("message") or {}

        if str(
            message.get("chat", {}).get("id")
        ) != CHAT_ID:
            continue

        # پیام‌های ربات و پیام‌های غیرمتنی نادیده گرفته می‌شوند.
        if (
            message.get("from", {}).get("is_bot")
            or not message.get("text")
        ):
            continue

        jobs += on_text(
            message,
            update["update_id"]
        )

    if new_offset != offset:
        STATE.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        STATE.write_text(
            str(new_offset),
            encoding="utf-8"
        )

    set_output("jobs", jobs)

    set_output(
        "changed",
        "true" if new_offset != offset else "false"
    )


# =========================================================
# Video size and upload
# =========================================================

def shrink_if_needed(path):
    if path.stat().st_size <= MAX_TG_BYTES:
        return path

    small = path.with_name("reel_small.mp4")

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-c:v",
            "libx264",
            "-crf",
            "30",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(small)
        ],
        capture_output=True,
        text=True
    )

    if (
        result.returncode == 0
        and small.exists()
        and small.stat().st_size <= MAX_TG_BYTES
    ):
        return small

    return path


def mascot_report(log):
    found = re.search(
        r"^MASCOT: found=(.*)$",
        log,
        re.M
    )

    used = re.search(
        r"^MASCOT: used=(.*)$",
        log,
        re.M
    )

    if not found or found.group(1).strip() == "none":
        return (
            "🦁 فایل تصویر شیر پیدا نشد؛ "
            "اگر به شیر نیاز داری، فایل‌های تصویر را در پوشه mascot قرار بده."
        )

    report = (
        "🦁 فایل‌های شیر پیدا شده: "
        + found.group(1).strip()
    )

    if used and used.group(1).strip() != "none":
        report += (
            "\nحالت‌های استفاده‌شده: "
            + used.group(1).strip()
        )

    return report


def send_results(job, video, log):
    video = shrink_if_needed(video)

    ok = curl_send(
        "sendVideo",
        "video",
        video,
        [
            "-F",
            "supports_streaming=true",
            "-F",
            "width=1080",
            "-F",
            "height=1920"
        ]
    )

    if not ok:
        say(
            "⚠️ ویدیو ساخته شد ولی ارسالش به تلگرام ناموفق بود.\n"
            + run_url()
        )
        return False

    say("کپشن پیشنهادی (برای کپی):")

    say(
        make_caption(
            job.read_text(encoding="utf-8")
        )
    )

    return True


# =========================================================
# Video build
# =========================================================

def build():
    WORK.mkdir(
        parents=True,
        exist_ok=True
    )

    FAILED.mkdir(
        parents=True,
        exist_ok=True
    )

    jobs = sorted(
        WORK.glob("job_*.txt")
    )

    if not jobs:
        print("NO_PENDING_JOBS")
        return

    for job in jobs:
        out = (
            OUT
            / job.stem
            / "reel.mp4"
        )

        out.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "make_reel.py",
                    str(job),
                    str(out)
                ],
                capture_output=True,
                text=True,
                timeout=2400
            )

            log = (
                (result.stdout or "")
                + "\n"
                + (result.stderr or "")
            )

            print(log[-4000:])

            if (
                result.returncode != 0
                or not out.exists()
                or out.stat().st_size == 0
            ):
                tail = "\n".join(
                    log.strip().splitlines()[-15:]
                )

                say(
                    "❌ ساخت ویدیو خطا داد.\n"
                    + run_url()
                    + "\n\n"
                    + tail[-1200:]
                )

                # فایل در صف باقی می‌ماند تا اجرای بعدی دوباره امتحان کند.
                continue

            if send_results(job, out, log):
                job.unlink(missing_ok=True)

            # در صورت ناموفق بودن ارسال، فایل در صف باقی می‌ماند.

        except Exception as exc:
            print(
                "BUILD_ERROR:",
                type(exc).__name__,
                str(exc)[:500]
            )

            say(
                "❌ هنگام ساخت یا ارسال ویدیو خطا رخ داد.\n"
                + run_url()
            )

            # فایل کار برای تلاش مجدد در work باقی می‌ماند.


# =========================================================
# Main
# =========================================================

if __name__ == "__main__":
    commands = {
        "poll": poll,
        "build": build
    }

    command = (
        sys.argv[1]
        if len(sys.argv) > 1
        else ""
    )

    commands.get(
        command,
        lambda: sys.exit(
            "usage: python bot.py poll|build"
        )
    )()
