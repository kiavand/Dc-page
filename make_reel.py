"""Persian Reel builder v4.5 (Ultra-Professional Cinematic Deep Olive & Gold Motion UI).

Input file (input.txt):
  line starting with "#"        -> cover topic/hook (spoken first, shown on the cover)
  every other non-empty line    -> one scene:   عنوان کوتاه :: متن روایت
Each sentence is spoken and shown on screen one at a time.
Backgrounds feature high-end cinematic deep olive/matte dark aesthetics with motion icons.

Usage: python make_reel.py input.txt out/reel.mp4
Outputs: reel.mp4 and cover.png in the same folder.
"""
import asyncio
import base64
import hashlib
import html
import os
import random
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import unicodedata
import edge_tts
from pathlib import Path

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

W, H, FPS = 1080, 1920, 30
GAP = 0.30                       # pause between sentences (s)
OPENING_LEAD = 0.50              # silence before the first spoken word
COVER_PAUSE = 0.90               # pause after the cover word before body narration
TAIL = 0.45                      # pause at the end of each scene (s)
COVER_MIN = 3.0                  # cover stays visible long enough to read the word
OUTRO = 2.4                      # end card length (s)
XFADE = 0.5                      # crossfade between scenes (s)
MUSIC_VOLUME = 0.12
VOICE = "fa-IR-FaridNeural"      # Persian voice (Farid)

BRAND_NAME = "آقای استمرار | Mr.Continuity"

# پالت رنگی حرفه‌ای: سبز لجنی/زیتونی بسیار تیره و عمیق در ترکیب با مشکی کربنی و طلایی پرمیوم
BACKGROUND = "#09120B"
BACKGROUND_CSS = (
    "radial-gradient(circle at 50% 30%, rgba(30,55,35,0.7) 0%, rgba(9,18,11,0.96) 60%, #060D08 100%)"
)
BACKGROUND_PATTERN = (
    "radial-gradient(circle at 50% 30%, rgba(255,210,31,0.07) 0%, transparent 70%)"
)
YELLOW = "#FFD21F"

FONT_LINK = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
             '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
             '<link href="https://fonts.googleapis.com/css2?family=Estedad:wght@400;500;600;700;800&display=swap" rel="stylesheet">')
FONT = "'Estedad','Vazirmatn','Tahoma','DejaVu Sans',sans-serif"
FONT_RENDERING = "font-kerning:normal;font-feature-settings:'kern' 1,'liga' 1,'calt' 1;font-synthesis:none;text-rendering:optimizeLegibility;"

# ---------------------------------------------------------------- text
MARKS = re.compile("[\u064B-\u065F\u0670\u0640\u06D6-\u06ED]")


def strip_marks(s):
    return MARKS.sub("", (s or "").replace("\u06C0", "\u0647"))


def norm(s):
    return strip_marks(s).replace("\u200c", "").replace("ي", "ی").replace("ك", "ک")


def parse(text):
    hook, tag, scenes = None, None, []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#") and hook is None:
            hook = line.lstrip("#").strip()
            if "|" in hook:
                hook, tag = [x.strip() for x in hook.split("|", 1)]
            continue
        title, body = "", line
        if "::" in line:
            title, body = [x.strip() for x in line.split("::", 1)]
        scenes.append({"title": title, "text": body})
    if hook is None:
        hook = re.split(r"[.؟?!]", scenes[0]["text"])[0].strip() if scenes else ""
    return hook, tag, scenes


def split_chunks(text, max_chars=72):
    out = []
    L = lambda x: len(strip_marks(x))
    for s in re.split(r"(?<=[\.؟\?!…؛])\s+", text.strip()):
        s = s.strip()
        if not s:
            continue
        if L(s) <= max_chars:
            out.append(s)
            continue
        cur = ""
        for p in re.split(r"(?<=[،,:])\s+", s):
            if cur and L(cur) + 1 + L(p) > max_chars:
                out.append(cur)
                cur = p
            else:
                cur = (cur + " " + p).strip()
        if cur:
            out.append(cur)
    final = []
    for p in out:
        while L(p) > max_chars * 1.5:
            cut = p.rfind(" ", 0, max_chars)
            cut = cut if cut > 15 else max_chars
            final.append(p[:cut].strip())
            p = p[cut:].strip()
        final.append(p)
    
    merged = []
    for part in final:
        part = part.strip()
        if not part:
            continue
        meaningful = re.sub(r"[^\w]", "", part, flags=re.UNICODE)
        if not meaningful:
            if merged:
                merged[-1] += part
            elif final:
                merged.append(part)
            continue
        if len(meaningful) <= 1 and merged:
            merged[-1] = (merged[-1] + " " + part).strip()
        else:
            merged.append(part)

    if len(merged) > 1:
        last_meaningful = re.sub(r"[^\w]", "", merged[-1], flags=re.UNICODE)
        if len(last_meaningful) <= 1:
            merged[-2] = (merged[-2] + " " + merged[-1]).strip()
            merged.pop()

    while len(merged) > 1 and not re.sub(r"[^\w]", "", merged[0], flags=re.UNICODE):
        merged[1] = (merged[0] + merged[1]).strip()
        merged.pop(0)

    return merged


# ---------------------------------------------------------------- Edge-TTS
async def tts(text, out_wav, retries=4):
    text = (text or "").strip()
    if not text:
        raise ValueError("TTS received empty text.")

    out_wav = Path(out_wav)
    last_error = None
    for attempt in range(retries):
        try:
            try:
                out_wav.unlink(missing_ok=True)
            except TypeError:
                if out_wav.exists():
                    out_wav.unlink()

            communicate = edge_tts.Communicate(text, VOICE)
            await asyncio.wait_for(communicate.save(str(out_wav)), timeout=90)
            if not out_wav.exists() or out_wav.stat().st_size < 1024:
                raise RuntimeError("Edge-TTS returned an empty or incomplete audio file.")
            return
        except Exception as exc:
            last_error = exc
            try:
                out_wav.unlink(missing_ok=True)
            except Exception:
                pass
            if attempt + 1 >= retries:
                break
            delay = min(2 ** attempt, 12)
            print(f"[TTS] Attempt {attempt + 1}/{retries} failed: {type(exc).__name__}: {exc}. Retrying in {delay}s...", flush=True)
            await asyncio.sleep(delay)

    raise RuntimeError(
        f"Edge-TTS failed after {retries} attempts for text: {text[:100]!r}. "
        f"Last error: {type(last_error).__name__}: {last_error}"
    ) from last_error


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def to_wav(src, dst, pad):
    run(["ffmpeg", "-y", "-i", str(src), "-ar", "44100", "-ac", "2",
         "-af", f"apad=pad_dur={pad}", str(dst)])


def silence_wav(dst, seconds):
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", f"{seconds:.3f}", str(dst)])


def concat_wavs(paths, dst, d):
    lst = d / (dst.stem + "_list.txt")
    lst.write_text("".join(f"file '{p}'\n" for p in paths), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(dst)])


def tts_safe_text(text):
    kept = []
    for char in (text or ""):
        category = unicodedata.category(char)
        if category[0] in {"L", "N", "M", "P", "Z"}:
            kept.append(char)
    return re.sub(r"\s+", " ", "".join(kept)).strip()


async def build_scene_audio(idx, chunks, d, min_total=0.0, tail=TAIL, lead=0.0):
    wavs, timing, t = [], [], 0.0
    if lead > 0:
        lead_wav = d / f"a{idx:02d}_lead.wav"
        silence_wav(lead_wav, lead)
        wavs.append(lead_wav)
        t += lead
    for j, ch in enumerate(chunks):
        raw_audio = d / f"a{idx:02d}_{j:02d}_raw.wav"
        wav = d / f"a{idx:02d}_{j:02d}.wav"
        spoken = tts_safe_text(ch)
        if spoken:
            await tts(spoken, raw_audio)
            to_wav(raw_audio, wav, tail if j == len(chunks) - 1 else GAP)
        else:
            silence_wav(wav, tail if j == len(chunks) - 1 else GAP)
        dur = duration(wav)
        timing.append((ch, t, t + dur))
        wavs.append(wav)
        t += dur
    if t < min_total:
        extra = d / f"a{idx:02d}_extra.wav"
        silence_wav(extra, min_total - t)
        wavs.append(extra)
        t = min_total
    scene_wav = d / f"scene{idx:02d}.wav"
    concat_wavs(wavs, scene_wav, d)
    return scene_wav, timing, duration(scene_wav)


def icon_kind(text):
    t = norm(text).lower()
    specific_rules = [
        (("هوش مصنوعی", "ai", "ربات", "مدل زبانی", "چت بات", "چتبات", "اسمارت کانترکت", "smart contract"), "robot"),
        (("بلاکچین", "بلاک چین", "ارز دیجیتال", "کریپتو", "رمزارز", "بیت کوین", "بیتکوین", "bitcoin", "btc", "اتریوم", "توکن", "کوین"), "bitcoin"),
        (("چراغ راهنمایی", "چراغ قرمز", "راهنمایی و رانندگی", "traffic light"), "traffic_light"),
        (("دور زدن", "یوترن", "u-turn"), "u_turn"),
        (("علامت ایست", "ایست", "stop sign"), "stop_sign"),
        (("اسکناس", "برگ پول", "banknote", "دلار نقد"), "banknote"),
        (("هواپیما", "پرواز", "فرودگاه", "airplane"), "airplane"),
        (("قطار", "ریل", "train"), "train"),
        (("جاده", "بزرگراه", "اتوبان", "road"), "road"),
        (("بلندگو", "اسپیکر", "speaker"), "speaker"),
        (("موبایل", "گوشی همراه", "تلفن همراه", "smartphone", "mobile"), "mobile"),
        (("موتور", "موتورسیکلت", "motorcycle"), "motorcycle"),
        (("ساختمان", "برج", "آسمان خراش", "building"), "building"),
        (("دمبل", "وزنه", "بدنسازی", "dumbbell"), "dumbbell"),
        (("نقاب", "ماسک", "mask"), "mask"),
        (("گوش", "شنیدن", "شنوایی", "ear"), "ear"),
        (("خواب آلود", "zzz", "نماد خواب"), "sleep_symbol"),
        (("زن", "زنان", "خانم", "دختر", "بانوان"), "woman"),
        (("مرد", "مردان", "آقا", "پسر"), "man"),
        (("تقویم", "ماهانه", "برنامهریزی"), "calendar"),
        (("خورشید", "آفتاب", "طلوع", "غروب"), "sun"),
        (("ماه", "مهتاب", "هلال ماه"), "moon"),
        (("تخت خواب", "تختخواب", "تخت", "اتاق خواب"), "bed"),
        (("خواب", "خوابیدن", "بیخوابی", "استراحت"), "sleep"),
        (("ابزار", "آچار", "چکش", "پیچگوشتی"), "tools"),
        (("سیگار", "تنباکو"), "cigarette"),
        (("پرنده", "کبوتر", "عقاب", "بال پرواز"), "bird"),
        (("شیر", "شیر جنگل"), "lion"),
        (("روباه"), "fox"),
        (("گرگ"), "wolf"),
        (("گوسفند", "بره"), "sheep"),
        (("گاو"), "cow"),
    ]
    for words, kind in specific_rules:
        for word in words:
            term = norm(word)
            if " " in term:
                matched = term in t
            else:
                matched = re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", t) is not None
            if matched:
                return kind

    rules = [
        (("خانه", "مسکن", "منزل"), "home"),
        (("دست دادن", "توافق", "همکاری"), "handshake"),
        (("هشدار", "خطر", "ریسک", "احتیاط"), "warning"),
        (("ستاره", "درخشش", "ویژه"), "star"),
        (("ماشین", "خودرو", "رانندگی"), "car"),
        (("پیگیری", "تماس", "تلفن", "فالوآپ"), "phone"),
        (("تبلیغ", "بازاریابی", "محتواسازی", "کمپین"), "megaphone"),
        (("سیستم سازی", "فرایند", "اتوماسیون", "روال"), "gear"),
        (("رهبری", "رهبر", "مدیر", "مدیریت", "اثرگذاری"), "crown"),
        (("قدم", "گام", "مرحله", "پله"), "stairs"),
        (("ذهن", "فکر", "تمرکز", "هوش", "مغز", "یادگیری"), "brain"),
        (("پول", "ثروت", "درآمد", "مالی", "سرمایه", "فروش", "قیمت", "سود"), "money"),
        (("هدف", "موفقیت", "مسیر", "شروع", "نتیجه"), "target"),
        (("رشد", "پیشرفت", "نمودار", "توسعه", "بهبود", "ساختن"), "growth"),
        (("کتاب", "آموزش", "دانش", "مطالعه", "مهارت"), "book"),
        (("عشق", "رابطه", "خانواده", "اعتماد", "مذاکره", "ارتباط"), "heart"),
        (("زمان", "استمرار", "عادت", "صبر", "پشتکار", "تلاش", "تعهد"), "time"),
        (("سؤال", "سوال", "چرا", "چطور", "چگونه", "مشکل", "چالش"), "question"),
        (("تکنولوژی", "فناوری", "شبکه", "اینترنت", "سیستم"), "network"),
        (("تیم", "گروه", "تیمسازی"), "people"),
        (("ترس", "استرس", "شکست", "انگیزه", "امید"), "heart"),
        (("سلامت", "ورزش", "انرژی", "قدرت"), "growth"),
        (("ایده", "خلاقیت", "راهکار"), "bulb"),
        (("امنیت", "محافظت"), "shield"),
        (("پیام", "محتوا", "گفتگو"), "chat"),
        (("پیروزی", "جایزه", "دستاورد"), "trophy"),
        (("انتخاب", "قطب"), "compass"),
        (("قانون", "عدالت"), "scales"),
        (("برق", "سریع", "شتاب"), "lightning"),
        (("چشم", "آگاهی"), "eye"),
        (("قفل", "محرمانه"), "lock"),
        (("قله", "صعود"), "mountain"),
        (("پرچم", "رکورد"), "flag"),
        (("پیچیده", "حل مسئله"), "puzzle"),
    ]
    for words, kind in rules:
        if any(norm(w) in t for w in words):
            return kind
    choices = ("target", "brain", "network", "book", "people", "growth", "question", "time", "heart", "money")
    return choices[int(hashlib.sha256(t.encode("utf-8")).hexdigest()[:8], 16) % len(choices)]

ICON_LIBRARY = ('brain', 'money', 'target', 'growth', 'book', 'heart', 'time', 'question', 'network', 'people', 'spark', 'bulb', 'shield', 'chat', 'trophy', 'compass', 'scales', 'calendar', 'handshake', 'lightning', 'flag', 'mountain', 'lock', 'eye', 'puzzle', 'woman', 'man', 'sun', 'moon', 'sleep', 'tools', 'bed', 'cigarette', 'bird', 'lion', 'fox', 'wolf', 'sheep', 'cow', 'phone', 'megaphone', 'gear', 'crown', 'stairs', 'home', 'partnership', 'warning', 'star', 'car', 'location', 'robot', 'bitcoin', 'shop', 'tree', 'mask', 'ear', 'sleep_symbol', 'traffic_light', 'banknote', 'road', 'airplane', 'train', 'u_turn', 'stop_sign', 'speaker', 'mobile', 'motorcycle', 'building', 'dumbbell')
ICON_ALTERNATES = {
 "brain": ("book", "bulb", "eye", "puzzle", "network"),
 "target": ("compass", "flag", "mountain", "star", "growth"),
 "growth": ("stairs", "tree", "mountain", "star", "trophy"),
 "book": ("bulb", "brain", "chat", "puzzle", "network"),
 "heart": ("handshake", "people", "partnership", "home", "bird"),
 "time": ("calendar", "sun", "moon", "sleep", "stairs"),
 "question": ("eye", "puzzle", "compass", "brain", "book"),
 "people": ("woman", "man", "handshake", "partnership", "network"),
 "money": ("bitcoin", "growth", "shop", "crown", "scales"),
 "robot": ("network", "brain", "gear", "bulb", "shield"),
 "bitcoin": ("money", "network", "growth", "scales", "lock"),
 "sleep": ("sleep_symbol", "bed", "moon", "calendar", "home", "time"),
 "car": ("road", "traffic_light", "u_turn", "stop_sign", "motorcycle"),
 "phone": ("mobile", "speaker", "chat", "network"),
}

def assign_unique_icon_kinds(texts, reserved=()):
    used = set(reserved); result = []
    for text in texts:
        preferred = icon_kind(text)
        candidates = [preferred, *ICON_ALTERNATES.get(preferred, ())]
        candidates += [k for k in ICON_LIBRARY if k not in candidates]
        chosen = next((k for k in candidates if k not in used), None)
        if chosen is None:
            chosen = next((k for k in candidates if not result or k != result[-1]), preferred)
        result.append(chosen); used.add(chosen)
    return result

def icon_svg(text, color="#FFFFFF", size=300, kind_override=None):
    kind = kind_override or icon_kind(text)
    paths = {
        "robot": "<path d='M60 10v12M54 10h12'/><rect x='24' y='31' width='72' height='58' rx='18'/><path d='M24 49H13v23h11M96 49h11v23H96M39 89v12M81 89v12M45 101h30M39 54h10M71 54h10'/><rect x='43' y='43' width='34' height='23' rx='7'/><circle cx='51' cy='54' r='3' fill='currentColor'/><circle cx='69' cy='54' r='3' fill='currentColor'/><path d='M49 76h22M60 22v9'/>",
        "bitcoin": "<circle cx='60' cy='60' r='49'/><circle cx='60' cy='60' r='40'/><path d='M49 27v66M68 27v66M42 37h25c19 0 22 23 4 26 22 2 20 29 0 29H42M42 37h29M42 89h29'/><path d='M43 18v8M77 18v8M43 94v8M77 94v8'/>",
        "shop": "<path d='M18 48h84v57H18Z'/><path d='m13 48 10-27h74l10 27M13 48c0 10 16 10 16 0 0 10 16 10 16 0 0 10 16 10 16 0 0 10 16 10 16 0'/><path d='M38 105V72h24v33M74 69h16v16H74Z'/>",
        "tree": "<path d='M60 12 27 52h18L22 78h29v29h18V78h29L75 52h18Z'/><path d='M60 82v25'/>",
        "brain": "<path d='M48 20c-13-12-31-2-29 13-13 8-9 27 3 31-5 15 8 27 22 20 5 10 18 9 20-2V22C60 12 51 12 48 20Z'/><path d='M72 20c13-12 31-2 29 13 13 8 9 27-3 31 5 15-8 27-22 20-5 10-18 9-20-2V22c0-10 9-10 16-2Z'/><path d='M38 31c10 1 14 9 12 18M31 58c10-5 18 1 19 10M83 31c-10 1-14 9-12 18M90 58c-10-5-18 1-19 10'/>",
        "money": "<rect x='18' y='30' width='84' height='56' rx='9'/><path d='M18 42h84M78 54h24v22H78a11 11 0 0 1 0-22Z'/><circle cx='83' cy='65' r='3'/>",
        "target": "<circle cx='60' cy='60' r='42'/><circle cx='60' cy='60' r='27'/><circle cx='60' cy='60' r='11'/><path d='M60 60 99 21M83 21h16v16'/>",
        "growth": "<path d='M18 100V20M18 100h86'/><path d='m28 82 23-24 17 12 31-39'/><path d='M81 31h18v18'/>",
        "book": "<path d='M15 25c20-7 34-2 45 8v61C49 83 34 79 15 86Z'/><path d='M105 25c-20-7-34-2-45 8v61c11-11 26-15 45-8Z'/><path d='M29 42c8-2 15 0 22 4M29 55c8-2 15 0 22 4M91 42c-8-2-15 0-22 4M91 55c-8-2-15 0-22 4'/>",
        "heart": "<path d='M60 99 20 61C-3 36 28 9 49 29l11 11 11-11c21-20 52 7 29 32Z'/>",
        "time": "<circle cx='60' cy='60' r='42'/><path d='M60 34v28l20 13'/>",
        "question": "<path d='M38 42c2-27 42-31 48-6 4 18-22 21-25 39'/><circle cx='60' cy='91' r='3'/>",
        "network": "<circle cx='60' cy='22' r='10'/><circle cx='25' cy='86' r='10'/><circle cx='95' cy='86' r='10'/><circle cx='60' cy='62' r='12'/><path d='M55 32 42 52M65 32 78 52M50 70 32 79M70 70 88 79'/>",
        "people": "<circle cx='60' cy='31' r='13'/><circle cx='28' cy='43' r='10'/><circle cx='92' cy='43' r='10'/><path d='M34 95V83c0-15 12-26 26-26s26 11 26 26v12M9 91V78c0-10 8-18 18-18M111 91V78c0-10-8-18-18-18'/>",
        "spark": "<path d='m60 8 12 37 37 15-37 12-12 37-12-37L11 60l37-15Z'/><path d='m97 9 4 12 12 4-12 4-4 12-4-12-12-4 12-4Z'/>",
        "bulb": "<path d='M42 76c-3-9-16-17-16-34a34 34 0 1 1 68 0c0 17-13 25-16 34Z'/><path d='M45 88h30M49 99h22M47 76h26M60 4v10M13 42h10M97 42h10M24 17l8 8M96 17l-8 8'/>",
        "shield": "<path d='M60 10 100 25v29c0 27-17 43-40 56C37 97 20 81 20 54V25Z'/><path d='m40 59 13 13 28-31'/>",
        "chat": "<path d='M18 23h84v58H55L32 99V81H18Z'/><path d='M34 42h52M34 58h38'/>",
        "trophy": "<path d='M36 14h48v26c0 21-10 32-24 32S36 61 36 40Z'/><path d='M36 25H17v13c0 15 10 22 23 22M84 25h19v13c0 15-10 22-23 22M60 72v20M40 104h40M45 92h30'/>",
        "compass": "<circle cx='60' cy='60' r='46'/><path d='m76 34-10 32-32 20 10-32Z'/><circle cx='60' cy='60' r='4'/>",
        "scales": "<path d='M60 16v84M38 30h44M60 16l-7 8h14Z'/><path d='m28 30-16 31h32ZM92 30 76 61h32ZM40 61c-4 13-20 13-28 0M108 61c-8 13-24 13-32 0M42 100h36'/>",
        "calendar": "<rect x='18' y='25' width='84' height='78' rx='8'/><path d='M18 44h84M39 15v20M81 15v20M36 61h12M56 61h12M76 61h12M36 78h12M56 78h12'/>",
        "handshake": "<path d='m12 48 20-20 20 4 10 10 10-8 16 2 20 18-18 22-14-4-12 10-12-5-10 1-18-20Z'/><path d='m42 49 18 16c6 5 13-3 8-8M52 42l22 18c6 5 13-3 8-8M32 28l-9-9M88 36l10-8'/>",
        "lightning": "<path d='M68 8 25 66h28l-5 46 47-65H65Z'/>",
        "flag": "<path d='M25 108V12M27 18h65L78 40l14 20H27'/>",
        "mountain": "<path d='m8 100 35-62 18 28 13-19 38 53Z'/><path d='m30 61 13-23 18 28 13-19 12 19'/>",
        "lock": "<rect x='22' y='49' width='76' height='55' rx='8'/><path d='M38 49V34a22 22 0 0 1 44 0v15M60 68v17'/>",
        "eye": "<path d='M8 60s18-30 52-30 52 30 52 30-18 30-52 30S8 60 8 60Z'/><circle cx='60' cy='60' r='14'/>",
        "puzzle": "<path d='M20 20h25a12 12 0 1 1 24 0h31v31a12 12 0 1 0 0 24v25H69a12 12 0 1 0-24 0H20V69a12 12 0 1 0 0-24Z'/>",
        "woman": "<circle cx='60' cy='29' r='17'/><path d='M35 105V88c0-17 11-29 25-29s25 12 25 29v17M43 45c-8-10-8-25 0-34M77 45c8-10 8-25 0-34M45 71l-13 34M75 71l13 34' />",
        "man": "<circle cx='60' cy='29' r='17'/><path d='M35 105V88c0-17 11-29 25-29s25 12 25 29v17M43 20c3-12 31-12 34 0M48 43h24' />",
        "sun": "<circle cx='60' cy='60' r='22'/><path d='M60 8v18M60 94v18M8 60h18M94 60h18M23 23l13 13M84 84l13 13M97 23 84 36M36 84 23 97' />",
        "moon": "<path d='M83 17c-7 4-13 13-15 24-5 25 12 49 37 54-8 10-22 16-37 16-28 0-50-22-50-50S40 11 68 11c5 0 10 2 15 6Z' />",
        "sleep": "<path d='M14 83h92M23 83V58h74v25M23 58l10-17h25l10 17M42 27h16l-16 16h17M75 17h17L75 34h18' />",
        "tools": "<path d='M22 18a25 25 0 0 0 31 31l43 43-14 14-43-43A25 25 0 0 1 22 18Z'/><path d='m75 19 8-8a19 19 0 0 1 24 24l-18 18-14-14 18-18' />",
        "bed": "<path d='M12 90V35M12 70h96v20M108 90V52M12 58h34c11 0 17 5 17 12M26 58V43h22c10 0 15 6 15 15M12 104v-14M108 104v-14' />",
        "cigarette": "<path d='m23 82 67-39 13 22-67 39Z'/><path d='m79 50 13 22M18 91l12 20M72 29c-9-10 7-13 0-24M92 25c-9-10 7-13 0-24' />",
        "bird": "<path d='M18 75c10-26 31-40 51-34 12 4 18 14 22 25l17 7-17 8c-10 18-31 24-50 15-10-5-18-12-23-21Z'/><path d='m88 53 20-7-11 18M49 95v12M70 95v12M40 65c12-10 25-11 38-4' />",
        "lion": "<path d='M60 9 72 18 88 15 94 31 108 41 101 58 108 75 93 86 88 103 70 100 60 112 48 100 30 103 25 86 10 75 17 58 10 41 26 31 31 15 48 18Z'/><path d='M39 51q8-8 16 0M65 51q8-8 16 0M55 67l5 5 5-5M60 72v10M48 86q12 9 24 0' />",
        "fox": "<path d='M20 23 46 35 60 24 74 35 100 23 94 72 60 105 26 72Z'/><path d='M39 57l14 5M81 57l-14 5M53 76l7 7 7-7M60 83v8' />",
        "wolf": "<path d='M17 18 43 32 60 25 77 32 103 18 98 66 84 91 60 108 36 91 22 66Z'/><path d='M39 55l14 5M81 55l-14 5M53 75l7 7 7-7M60 82l-9 9M60 82l9 9M46 38l-8-11M74 38l8-11' />",
        "sheep": "<path d='M35 45c-13-12 1-30 16-21 7-17 29-13 31 3 17-5 27 13 16 25 15 14 3 32-12 29-8 17-29 17-36 2-17 8-31-9-21-23-14-3-15-24 6-15Z'/><path d='M48 52h24c12 0 17 9 14 21l-8 20H42l-8-20c-3-12 2-21 14-21Z'/><path d='M49 65h1M71 65h1M55 79q5 5 10 0M42 91v15M78 91v15' />",
        "cow": "<path d='M30 35 13 18 11 51 28 59M90 35l17-17 2 33-17 8M34 43c0-18 12-28 26-28s26 10 26 28v40c0 16-12 27-26 27S34 99 34 83Z'/><path d='M44 58h1M75 58h1M44 82c8 12 24 12 32 0M48 82v8M72 82v8' />",
        "phone": "<path d='M34 15 20 29c-5 5 1 25 22 46s41 27 46 22l14-14-22-20-13 12c-9-5-17-13-22-22l12-13Z'/><path d='M70 18c17 4 28 15 32 32M70 34c9 2 14 7 16 16'/>",
        "megaphone": "<path d='M15 48h22l57-25v74L37 72H15Z'/><path d='m37 72 9 28h19L54 79M15 48v24M105 44l9-8M105 72l9 8'/>",
        "gear": "<path d='M52 10h16l4 13 10 4 11-7 12 12-7 11 4 10 13 4v16l-13 4-4 10 7 11-12 12-11-7-10 4-4 13H52l-4-13-10-4-11 7-12-12 7-11-4-10-13-4V54l13-4 4-10-7-11 12-12 11 7 10-4Z'/><circle cx='60' cy='62' r='19'/>",
        "crown": "<path d='m14 36 25 19 21-34 21 34 25-19-10 61H24Z'/><path d='M24 84h72M24 97h72'/><circle cx='14' cy='31' r='6'/><circle cx='60' cy='17' r='6'/><circle cx='106' cy='31' r='6'/>",
        "stairs": "<path d='M15 100V80h22V60h22V40h22V20h24'/><path d='M78 20h27v27M105 20 70 55'/><path d='M15 109h90'/>",
        "home": "<path d='m10 55 50-42 50 42M22 48v58h76V48M48 106V70h24v36'/>",
        "partnership": "<path d='m12 45 22-22 26 14 26-14 22 22-20 36-20-8-20 8Z'/><path d='m34 23 18 22 15-8 19 8M40 67l13 13M55 59l17 17M70 55l15 14'/>",
        "warning": "<path d='M60 12 110 104H10Z'/><path d='M60 42v29'/><circle cx='60' cy='86' r='4' fill='currentColor'/>",
        "star": "<path d='m60 10 15 32 35 5-25 24 6 36-31-17-31 17 6-36L10 47l35-5Z'/>",
        "car": "<path d='m18 70 10-30h64l10 30v25H18Z'/><path d='M28 40 40 20h40l12 20M18 70h84M32 95v10M88 95v10'/><circle cx='34' cy='75' r='7'/><circle cx='86' cy='75' r='7'/>",
        "mask": "<path d='M18 38Q60 8 102 38v28Q60 108 18 66Z'/><path d='M28 51q14-12 28 0M64 51q14-12 28 0M35 72q25 17 50 0'/><path d='M18 42 9 35M102 42l9-7' />",
        "ear": "<path d='M31 61V48c0-21 13-35 31-35s31 14 31 34c0 17-10 23-19 32-6 6-7 15-15 20-11 7-24-1-24-13'/><path d='M48 51c0-9 6-15 14-15s14 6 14 14c0 9-7 13-13 19-4 4-4 10-9 13'/><path d='M42 91q-2 12 9 16' />",
        "sleep_symbol": "<path d='M18 28h34L18 61h35M65 16h37L65 53h37M48 76h55L48 111h55'/><path d='m20 12 3 8 8 3-8 3-3 8-3-8-8-3 8-3Z'/> ",
        "traffic_light": "<rect x='37' y='10' width='46' height='91' rx='12'/><circle cx='60' cy='31' r='10'/><circle cx='60' cy='56' r='10'/><circle cx='60' cy='81' r='10'/><path d='M60 101v10M48 111h24'/>",
        "banknote": "<rect x='10' y='27' width='100' height='66' rx='8'/><path d='M22 27q0 14-12 14M98 27q0 14 12 14M22 93q0-14-12-14M98 93q0-14 12-14'/><circle cx='60' cy='60' r='19'/><path d='M66 48c-12-8-22 5-10 11 13 6 3 20-10 11M60 43v34'/>",
        "road": "<path d='M37 110 49 10M83 110 71 10'/><path d='M60 16v17M60 48v18M60 83v18'/><path d='M37 110h46'/>",
        "airplane": "<path d='M60 8 70 51l38 21v12L69 73l-3 27 12 9v6l-18-6-18 6v-6l12-9-3-27-39 11V72l38-21Z'/><path d='M49 49h22'/>",
        "train": "<rect x='25' y='12' width='70' height='86' rx='16'/><path d='M25 62h70M39 28h16v19H39ZM65 28h16v19H65ZM39 98l-10 13M81 98l10 13M39 82h42'/><circle cx='42' cy='70' r='4'/><circle cx='78' cy='70' r='4'/>",
        "u_turn": "<path d='M38 106V48c0-17 10-27 25-27s25 10 25 27v12'/><path d='m70 49 18 18 18-18'/><path d='M38 78h25'/>",
        "stop_sign": "<path d='m39 10 42 0 29 29v42l-29 29H39L10 81V39Z'/><path d='M34 48h52v24H34Z'/><path d='M43 60h34'/>",
        "speaker": "<path d='M14 46h23l42-25v78L37 74H14Z'/><path d='M37 74 46 105h18L55 84'/><path d='M91 43q18 17 0 34M100 31q29 29 0 58'/>",
        "mobile": "<rect x='31' y='9' width='58' height='102' rx='11'/><path d='M45 21h30M53 96h14'/><circle cx='60' cy='84' r='3'/>",
        "motorcycle": "<circle cx='28' cy='86' r='18'/><circle cx='94' cy='86' r='18'/><path d='m28 86 23-34h22l21 34H60L48 65M51 52h-9M72 52l10-12h12M60 86l-9-34'/><path d='M78 36h15'/>",
        "building": "<path d='M18 108V20h84v88M18 42h84M18 64h84M18 86h84'/><path d='M31 30h8M53 30h8M75 30h8M31 52h8M53 52h8M75 52h8M31 74h8M53 74h8M75 74h8M49 108V91h22v17'/>",
        "dumbbell": "<path d='M12 43v34M25 32v56M25 60h70M95 32v56M108 43v34'/><path d='M12 60h13M95 60h13'/>",
        "location": "<path d='M60 110S22 68 22 46a38 38 0 1 1 76 0c0 22-38 64-38 64Z'/><circle cx='60' cy='46' r='13'/>",
    }
    if kind not in paths:
        kind = "spark"
    return (f"<svg width='{size}' height='{size}' viewBox='0 0 120 120' "
            f"fill='none' stroke='{color}' stroke-width='5.5' stroke-linecap='round' "
            f"stroke-linejoin='round' aria-label='{kind}' "
            f"style='filter:drop-shadow(0 12px 25px rgba(0,0,0,.75)) drop-shadow(0 0 24px rgba(255,210,31,.4));animation:iconMotion 2.6s ease-in-out infinite alternate;'>"
            f"<g transform='translate(0 -1)'>{paths[kind]}</g></svg>")

def cover_word(hook):
    t = norm(hook).strip()
    hook_phrases = [
        (("منتظر نمون", "منتظر نمان", "منتظر شرایط", "شروع نکردن", "شروع کن"), "شروع کن"),
        (("کمال گرایی", "کمالگرایی", "بی نقص"), "شروع ناقص"),
        (("کنترل ذهن", "ذهن در شرایط سخت"), "کنترل ذهن"),
        (("مثبت نگه داشتن", "مثبت باش"), "ذهن مثبت"),
        (("اعتماد به نفس", "اعتمادبنفس"), "اعتمادبه‌نفس"),
        (("مدیریت پول", "درآمد بیشتر"), "رشد مالی"),
        (("عادت های درست", "ساختن عادت"), "عادت‌سازی"),
        (("رهبری مستقل", "وابسته‌سازی"), "رهبری مستقل"),
    ]
    for phrases, label in hook_phrases:
        if any(norm(phrase) in t for phrase in phrases):
            return label

    tokens = re.findall(r"[\u0600-\u06FF\w]+(?:‌[\u0600-\u06FF\w]+)?", strip_marks(hook))
    ignored = {"از", "برای", "چطور", "چگونه", "چرا", "اگر", "این", "آن", "یک", "که", "با", "به", "در", "را", "و", "یا", "تا"}
    chosen = [x for x in tokens if norm(x) not in ignored and len(norm(x)) > 1]
    return " ".join(chosen[:2]) if len(chosen) >= 2 else (chosen[0] if chosen else "موضوع مهم")


# ---------------------------------------------------------------- HTML Layouts (Ultra-Professional Alignment)
def bg_html(image_path, idx, total, title):
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:{BACKGROUND}}}"
        f".bg-cinematic {{"
        f"  position: absolute; width: 100%; height: 100%; top: 0; left: 0;"
        f"  background: {BACKGROUND_CSS};"
        f"}}"
        f".glow-orb {{"
        f"  position: absolute; width: 750px; height: 750px; top: 30%; left: 50%; transform: translate(-50%, -50%);"
        f"  background: {BACKGROUND_PATTERN};"
        f"  border-radius: 50%; pointer-events: none;"
        f"}}"
        f"</style></head><body>"
        f"<div class='bg-cinematic'></div>"
        f"<div class='glow-orb'></div>"
        f"</body></html>"
    )


def sub_html(text, icon_override=None):
    text = strip_marks(text)
    n = len(text)
    size = 62 if n < 30 else 56 if n < 48 else 50 if n < 62 else 46

    keyword_groups = ["استمرار", "موفقیت", "ثروت", "هدف", "ذهن", "رشد", "پشتکار", "درآمد", "تلاش",
                      "تیم", "رهبری", "اعتماد", "عادت", "تمرکز", "تصمیم", "انگیزه", "زمان", "استقلال", "مسئولیت", "انعطاف"]
    normalized = norm(text)
    highlighted = next((w for w in keyword_groups if norm(w) in normalized), None)
    if not highlighted:
        stop = {"این", "آن", "برای", "چون", "اما", "اگر", "باید", "باشد", "هست", "است", "شود", "یک", "از", "به", "در", "با", "که", "را", "و"}
        words = re.findall(r"[\u0600-\u06FF]{3,}", text)
        candidates = [w for w in words if norm(w) not in stop]
        if candidates:
            highlighted = max(candidates, key=lambda w: len(norm(w)))

    safe = html.escape(text)
    if highlighted:
        pat = re.compile(re.escape(highlighted), re.IGNORECASE)
        safe, count = pat.subn(lambda m: "<span class='kw'>" + m.group(0) + "</span>", safe, count=1)

    icon = icon_svg(text, YELLOW, 280, icon_override)
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;background:transparent;overflow:hidden}}"
        f"body{{font-family:{FONT};{FONT_RENDERING}}}"
        f".sub-icon{{position:absolute;left:0;right:0;top:600px;height:340px;display:flex;justify-content:center;align-items:center;}}"
        f".sub-icon svg{{width:280px;height:280px;}}"
        f".w{{position:absolute;left:60px;right:60px;top:990px;display:flex;justify-content:center}}"
        f".b{{width:100%;max-width:960px;box-sizing:border-box;background:rgba(9,18,11,0.97);color:#f3f4f6;font-family:{FONT};"
        f"{FONT_RENDERING}font-weight:700;font-size:{size}px;line-height:1.72;padding:34px 46px;"
        f"border-radius:34px;text-align:center;direction:rtl;unicode-bidi:plaintext;letter-spacing:0;"
        f"text-wrap:pretty;overflow-wrap:anywhere;border:1.8px solid rgba(255,210,31,0.28);"
        f"box-shadow:0 30px 70px rgba(0,0,0,.85),inset 0 1px 0 rgba(255,255,255,.15);}}"
        f".kw{{color:{YELLOW};font-weight:800;text-shadow:0 0 18px rgba(255,210,31,0.4)}}"
        f".brand{{position:absolute;left:0;right:0;top:1530px;text-align:center;color:{YELLOW};"
        f"font-family:{FONT};{FONT_RENDERING}font-size:34px;font-weight:700;letter-spacing:0;opacity:0.95;text-shadow:0 0 18px rgba(255,210,31,0.35)}}"
        f"@keyframes iconMotion {{"
        f"  0% {{ transform: scale(0.85) translateY(12px); opacity: 0; filter: drop-shadow(0 0 0px rgba(255,210,31,0)); }}"
        f"  50% {{ transform: scale(1.06) translateY(-4px); opacity: 1; }}"
        f"  100% {{ transform: scale(1.0) translateY(0); opacity: 1; filter: drop-shadow(0 12px 25px rgba(0,0,0,.75)) drop-shadow(0 0 24px rgba(255,210,31,.4)); }}"
        f"}}"
        f"</style></head><body>"
        f"<div class='sub-icon'>{icon}</div>"
        f"<div class='w'><div class='b'>{safe}</div></div>"
        f"<div class='brand'>{html.escape(BRAND_NAME)}</div>"
        f"</body></html>"
    )


def cover_html(hook, tag=None, mascot=False, cover_image=None, cover_variant=0):
    word = cover_word(hook)
    accent = YELLOW
    glow_a = "rgba(255,210,31,.25)"
    cover_icon = icon_svg(hook + " " + word, color=YELLOW, size=340 + (cover_variant % 3) * 20)
    layout_shift = (cover_variant % 4)
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:{BACKGROUND}}}"
        f"body{{font-family:{FONT};{FONT_RENDERING}color:#fff;position:relative;text-align:center;"
        f"display:flex;flex-direction:column;align-items:center;justify-content:center}}"
        f".bg-cinematic{{position:absolute;inset:0;background:{BACKGROUND_CSS};pointer-events:none;}}"
        f".glow-orb{{position:absolute;width:850px;height:850px;top:30%;left:50%;transform:translate(-50%,-50%);background:{BACKGROUND_PATTERN};border-radius:50%;pointer-events:none;}}"
        f".visual{{width:540px;height:520px;display:flex;align-items:center;justify-content:center;z-index:2;"
        f"margin-top:-55px;position:relative}}"
        f".cover-icon{{width:{340 + (cover_variant % 3)*20}px;height:{340 + (cover_variant % 3)*20}px;}}"
        f".word{{z-index:2;color:{accent};font-weight:800;font-size:118px;line-height:1.42;max-width:920px;"
        f"padding:0 48px;text-wrap:balance;text-shadow:0 8px 40px {glow_a};margin-top:{-4 + layout_shift*9}px}}"
        f".rule{{width:{110 + layout_shift*22}px;height:4px;border-radius:8px;background:linear-gradient(90deg,transparent,{accent},transparent);margin-top:25px;z-index:2}}"
        f".brand{{position:absolute;bottom:112px;left:0;right:0;color:{accent};font-family:{FONT};{FONT_RENDERING}"
        f"font-size:35px;font-weight:700;letter-spacing:0;opacity:.95;text-shadow:0 0 18px {glow_a};z-index:2}}"
        f"@keyframes iconMotion {{"
        f"  0% {{ transform: scale(0.85) translateY(15px); opacity: 0; }}"
        f"  100% {{ transform: scale(1) translateY(0); opacity: 1; }}"
        f"}}"
        f"</style></head><body>"
        f"<div class='bg-cinematic'></div><div class='glow-orb'></div>"
        f"<div class='visual'>{cover_icon}</div><div class='word'>{html.escape(word)}</div><div class='rule'></div>"
        f"<div class='brand'>{html.escape(BRAND_NAME)}</div></body></html>"
    )


def outro_html(mascot=False):
    slogan = "با آقای استمرار، انگیزه بساز"
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:{BACKGROUND}}}"
        f"body{{font-family:{FONT};{FONT_RENDERING}color:{YELLOW};display:flex;align-items:center;justify-content:center;position:relative;"
        f"background-image:{BACKGROUND_CSS};"
        f"text-align:center}}.wrap{{width:980px;display:flex;flex-direction:column;align-items:center;"
        f"justify-content:center;gap:30px}}"
        f".slogan{{font-family:{FONT};{FONT_RENDERING}font-size:68px;font-weight:800;line-height:1.55;color:{YELLOW};"
        f"white-space:nowrap;letter-spacing:0;text-shadow:0 0 38px rgba(255,210,31,0.4)}}"
        f".brand{{font-family:{FONT};{FONT_RENDERING}font-size:40px;font-weight:700;color:{YELLOW};line-height:1.6;letter-spacing:0;text-shadow:0 0 20px rgba(255,210,31,.3)}}"
        f"</style></head><body><div class='wrap'>"
        f"<div class='slogan'>{html.escape(slogan)}</div>"
        f"<div class='brand'>{html.escape(BRAND_NAME)}</div>"
        f"</div></body></html>"
    )


async def render_pages(jobs):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": W, "height": H})
            for content, out, transparent in jobs:
                await page.set_content(content, wait_until="networkidle")
                await page.evaluate("document.fonts.ready.then(() => true)")
                await page.screenshot(path=str(out), omit_background=transparent)
        finally:
            await browser.close()


def scene_video(idx, bg_png, subs, dur, is_last, out, mascots=()):
    length = dur + (0 if is_last else XFADE)
    frames = int(length * FPS) + 2
    inc = 0.08 / frames
    z = f"min(zoom+{inc:.6f},1.08)"
    
    cmd = ["ffmpeg", "-y", "-i", str(bg_png)]
    for png, _, _ in list(mascots) + list(subs):
        cmd += ["-loop", "1", "-t", f"{length:.3f}", "-i", str(png)]

    fc = (f"[0:v]scale={W*2}:{H*2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':"
          f"y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p[v0]")
    last = "v0"
    base = len(mascots)
    for k, (_, a, b) in enumerate(subs, start=base + 1):
        fade_d = min(0.22, max(0.10, (b-a)/4.0))
        fc += (f";[{k}:v]format=rgba,fade=t=in:st={a:.3f}:d={fade_d:.3f}:alpha=1[ov{k}]"
               f";[{last}][ov{k}]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[v{k}]")
        last = f"v{k}"

    cmd += ["-filter_complex", fc, "-map", f"[{last}]", "-t", f"{length:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)]
    run(cmd)


def pick_music(seed_text):
    here = Path(__file__).resolve().parent
    files = sorted([*here.glob("music/*.mp3"), *here.glob("music/*.wav"),
                    *here.glob("*.mp3"), *here.glob("*.wav")])
    if not files:
        return None
    rnd = random.Random(int(hashlib.md5(seed_text.encode()).hexdigest(), 16))
    return rnd.choice(files)


def assemble(clips, durs, voice_wav, music, out):
    total = sum(durs)
    cmd = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", str(c)]
    cmd += ["-i", str(voice_wav)]
    vi = len(clips)
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
    fc, last, start = "", "[0:v]", 0.0
    for i in range(1, len(clips)):
        start += durs[i - 1]
        fc += (f"{last}[{i}:v]xfade=transition=fade:duration={XFADE}:"
               f"offset={start:.3f}[x{i}];")
        last = f"[x{i}]"
    fc += f"{last}null[vout];" if len(clips) > 1 else "[0:v]null[vout];"
    if music:
        fc += (f"[{vi+1}:a]volume={MUSIC_VOLUME},afade=t=in:d=1,"
               f"afade=t=out:st={max(total-1.5,0):.3f}:d=1.5[m];"
               f"[{vi+1}:a][m]amix=inputs=2:duration=first:normalize=0[mix];"
               f"[mix]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
    else:
        fc += f"[{vi}:a]loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
    cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]",
            "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    run(cmd)


async def build(hook, scenes, d, dst, tag=None):
    shots = []
    for i, sc in enumerate(scenes, start=1):
        chunks = split_chunks(sc["text"])
        if chunks:
            shots.append({"scene": i, "title": sc.get("title", ""), "chunks": chunks})
    if not shots:
        raise ValueError("Input file has no readable scenes.")

    cover_spoken_word = cover_word(hook)
    audio = [await build_scene_audio(0, [cover_spoken_word], d, min_total=COVER_MIN,
                                     tail=COVER_PAUSE, lead=OPENING_LEAD)]
    for k, sh in enumerate(shots, start=1):
        audio.append(await build_scene_audio(k, sh["chunks"], d,
                                             tail=TAIL if k == len(shots) else GAP))
    outro_wav = d / "outro.wav"
    silence_wav(outro_wav, OUTRO)
    audio.append((outro_wav, [], duration(outro_wav)))
    n = len(audio)

    jobs, bg_pngs, sub_pngs = [], [], []
    cover_kind = icon_kind(hook + " " + cover_word(hook))
    all_texts = [txt for sh in shots for txt in sh["chunks"]]
    assigned_kinds = iter(assign_unique_icon_kinds(all_texts, reserved=(cover_kind,)))
    cover_png = d / "cover.png"
    history_dir = dst.parent / ".cover_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    known_hashes = set()
    for old_cover in history_dir.glob("*.png"):
        try:
            known_hashes.add(hashlib.sha256(old_cover.read_bytes()).hexdigest())
        except OSError:
            pass
    cover_variant = random.SystemRandom().randrange(0, 6)
    for attempt in range(24):
        candidate_variant = (cover_variant + attempt) % 24
        candidate_html = cover_html(hook, cover_variant=candidate_variant)
        await render_pages([(candidate_html, cover_png, False)])
        candidate_hash = hashlib.sha256(cover_png.read_bytes()).hexdigest()
        if candidate_hash not in known_hashes:
            cover_variant = candidate_variant
            break
    else:
        raise RuntimeError("Could not create a unique cover after 24 design variations.")
    archive_name = f"{hashlib.sha256((hook + str(random.random())).encode('utf-8')).hexdigest()[:16]}.png"
    (history_dir / archive_name).write_bytes(cover_png.read_bytes())
    bg_pngs.append(cover_png)
    sub_pngs.append([])
    
    for k, sh in enumerate(shots, start=1):
        bp = d / f"bg{k:02d}.png"
        jobs.append((bg_html(None, sh["scene"], len(scenes), sh["title"] or " ".join(sh["chunks"])), bp, False))
        bg_pngs.append(bp)
        row = []
        for j, (txt, a, b) in enumerate(audio[k][1]):
            sp = d / f"sub{k:02d}_{j:02d}.png"
            chosen_kind = next(assigned_kinds)
            jobs.append((sub_html(txt, chosen_kind), sp, True))
            row.append((sp, a, b))
        sub_pngs.append(row)
        
    outro_png = d / "outro.png"
    jobs.append((outro_html(), outro_png, False))
    bg_pngs.append(outro_png)
    sub_pngs.append([])
    await render_pages(jobs)

    clips, durs = [], []
    for i in range(n):
        clip = d / f"clip{i:02d}.mp4"
        dur = audio[i][2]
        scene_video(i, bg_pngs[i], sub_pngs[i], dur, i == n - 1, clip, ())
        clips.append(clip)
        durs.append(dur)
        
    voice = d / "voice.wav"
    concat_wavs([a[0] for a in audio], voice, d)
    music = pick_music(hook)
    dst.parent.mkdir(parents=True, exist_ok=True)
    assemble(clips, durs, voice, music, dst)
    (dst.parent / "cover.png").write_bytes(cover_png.read_bytes())
    print(f"Build completed successfully: {dst}")


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    hook, tag, scenes = parse(src.read_text(encoding="utf-8"))
    if not scenes:
        sys.exit("Input file has no scenes.")
    with tempfile.TemporaryDirectory() as t:
        asyncio.run(build(hook, scenes, Path(t), dst, tag))


if __name__ == "__main__":
    main()
