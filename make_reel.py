"""
Persian Reel builder v3 (modern look, in-person follow-up content).

Input file (input.txt):
  line starting with "#"        -> cover topic/hook (spoken first, shown on the cover)
  every other non-empty line    -> one scene:   عنوان کوتاه :: متن روایت
                                   (the "عنوان ::" part is optional)
Each sentence is spoken and shown on screen one at a time.
Backgrounds are pure black with topic-matched inline vector icons.

Optional music: put royalty-free .mp3/.wav files in the repo (root or "music"
folder); one is mixed quietly under the voice.

Usage: python make_reel.py input.txt out/reel.mp4
Outputs: reel.mp4 and cover.png in the same folder.
"""
import asyncio
import base64
import hashlib
import html
import random
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

W, H, FPS = 1080, 1920, 30
VOICE = "fa-IR-FaridNeural"      # male Persian voice
RATE = "-5%"
USE_DIACRITICS_FOR_VOICE = True   # set False if the voice mispronounces marked text
GAP = 0.32                       # pause between sentences (s)
OPENING_LEAD = 0.50              # silence before the first spoken word
COVER_PAUSE = 0.90               # pause after the cover word before body narration
TAIL = 0.45                      # pause at the end of each scene (s)
COVER_MIN = 3.0                  # cover stays visible long enough to read the word
OUTRO = 2.4                      # end card length (s)
PAGE_HANDLE = ""  # no handle is displayed inside the video
MASCOT_DIR = Path(__file__).resolve().parent / "mascot"   # default.png + optional <mood>.png
XFADE = 0.5                      # crossfade between scenes (s)
MUSIC_VOLUME = 0.12

# ---- fixed cover template: only the hook text changes between reels
COVER_TAG = "آقای استمرار | Mr.Continuity"
BRAND_NAME = "آقای استمرار | Mr.Continuity"
BRAND_HANDLE = ""  # intentionally omitted from all video frames
YELLOW = "#FFD21F"

STYLE = (", realistic photo, natural light, shallow depth of field, high quality, "
         "vertical 9:16, no text, no letters, no logos")
FALLBACK_PROMPT = "candid photo of friendly people talking in a bright modern office, warm light"
MAX_SHOTS = 16                   # most pictures used in one reel
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

FONT_LINK = ('<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:'
             'wght@500;800&display=swap" rel="stylesheet">')
FONT = "'Vazirmatn','Tahoma','DejaVu Sans',sans-serif"

# keyword (Persian, without half-spaces) -> visual prompt. First match wins.
KEYWORDS = [
    (("ثبتنام", "عضویت", "فرم"),
     "candid photo of two people at a desk filling out a registration form together, friendly consultant, bright modern office"),
    (("تیمسازی", "تیم", "همتیم"),
     "candid photo of a diverse team of young people collaborating around a table, smiling, warm light"),
    (("قهوه", "کافه"),
     "candid photo of two people talking face to face over coffee in a cozy cafe"),
    (("دیدار", "حضوری", "ملاقات", "رودرو"),
     "candid photo of two people meeting face to face and shaking hands in a bright modern lounge"),
    (("جلسه",),
     "candid photo of a small team meeting around a table with notes in a bright room"),
    (("گوش", "بپرس", "بشنو", "شنیدن", "حرفها"),
     "candid photo of a person listening attentively to another person in a friendly conversation"),
    (("اعتماد", "همراهی", "کنارش"),
     "candid photo of two people smiling and shaking hands, trust and partnership"),
    (("رهبر", "لیدر", "مربی"),
     "candid photo of a confident mentor guiding a small team at a whiteboard"),
    (("بنویس", "یادداشت", "قدمها", "قدم"),
     "candid photo of two people writing a plan together in a notebook"),
    (("تقویم", "هماهنگ", "زمان"),
     "candid photo of a person writing in a planner next to a coffee cup, morning light"),
    (("پیام", "تماس", "تلفن"),
     "candid photo of a person smiling while talking on the phone, warm light"),
    (("هدف", "رشد", "موفقیت", "شروع"),
     "candid photo of a smiling person looking ahead at the sunrise, hopeful"),
    (("نگرانی", "استرس", "ترس", "تنها"),
     "candid photo of a thoughtful person by a window, soft light"),
    (("رابطه", "صمیمی"),
     "candid photo of two people laughing together, genuine connection"),
    (("پیگیری",),
     "candid photo of two friendly people having a follow-up conversation at a table with a notebook"),
    (("کتاب", "مطالعه", "خواندن"),
     "candid photo of a person reading a book in a cozy room, warm light"),
    (("شکست", "ناامید", "ناامیدی"),
     "candid photo of a person sitting alone and thinking, soft light, hopeful mood"),
    (("تلاش", "پشتکار"),
     "candid photo of a determined person working hard at a desk, morning light"),
    (("پیروزی", "موفق", "جشن"),
     "candid photo of a person celebrating success with arms raised outdoors"),
    (("قله", "کوه"),
     "candid photo of a person hiking up a mountain path at sunrise"),
    (("دوست", "خانواده"),
     "candid photo of friends laughing together outdoors, warm light"),
]


# ---------------------------------------------------------------- text
MARKS = re.compile("[\u064B-\u065F\u0670\u0640\u06D6-\u06ED]")  # harakat, hamza sign, tatweel


def strip_marks(s):
    """Remove diacritics (kasra, hamza sign, ...) so subtitles stay clean."""
    return MARKS.sub("", (s or "").replace("\u06C0", "\u0647"))


def speak_text(s):
    return s if USE_DIACRITICS_FOR_VOICE else strip_marks(s)


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
    """One sentence per chunk; long sentences are split at commas."""
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
    return final


# ---------------------------------------------------------------- AI images
def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def keyword_prompt(title, text):
    for src in (title, text):
        n = norm(src)
        for keys, prompt in KEYWORDS:
            if any(k in n for k in keys):
                return prompt
    return None


def llm_prompt(text):
    ask = ("Write ONE short English image-generation prompt (max 25 words) for a realistic "
           "candid photo that features real people and visually matches this Persian text. No text, no letters. "
           "Reply with only the prompt. Text: " + text)
    q = urllib.parse.quote(ask)
    for base in ("https://text.pollinations.ai/", "https://gen.pollinations.ai/text/"):
        try:
            out = _get(base + q, 45).decode("utf-8", "ignore").strip().splitlines()[0]
            ascii_ratio = sum(c.isascii() for c in out) / max(len(out), 1)
            if 10 < len(out) < 400 and ascii_ratio > 0.9 and not out.startswith(("{", "<")):
                return out
        except Exception as e:
            print("prompt service failed:", e)
    return None


def image_prompt(title, text):
    return keyword_prompt(title, text) or llm_prompt(text) or FALLBACK_PROMPT


def fetch_image(prompt, path, seed):
    q = urllib.parse.quote(prompt + STYLE)
    for attempt in range(3):
        s = seed + attempt * 11
        urls = [
            f"https://image.pollinations.ai/prompt/{q}?width=864&height=1536&nologo=true&seed={s}",
            f"https://gen.pollinations.ai/image/{q}?width=864&height=1536&seed={s}",
        ]
        for u in urls:
            try:
                data = _get(u, 100)
                if len(data) > 5000 and (data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG"):
                    path.write_bytes(data)
                    return True
                print("image service returned a non-image reply")
            except Exception as e:
                print("image service failed:", e)
    return False


# ---------------------------------------------------------------- speech
async def tts(text, out_mp3):
    import edge_tts
    last = None
    for _ in range(3):
        try:
            await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(out_mp3))
            return
        except Exception as e:
            last = e
            await asyncio.sleep(2)
    raise last


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


async def build_scene_audio(idx, chunks, d, min_total=0.0, tail=TAIL, lead=0.0):
    """Return (scene_wav, [(sentence, start, end)], total_duration)."""
    wavs, timing, t = [], [], 0.0
    if lead > 0:
        lead_wav = d / f"a{idx:02d}_lead.wav"
        silence_wav(lead_wav, lead)
        wavs.append(lead_wav)
        t += lead
    for j, ch in enumerate(chunks):
        mp3 = d / f"a{idx:02d}_{j:02d}.mp3"
        wav = d / f"a{idx:02d}_{j:02d}.wav"
        await tts(speak_text(ch), mp3)
        to_wav(mp3, wav, tail if j == len(chunks) - 1 else GAP)
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


# ---------------------------------------------------------------- pictures
def img_b64(path):
    mime = "image/png" if path.suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()



MESH = (
    "radial-gradient(circle at 82% 18%,rgba(250,204,21,.18),transparent 32%),"
    "radial-gradient(circle at 12% 82%,rgba(255,255,255,.06),transparent 38%),"
    "#080808"
)



def icon_kind(text):
    t = norm(text).lower()
    rules = [
        (("ذهن", "فکر", "تمرکز", "هوش", "مغز", "یادگیری", "تصمیمگیری"), "brain"),
        (("پول", "ثروت", "درآمد", "مالی", "سرمایه", "کیفپول", "اقتصاد", "فروش", "قیمت", "بازار", "معامله", "سود", "ضرر", "ریسک"), "money"),
        (("هدف", "موفقیت", "برنامه", "مسیر", "تصمیم", "شروع", "نتیجه", "اولویت"), "target"),
        (("رشد", "پیشرفت", "افزایش", "آمار", "نمودار", "توسعه", "تغییر", "بهبود", "ساختن"), "growth"),
        (("کتاب", "یادگیری", "آموزش", "دانش", "مطالعه", "مهارت", "درس", "تجربه"), "book"),
        (("عشق", "رابطه", "خانواده", "دوستی", "اعتماد", "احترام", "گفتگو", "گفتوگو", "زبانبدن", "مذاکره", "قرارداد", "ارتباط"), "heart"),
        (("زمان", "استمرار", "عادت", "صبر", "پشتکار", "تلاش", "انضباط", "تعهد", "روزانه", "تکرار"), "time"),
        (("سؤال", "سوال", "چرا", "چطور", "چگونه", "پرسش", "مشکل", "اشتباه", "راهحل", "چالش"), "question"),
        (("بلاکچین", "کریپتو", "ارزدیجیتال", "تکنولوژی", "فناوری", "شبکه", "اینترنت", "هوشمصنوعی", "ربات", "سیستم"), "network"),
        (("تیم", "رهبری", "همکاری", "نتورک", "گروه", "مدیریت", "مربی", "جلسه", "تیمسازی"), "people"),
        (("ترس", "نگرانی", "اضطراب", "فشار", "استرس", "شکست", "اعتمادبهنفس", "انگیزه", "امید"), "heart"),
        (("سلامت", "ورزش", "بدن", "انرژی", "خواب", "تغذیه", "قدرت"), "growth"),
        (("ایده", "خلاقیت", "الهام", "راهکار"), "bulb"),
        (("امنیت", "محافظت", "حریم"), "shield"),
        (("پیام", "محتوا", "گفتگو", "صحبت"), "chat"),
        (("پیروزی", "جایزه", "قهرمان", "دستاورد"), "trophy"),
        (("انتخاب", "جهت", "قطب"), "compass"),
        (("قانون", "تعادل", "عدالت", "منصفانه"), "scales"),
        (("تقویم", "مهلت", "زمانبندی", "برنامهریزی"), "calendar"),
        (("توافق", "مشارکت", "دستدادن"), "handshake"),
        (("برق", "سریع", "شتاب", "فوری", "قدرت"), "lightning"),
        (("چشم", "دیدن", "مشاهده", "توجه", "آگاهی"), "eye"),
        (("قفل", "امنیت", "حفاظت", "محرمانه"), "lock"),
        (("قله", "مسیر سخت", "صعود", "بلندپروازی"), "mountain"),
        (("پرچم", "پیشرو", "رکورد", "رقابت"), "flag"),
        (("پیچیده", "ترکیب", "حل مسئله", "راهحل"), "puzzle"),
    ]
    for words, kind in rules:
        if any(norm(w) in t for w in words):
            return kind
    # Unknown topics should not all collapse to the same star/spark icon.
    # Pick a stable, varied fallback from the available semantic shapes.
    choices = ("target", "brain", "network", "book", "people", "growth", "question", "time", "heart", "money")
    return choices[int(hashlib.sha256(t.encode("utf-8")).hexdigest()[:8], 16) % len(choices)]


def icon_svg(text, color="#FFFFFF", size=300, kind_override=None):
    """Inline, dependency-free vector icons selected from the scene topic."""
    kind = kind_override or icon_kind(text)
    paths = {
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
        "flag": "<path d='M25 108V12M27 18h65L78 40l14 20H27'/>",
    }
    return (f"<svg width='{size}' height='{size}' viewBox='0 0 120 120' "
            f"fill='none' stroke='{color}' stroke-width='5.5' stroke-linecap='round' "
            f"stroke-linejoin='round' aria-label='{kind}' "
            f"style='filter:drop-shadow(0 5px 2px rgba(255,255,255,.16)) drop-shadow(0 0 12px rgba(255,255,255,.12))'>"
            f"<g transform='translate(0 -1)'>{paths[kind]}</g></svg>")


def cover_word(hook):
    """Choose a meaningful Persian cover title: one word when clear, otherwise two."""
    t = norm(hook).strip()
    # Topics that can be expressed accurately in one strong word.
    single_word_rules = [
        (("ذهن", "فکر", "تمرکز", "هوش ذهنی"), "ذهن"),
        (("پول", "درآمد", "ثروت", "سرمایه", "هوش مالی", "مدیریت مالی"), "ثروت"),
        (("هدف", "هدفگذاری", "هدف گذاری"), "هدف"),
        (("موفقیت", "موفق", "پیروزی"), "موفقیت"),
        (("آموزش", "یادگیری", "دانش", "مطالعه"), "یادگیری"),
        (("استمرار", "پشتکار", "عادت"), "استمرار"),
        (("رشد", "پیشرفت", "توسعه فردی"), "رشد"),
        (("بلاکچین", "کریپتو", "ارز دیجیتال", "ارزدیجیتال"), "بلاکچین"),
        (("ترس", "نگرانی", "اضطراب"), "ترس"),
        (("زمان", "وقت", "مدیریت زمان"), "زمان"),
        (("انگیزه", "انگیزشی"), "انگیزه"),
        (("اعتماد", "اعتمادسازی"), "اعتماد"),
        (("رهبری", "رهبر", "لیدرشیپ"), "رهبری"),
        (("ارتباطات", "ارتباط", "روابط عمومی", "مهارت ارتباطی"), "ارتباطات"),
        (("نتورک", "شبکه سازی", "شبکهسازی", "تیم سازی", "تیمسازی"), "تیم‌سازی"),
    ]
    for keys, word in single_word_rules:
        if any(norm(k) in t for k in keys):
            # For broad phrases such as "دنیای ارتباطات", keep the useful topic phrase.
            if any(norm(k) in t for k in ("دنیای ارتباطات", "دنیای رابطه", "مهارت ارتباطی", "روابط عمومی")):
                return "دنیای ارتباطات" if "دنیای ارتباطات" in t else ("مهارت ارتباطی" if "مهارت ارتباطی" in t else "ارتباطات")
            return word

    # Strip leading question/filler words, then keep the first two content words.
    tokens = re.findall(r"[\u0600-\u06FF\w]+(?:‌[\u0600-\u06FF\w]+)?", strip_marks(hook))
    ignored = {"از", "برای", "چطور", "چگونه", "چرا", "اگر", "این", "آن", "یک", "که", "با", "به",
               "در", "را", "و", "یا", "تا", "آیا", "چه", "چی", "میشه", "شود", "است", "هست", "درباره"}
    chosen = [x for x in tokens if norm(x) not in ignored and len(norm(x)) > 1]
    if not chosen:
        return "موضوع مهم"
    # A single word is fine only when it is a meaningful topic, not a vague opener like «دنیای».
    vague = {"دنیای", "جهان", "چیز", "موضوع", "راه", "روش", "نکته", "راز", "اصل"}
    if len(chosen) >= 2 and (norm(chosen[0]) in vague or len(chosen[0]) < 4):
        return " ".join(chosen[:2])
    if len(chosen) >= 2 and not any(norm(k) in t for keys, _ in single_word_rules for k in keys):
        return " ".join(chosen[:2])
    return chosen[0]


def bg_html(image_path, idx, total, title):
    """Clean black scene plate; the icon changes together with each subtitle."""
    return (f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>"
            f"<style>html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#000}}</style>"
            f"</head><body></body></html>")


def sub_html(text, icon_override=None):
    """Each subtitle gets its own topic-matched yellow icon and a guaranteed yellow keyword."""
    text = strip_marks(text)
    n = len(text)
    size = 64 if n < 30 else 58 if n < 48 else 52 if n < 62 else 48

    # Prefer known meaningful terms; if none occur, highlight the strongest content word.
    keyword_groups = ["استمرار", "موفقیت", "ثروت", "هدف", "ذهن", "رشد", "پشتکار", "بلاکچین", "درآمد", "تلاش",
                      "نتورک", "تیم", "رهبر", "رهبری", "اعتماد", "ترس", "شکست", "عادت", "تمرکز", "تصمیم",
                      "انگیزه", "یادگیری", "دانش", "زمان", "فرصت", "تغییر", "زندگی", "قدرت", "مسئولیت"]
    normalized = norm(text)
    highlighted = next((w for w in keyword_groups if norm(w) in normalized), None)
    if not highlighted:
        stop = {"این", "آن", "برای", "چون", "اما", "اگر", "باید", "باشد", "هست", "است", "شود", "شده", "کرد", "کنید", "کنیم", "همین", "خیلی", "بیشتر", "کمتر", "وقتی", "چطور", "چگونه", "چرا", "یک", "از", "به", "در", "با", "که", "را", "و", "یا", "تا", "نه", "می", "تواند"}
        words = re.findall(r"[\u0600-\u06FF]{3,}", text)
        candidates = [w for w in words if norm(w) not in stop]
        if candidates:
            highlighted = max(candidates, key=lambda w: len(norm(w)))

    safe = html.escape(text)
    if highlighted:
        pat = re.compile(re.escape(highlighted), re.IGNORECASE)
        safe, count = pat.subn(lambda m: "<span class='kw'>" + m.group(0) + "</span>", safe, count=1)
        if not count:
            highlighted = None

    # Icon is rendered in the subtitle layer, so it changes with every spoken text chunk.
    icon = icon_svg(text, YELLOW, 168, icon_override)
    return (f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
            f"<style>html,body{{margin:0;width:{W}px;height:{H}px;background:transparent;overflow:hidden}}"
            f".sub-icon{{position:absolute;left:0;right:0;top:820px;display:flex;justify-content:center;"
            f"align-items:center;filter:drop-shadow(0 0 18px rgba(255,210,31,.24));}}"
            f".w{{position:absolute;left:60px;right:60px;top:1010px;display:flex;justify-content:center}}"
            f".b{{max-width:920px;background:rgba(0,0,0,.9);color:#fff;font-family:{FONT};"
            f"font-weight:800;font-size:{size}px;line-height:1.65;padding:24px 36px;"
            f"border-radius:28px;text-align:center;border:2px solid rgba(255,255,255,.14)}}"
            f".kw{{color:{YELLOW};text-shadow:0 0 12px rgba(255,210,31,.2)}}"
            f".brand{{position:absolute;left:0;right:0;top:1500px;text-align:center;color:{YELLOW};"
            f"font-family:{FONT};font-size:38px;font-weight:800;line-height:1.55;"
            f"text-shadow:0 0 20px rgba(255,210,31,.22)}}"
            f"</style></head><body><div class='sub-icon'>{icon}</div><div class='w'><div class='b'>{safe}</div></div>"
            f"<div class='brand'>{html.escape(BRAND_NAME)}</div></body></html>")


# ---------------------------------------------------------------- mascot
def sentence_mood(text):
    t = norm(text)
    if "؟" in text or "?" in text or any(w in t for w in ("چرا", "چطور", "چگونه", "آیا", "کدام")):
        return "question"
    if any(w in t for w in ("ذخیره", "بفرست", "دنبال", "کامنت", "نظرت", "اشتراک")):
        return "love"
    if any(w in t for w in ("موفق", "پیروز", "برنده", "تبریک", "رسیدی", "جشن", "قهرمان", "دستاورد")):
        return "celebrate"
    if any(w in t for w in ("ایده", "راز", "نکته", "راهحل", "کلید", "یادبگیر", "بدان")):
        return "idea"
    if any(w in t for w in ("شکست", "ناامید", "سخت", "مشکل", "اشتباه", "تنها", "ترس", "نگران", "استرس", "درد")):
        return "worried"
    if "!" in text or any(w in t for w in ("باورنکردنی", "عالی", "شگفت", "فوقالعاده")):
        return "excited"
    return "talk"


def mood_runs(timing):
    """Merge consecutive sentences with the same mood: [[mood, start, end], ...]."""
    runs = []
    for txt, a, b in timing:
        m = sentence_mood(txt)
        if runs and runs[-1][0] == m:
            runs[-1][2] = b
        else:
            runs.append([m, a, b])
    return runs


POSE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
POSE_NAMES = ("default", "talk", "question", "idea", "excited", "celebrate", "worried", "love")


def scan_mascots():
    """Find pose files (name = mood, any letter case) in the 'mascot' folder or the repo root."""
    found = {}
    for folder in (MASCOT_DIR, MASCOT_DIR.parent):
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix.lower() in POSE_EXT:
                stem = f.stem.strip().lower()
                if stem in POSE_NAMES and stem not in found:
                    found[stem] = f
    return found


def mascot_file(mood):
    found = scan_mascots()
    for name in (mood, "default") + POSE_NAMES:
        if name in found:
            return found[name]
    return None


def key_background(im):
    """Make a flat-colour background transparent (for poses without transparency)."""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = im.size
    work, marker = im.convert("RGB"), (255, 0, 255)
    for pt in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if work.getpixel(pt) != marker:
            ImageDraw.floodfill(work, pt, marker, thresh=45)
    mask = Image.new("L", (w, h), 255)
    px, mp = work.load(), mask.load()
    for y in range(h):
        for x in range(w):
            if px[x, y] == marker:
                mp[x, y] = 0
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
    out = im.convert("RGBA")
    out.putalpha(mask)
    return out


_URI_CACHE = {}


def mascot_uri(mood):
    path = mascot_file(mood)
    if not path:
        return None
    if path in _URI_CACHE:
        return _URI_CACHE[path]
    data = path.read_bytes()
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if im.mode != "RGBA" or im.getchannel("A").getextrema()[0] > 250:
            buf = io.BytesIO()
            key_background(im).save(buf, "PNG")
            data = buf.getvalue()
    except ImportError:
        pass
    uri = "data:image/png;base64," + base64.b64encode(data).decode()
    _URI_CACHE[path] = uri
    return uri


SPARK = ("<svg class='pr' viewBox='0 0 64 64' style='{pos}' width='{sz}' height='{sz}'>"
         "<path d='M32 2 L39 25 L62 32 L39 39 L32 62 L25 39 L2 32 L25 25 Z' fill='{col}'/></svg>")
HEART = ("<svg class='pr' viewBox='0 0 64 64' style='{pos}' width='{sz}' height='{sz}'>"
         "<path d='M32 58 C6 40 2 22 13 13 C21 7 30 11 32 18 C34 11 43 7 51 13 C62 22 58 40 32 58Z' "
         "fill='{col}'/></svg>")
PROPS = {
    "question": "<div class='pr bub' style='left:-60px;top:-70px;width:170px;height:170px;"
                "background:#fbbf24;color:#111;font-size:130px;transform:rotate(-12deg)'>؟</div>",
    "excited": "<div class='pr bub' style='left:-60px;top:-70px;width:160px;height:160px;"
               "background:#fb923c;color:#fff;font-size:130px;transform:rotate(-10deg)'>!</div>"
               + SPARK.format(pos="right:-20px;top:-40px", sz=90, col="#fde047")
               + SPARK.format(pos="right:60px;top:-90px", sz=50, col="#ffffff"),
    "idea": "<svg class='pr' viewBox='0 0 64 64' style='left:-50px;top:-90px' width='190' height='190'>"
            "<circle cx='32' cy='26' r='20' fill='#fde047'/><rect x='24' y='44' width='16' height='10' rx='3' "
            "fill='#facc15'/><rect x='26' y='55' width='12' height='5' rx='2.5' fill='#a16207'/>"
            "<path d='M32 8v-6M10 26H4M60 26h-6M16 10l-4-4M48 10l4-4' stroke='#fde047' stroke-width='3' "
            "stroke-linecap='round'/></svg>",
    "celebrate": SPARK.format(pos="left:-50px;top:-30px", sz=110, col="#fde047")
                 + SPARK.format(pos="right:-30px;top:-70px", sz=130, col="#fbbf24")
                 + SPARK.format(pos="left:60px;top:-100px", sz=60, col="#ffffff")
                 + SPARK.format(pos="right:90px;top:-110px", sz=45, col="#f472b6")
                 + SPARK.format(pos="right:-10px;top:150px", sz=70, col="#60a5fa"),
    "worried": "<svg class='pr' viewBox='0 0 64 64' style='right:10px;top:10px' width='110' height='110'>"
               "<path d='M32 4 C32 4 12 30 12 42 a20 20 0 0 0 40 0 C52 30 32 4 32 4Z' fill='#60a5fa'/>"
               "<path d='M24 40 a8 8 0 0 0 8 8' stroke='#fff' stroke-width='3' fill='none' "
               "stroke-linecap='round' opacity='.7'/></svg>",
    "love": HEART.format(pos="left:-50px;top:-50px", sz=120, col="#ef4444")
            + HEART.format(pos="right:-30px;top:-90px", sz=90, col="#f472b6")
            + HEART.format(pos="left:90px;top:-120px", sz=60, col="#fb7185"),
    "talk": "",
}
MASCOT_CSS = (".mw{position:absolute}.mi{width:100%;display:block}"
              ".pr{position:absolute}"
              ".bub{display:flex;align-items:center;justify-content:center;border-radius:50%;"
              f"font-family:{FONT};font-weight:800;box-shadow:0 8px 24px rgba(0,0,0,.35)}}")


def mascot_block(mood, uri, width=440, style=""):
    if not uri:
        return ""
    tilt = {"question": -5, "worried": 4, "idea": 3}.get(mood, 0)
    filt = "drop-shadow(0 14px 22px rgba(0,0,0,.45))"
    if mood == "worried":
        filt += " saturate(.88) brightness(.95)"
    return (f"<div class='mw' style='width:{width}px;{style}'>"
            f"<img class='mi' src='{uri}' style='transform:rotate({tilt}deg);filter:{filt}'>"
            f"{PROPS.get(mood, '')}</div>")


def mascot_layer_html(mood):
    return (f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
            f"<style>html,body{{margin:0;width:{W}px;height:{H}px;background:transparent}}{MASCOT_CSS}"
            f"</style></head><body>{mascot_block(mood, mascot_uri(mood), 440, 'right:30px;top:440px')}</body></html>")



def cover_html(hook, tag=None, mascot=False, cover_image=None):
    """Minimal black cover: one yellow topic word, matching yellow vector icon, white brand."""
    word = cover_word(hook)
    icon = icon_svg(word, YELLOW, 330)
    return (f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
            f"<style>html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#000}}"
            f"body{{font-family:{FONT};color:#fff;display:flex;flex-direction:column;"
            f"align-items:center;justify-content:center;text-align:center;position:relative}}"
            f".icon{{margin-bottom:36px;display:flex;align-items:center;justify-content:center}}"
            f".word{{color:{YELLOW};font-weight:800;font-size:126px;line-height:1.35;max-width:900px;"
            f"overflow-wrap:anywhere}}"
            f".brand{{position:absolute;bottom:165px;left:0;right:0;font-size:39px;font-weight:800;color:#fff}}"
            f".handle{{display:block;direction:ltr;font-size:31px;margin-top:12px;font-weight:600}}"
            f"</style></head><body><div class='icon'>{icon}</div>"
            f"<div class='word'>{html.escape(word)}</div>"
            f"<div class='brand'>{html.escape(BRAND_NAME)}<span class='handle'>{html.escape(BRAND_HANDLE)}</span></div>"
            f"</body></html>")


def outro_html(mascot=False):
    """Silent end card: exact yellow slogan centered, brand beneath it, no handle."""
    slogan = "با آقای استمرار، انگیزه بساز"
    return (f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
            f"<style>html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#000}}"
            f"body{{font-family:{FONT};color:{YELLOW};display:flex;align-items:center;justify-content:center;"
            f"text-align:center}}.wrap{{width:940px;display:flex;flex-direction:column;align-items:center;"
            f"justify-content:center;gap:42px}}"
            f".slogan{{font-size:94px;font-weight:800;line-height:1.65;color:{YELLOW};"
            f"text-shadow:0 0 26px rgba(255,210,31,.18)}}"
            f".brand{{font-size:48px;font-weight:800;color:{YELLOW};line-height:1.5}}"
            f".slogan{{max-width:100%;overflow-wrap:normal;word-break:normal}}"
            f"@media (max-width:1080px){{.slogan{{font-size:clamp(64px,7.5vw,86px);line-height:1.55}}"
            f".brand{{font-size:clamp(34px,4.2vw,48px)}}}}"
            f"</style></head><body><div class='wrap'>"
            f"<div class='slogan'>{html.escape(slogan)}</div>"
            f"<div class='brand'>{html.escape(BRAND_NAME)}</div>"
            f"</div></body></html>")


def render_pages(jobs):
    """jobs: list of (html_string, out_png, transparent)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for content, out, transparent in jobs:
            page.set_content(content, wait_until="networkidle")
            page.evaluate("document.fonts.ready.then(() => true)")
            page.screenshot(path=str(out), omit_background=transparent)
        browser.close()


# ---------------------------------------------------------------- video
def scene_video(idx, bg_png, subs, dur, is_last, out, mascots=()):
    """bg with slow zoom + timed subtitle overlays. subs: [(png, start, end)]."""
    length = dur + (0 if is_last else XFADE)
    frames = int(length * FPS) + 2
    inc = 0.12 / frames
    if idx % 2 == 0:
        z = f"min(zoom+{inc:.6f},1.12)"
    else:
        z = f"if(eq(on,0),1.12,max(zoom-{inc:.6f},1.0))"
    cmd = ["ffmpeg", "-y", "-i", str(bg_png)]
    for png, _, _ in list(mascots) + list(subs):
        cmd += ["-i", str(png)]
    fc = (f"[0:v]scale={W*2}:{H*2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':"
          f"y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p[v0]")
    last = "v0"
    for k, (_, a, b) in enumerate(mascots, start=1):
        fc += (f";[{last}][{k}:v]overlay=x=0:y='14*sin(2*PI*(t-{a:.3f})/1.8)':"
               f"enable='between(t,{a:.3f},{b:.3f})'[v{k}]")
        last = f"v{k}"
    base = len(mascots)
    for k, (_, a, b) in enumerate(subs, start=base + 1):
        fc += f";[{last}][{k}:v]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[v{k}]"
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
               f"[{vi}:a][m]amix=inputs=2:duration=first:normalize=0[mix];"
               f"[mix]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
    else:
        fc += f"[{vi}:a]loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
    cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]",
            "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    run(cmd)


# ---------------------------------------------------------------- main
def plan_shots(scenes):
    """One shot = consecutive sentences that share one picture.
    A new picture starts when a sentence contains a different keyword."""
    shots = []
    for i, sc in enumerate(scenes, start=1):
        base = image_prompt(sc["title"], sc["text"])
        cur = None
        for ch in split_chunks(sc["text"]):
            prompt = keyword_prompt("", ch) or (cur["prompt"] if cur else base)
            if cur and prompt == cur["prompt"]:
                cur["chunks"].append(ch)
            else:
                cur = {"scene": i, "title": sc["title"], "prompt": prompt, "chunks": [ch]}
                shots.append(cur)
    while len(shots) > MAX_SHOTS:
        cands = [x for x in range(1, len(shots)) if shots[x]["scene"] == shots[x - 1]["scene"]]
        if not cands:
            break
        j = min(cands, key=lambda x: sum(len(c) for c in shots[x]["chunks"]))
        shots[j - 1]["chunks"] += shots[j]["chunks"]
        del shots[j]
    return shots


async def build(hook, scenes, d, dst, tag=None):
    """Build a black-background, vector-icon reel while preserving Farid voice/music."""
    # Use one shot per original scene; each scene is represented by a topic-matched vector icon.
    shots = []
    for i, sc in enumerate(scenes, start=1):
        chunks = split_chunks(sc["text"])
        if chunks:
            shots.append({"scene": i, "title": sc.get("title", ""), "chunks": chunks})
    if not shots:
        raise ValueError("Input file has no readable scenes.")

    # Audio flow intentionally remains cover -> scenes -> silent branded outro.
    cover_spoken_word = cover_word(hook)
    # Read only the word shown on the cover, then leave a clear pause before the main text.
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
    cover_png = d / "cover.png"
    jobs.append((cover_html(hook), cover_png, False))
    bg_pngs.append(cover_png)
    sub_pngs.append([])
    for k, sh in enumerate(shots, start=1):
        bp = d / f"bg{k:02d}.png"
        jobs.append((bg_html(None, sh["scene"], len(scenes), sh["title"] or " ".join(sh["chunks"])), bp, False))
        bg_pngs.append(bp)
        row = []
        recent_icon_kinds = []
        icon_kinds = ("brain", "money", "target", "growth", "book", "heart", "time",
                      "question", "network", "people", "spark", "bulb", "shield", "chat",
                      "trophy", "compass", "scales", "calendar", "handshake", "lightning",
                      "flag", "mountain", "lock", "eye", "puzzle")
        for j, (txt, a, b) in enumerate(audio[k][1]):
            sp = d / f"sub{k:02d}_{j:02d}.png"
            chosen_kind = icon_kind(txt)
            # Avoid repeated icons across the last three subtitles, while keeping semantic matches.
            if chosen_kind in recent_icon_kinds:
                seed = int(hashlib.sha256(norm(txt).encode("utf-8")).hexdigest()[:8], 16)
                alternatives = [kind for kind in icon_kinds if kind not in recent_icon_kinds]
                if alternatives:
                    chosen_kind = alternatives[seed % len(alternatives)]
            jobs.append((sub_html(txt, chosen_kind), sp, True))
            row.append((sp, a, b))
            recent_icon_kinds.append(chosen_kind)
            recent_icon_kinds = recent_icon_kinds[-3:]
        sub_pngs.append(row)
    outro_png = d / "outro.png"
    jobs.append((outro_html(), outro_png, False))
    bg_pngs.append(outro_png)
    sub_pngs.append([])
    await asyncio.to_thread(render_pages, jobs)

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
    print("music:", music.name if music else "none (add .mp3 files to use music)")
    dst.parent.mkdir(parents=True, exist_ok=True)
    assemble(clips, durs, voice, music, dst)
    (dst.parent / "cover.png").write_bytes(cover_png.read_bytes())
    total = sum(durs)
    print(f"Done: {dst} ({n} clips incl. cover, {total:.1f} s)")
    if total > 90:
        print("NOTE: this reel is longer than 90 seconds; shorter reels usually keep viewers better.")


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    hook, tag, scenes = parse(src.read_text(encoding="utf-8"))
    if not scenes:
        sys.exit("Input file has no scenes.")
    with tempfile.TemporaryDirectory() as t:
        asyncio.run(build(hook, scenes, Path(t), dst, tag))


if __name__ == "__main__":
    main()
