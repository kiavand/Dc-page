"""
Persian Reel builder v3 (modern look, in-person follow-up content).

Input file (input.txt):
  line starting with "#"        -> cover topic/hook (spoken first, shown on the cover)
  every other non-empty line    -> one scene:   عنوان کوتاه :: متن روایت
                                   (the "عنوان ::" part is optional)
Each sentence is spoken and shown on screen one at a time.
Backgrounds are AI images chosen from the keywords of each scene.

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
GAP = 0.28                       # pause between sentences (s)
TAIL = 0.45                      # pause at the end of each scene (s)
COVER_MIN = 2.6                  # minimum cover length (s)
OUTRO = 2.4                      # end card length (s)
PAGE_HANDLE = ""                 # e.g. "@your_page" - shown on the end card when set
MASCOT_DIR = Path(__file__).resolve().parent / "mascot"   # default.png + optional <mood>.png
XFADE = 0.5                      # crossfade between scenes (s)
MUSIC_VOLUME = 0.12

# ---- fixed cover template: only the hook text changes between reels
COVER_TAG = "نکته‌ی امروز"          # default label; a reel can set its own ("# تیتر | برچسب")
COVER_FOOT = "ذخیره کن  ·  برای تیمت بفرست"

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


async def build_scene_audio(idx, chunks, d, min_total=0.0, tail=TAIL):
    """Return (scene_wav, [(sentence, start, end)], total_duration)."""
    wavs, timing, t = [], [], 0.0
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



def bg_html(image_path, idx, total, title):
    bg = (f"#0b1020 url({img_b64(image_path)}) center/cover no-repeat"
          if image_path else MESH)
    segs = "".join(f"<i class='{'on' if k <= idx else ''}'></i>" for k in range(1, total + 1))
    title = strip_marks(title)
    t = f"<div class='t'><span>{html.escape(title)}</span></div>" if title else ""
    deco = ""
    if not image_path:
        deco = ("<div class='r1'></div><div class='r2'></div><div class='r3'></div>"
                + (f"<div class='big'>{html.escape(title)}</div>" if title else ""))
    return (f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
            f"<style>html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden}}"
            f"body{{background:{bg}}}"
            f".s{{position:absolute;inset:0;background:linear-gradient(to bottom,"
            f"rgba(0,0,0,.38),rgba(0,0,0,0) 28%,rgba(0,0,0,0) 50%,rgba(0,0,0,.72))}}"
            f".p{{position:absolute;top:110px;left:70px;right:70px;display:flex;gap:10px;direction:rtl}}"
            f".p i{{flex:1;height:8px;border-radius:6px;background:rgba(255,255,255,.35)}}"
            f".p i.on{{background:#fbbf24}}"
            f".t{{position:absolute;top:190px;left:0;right:0;text-align:center}}"
            f".t span{{display:inline-block;background:rgba(255,255,255,.20);"
            f"border:2px solid rgba(255,255,255,.40);color:#fff;font-family:{FONT};"
            f"font-weight:800;font-size:56px;line-height:1.5;padding:14px 48px;border-radius:60px}}"
            f".r1,.r2,.r3{{position:absolute;border-radius:50%;border:3px solid rgba(255,255,255,.14);left:50%;top:760px;transform:translate(-50%,-50%)}}"
            f".r1{{width:420px;height:420px}}.r2{{width:720px;height:720px}}.r3{{width:1020px;height:1020px;border-color:rgba(251,191,36,.20)}}"
            f".big{{position:absolute;left:0;right:0;top:640px;text-align:center;color:#fff;opacity:.16;font-family:{FONT};font-weight:800;font-size:170px;line-height:1.3;padding:0 60px}}"
            f"</style></head><body><div class='s'></div>{deco}<div class='p'>{segs}</div>{t}</body></html>")


def sub_html(text):
    text = strip_marks(text)
    n = len(text)
    size = 64 if n < 30 else 58 if n < 48 else 52 if n < 62 else 48
    return (f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
            f"<style>html,body{{margin:0;width:{W}px;height:{H}px;background:transparent}}"
            f".w{{position:absolute;left:0;right:0;top:1150px;display:flex;justify-content:center}}"
            f".b{{max-width:880px;background:rgba(10,14,30,.66);color:#fff;font-family:{FONT};"
            f"font-weight:800;font-size:{size}px;line-height:1.75;padding:28px 50px;"
            f"border-radius:40px;text-align:center;border-right:12px solid #fbbf24}}"
            f"</style></head><body><div class='w'><div class='b'>{html.escape(text)}</div></div></body></html>")


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
    
    hook, tag = strip_marks(hook), strip_marks(tag or COVER_TAG)
    cover_bg = (
        f"background-image:linear-gradient(rgba(0,0,0,.48),rgba(0,0,0,.78)),"
        f"url('data:image/jpeg;base64,{base64.b64encode(cover_image.read_bytes()).decode()}');"
        if cover_image and cover_image.exists()
        else f"background:{MESH};"
    )
    n = len(hook)

    size = 112 if n < 30 else 94 if n < 55 else 80

    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head>"
        f"<meta charset='utf-8'>{FONT_LINK}"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden}}"
        f"body{{background:{MESH};font-family:{FONT};color:#fff;"
        f"display:flex;flex-direction:column;align-items:center;"
        f"justify-content:center;text-align:center;position:relative}}"
        f".tag{{background:#facc15;color:#080808;font-weight:800;"
        f"font-size:46px;padding:12px 46px;border-radius:60px;"
        f"margin-bottom:50px}}"
        f".card{{margin:0 70px;padding:80px 56px;border-radius:50px;"
        f"background:rgba(255,255,255,.06);"
        f"border:2px solid rgba(250,204,21,.55);"
        f"box-shadow:0 20px 80px rgba(0,0,0,.35)}}"
        f".h{{font-weight:800;font-size:{size}px;line-height:1.6}}"
        f".bar{{width:220px;height:14px;background:#facc15;"
        f"border-radius:8px;margin:44px auto 0}}"
        f".f{{position:absolute;bottom:130px;left:0;right:0;"
        f"font-size:44px;font-weight:500;color:#fff}}"
        f"</style></head><body>"
        f"<div class='tag'>{html.escape(tag)}</div>"
        f"<div class='card'><div class='h'>{html.escape(hook)}</div>"
        f"<div class='bar'></div></div>"
        f"<div class='f'>{html.escape(COVER_FOOT)}</div>"
        f"</body></html>"
    )



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
    shots = plan_shots(scenes)
    found_poses = scan_mascots()
    use_mascot = bool(found_poses)
    print('MASCOT: found=' + (','.join(found_poses) if found_poses else 'none'))

    # 1) audio: cover first, then every shot
    audio = [await build_scene_audio(0, [hook], d, min_total=COVER_MIN)]
    for k, sh in enumerate(shots, start=1):
        end_of_scene = k == len(shots) or shots[k]["scene"] != sh["scene"]
        audio.append(await build_scene_audio(k, sh["chunks"], d,
                                             tail=TAIL if end_of_scene else GAP))

    outro_wav = d / "outro.wav"
    silence_wav(outro_wav, OUTRO)
    audio.append((outro_wav, [], duration(outro_wav)))
    n = len(audio)

    # Generate a dedicated AI image for the cover
    cover_image = d / "cover_ai.jpg"
    cover_prompt = (
        image_prompt(hook, hook)
        + ", cinematic editorial cover image, dramatic black background, "
        "golden yellow highlights, strong visual subject, "
        "high contrast, no text, no letters, no logos"
    )
    cover_ok = await asyncio.to_thread(
        fetch_image, cover_prompt, cover_image, 2026
    )
    print("COVER AI IMAGE:", "generated" if cover_ok else "fallback")
  
    # 2) AI photos (two at a time)
    sem = asyncio.Semaphore(2)

    async def get(k, sh):
        path = d / f"ai{k:02d}.jpg"
        async with sem:
            ok = await asyncio.to_thread(fetch_image, sh["prompt"], path, k * 7 + 3)
        return path if ok else None

    images = [None] + list(await asyncio.gather(
        *[get(k, sh) for k, sh in enumerate(shots, start=1)]))
    report = []
    for k, sh in enumerate(shots, start=1):
        print(f"shot {k} (scene {sh['scene']}): {sh['prompt']}")
        report.append("AI image" if images[k] else "FALLBACK background")

    # 3) render all pictures in one browser session
    jobs, bg_pngs, sub_pngs = [], [], []
    cover_png = d / "cover.png"
    jobs.append((cover_html(hook, tag, use_mascot), cover_png, False))
    bg_pngs.append(cover_png)
    sub_pngs.append([])
    mascot_rows = [[]]
    mood_png = {}
    for k in range(1, len(shots) + 1):
        for m, _, _ in mood_runs(audio[k][1]):
            if use_mascot and m not in mood_png:
                mood_png[m] = d / f"mas_{m}.png"
                jobs.append((mascot_layer_html(m), mood_png[m], True))
    for k, sh in enumerate(shots, start=1):
        bp = d / f"bg{k:02d}.png"
        jobs.append((bg_html(images[k], sh["scene"], len(scenes), sh["title"]), bp, False))
        bg_pngs.append(bp)
        row = []
        for j, (txt, a, b) in enumerate(audio[k][1]):
            sp = d / f"sub{k:02d}_{j:02d}.png"
            jobs.append((sub_html(txt), sp, True))
            row.append((sp, a, b))
        sub_pngs.append(row)
        mascot_rows.append([(mood_png[m], a, b) for m, a, b in mood_runs(audio[k][1])] if use_mascot else [])
    print("MASCOT: used=" + (",".join(sorted(mood_png)) if mood_png else "none"))
    outro_png = d / "outro.png"
    jobs.append((outro_html(use_mascot), outro_png, False))
    bg_pngs.append(outro_png)
    sub_pngs.append([])
    mascot_rows.append([])
    await asyncio.to_thread(render_pages, jobs)

    # 4) one clip per shot, joined with crossfades
    clips, durs = [], []
    for i in range(n):
        clip = d / f"clip{i:02d}.mp4"
        dur = audio[i][2]
        scene_video(i, bg_pngs[i], sub_pngs[i], dur, i == n - 1, clip, mascot_rows[i])
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
    for k, r in enumerate(report, start=1):
        print(f"shot {k}: {r}")
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
