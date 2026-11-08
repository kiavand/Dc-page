"""Persian Reel builder v4.8 (Modern Split Black & White Matte Motion UI).

Input file (input.txt):
  line starting with "#"        -> cover topic/hook (spoken first, shown on the cover)
  every other non-empty line    -> one scene:   عنوان کوتاه :: متن روایت
Each sentence is spoken and shown on screen one at a time.
Backgrounds feature a modern split white and black cinematic layout with motion icons.

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

# ترکیب مدرن دو‌تکه: سفید مات و مشکی کربنی با تفکیک هندسی شیک
BACKGROUND_CSS = (
    "linear-gradient(135deg, #F4F4F6 0%, #E2E2E6 50%, #0A0A0E 50.2%, #030305 100%);"
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
        (("نقاب", "چهره", "پشت نقاب", "ریا"), "mask"),
        (("دوست", "رفیق", "آدم", "ارتباط", "رابطه", "نزدیک"), "people"),
        (("خیر", "صلاح", "مهربان", "دلسوز", "لبخند"), "heart"),
        (("تصمیم", "انتخاب", "هدف"), "target"),
        (("ناامید", "کوچک", "شکست"), "shield"),
        (("موفق", "موفقیت", "رشد", "پیشرفت"), "growth"),
        (("ناراحت", "ناسپاس", "رفتار"), "chat"),
        (("کنترل", "تهدید", "قدرت"), "lock"),
        (("محبت", "لبخند", "دوستی"), "smile"),
        (("ترس", "ترسیدن", "نگرانی"), "warning"),
        (("گوش", "حرف", "شنیدن"), "ear"),
        (("زمان", "طول زمان", "روند"), "time"),
        (("خطرناک", "نقاب", "پشت", "فریب"), "eye"),
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
        (("ذهن", "فکر", "تمرکز", "هوش", "مغز"), "brain"),
        (("پول", "ثروت", "درآمد", "مالی"), "money"),
        (("کتاب", "دانش", "مطالعه"), "book"),
        (("زمان", "عادت", "پشتکار", "تلاش"), "time"),
        (("سوال", "چرا", "چطور", "چگونه"), "question"),
        (("تیم", "رهبری", "همکاری"), "people"),
        (("ایده", "خلاقیت"), "bulb"),
        (("امنیت", "حفاظت"), "shield"),
        (("پیروزی", "دستاورد"), "trophy"),
        (("مسیر", "جهت", "قطب‌نما"), "compass"),
    ]
    for words, kind in rules:
        if any(norm(w) in t for w in words):
            return kind
    choices = ("target", "brain", "people", "growth", "question", "time", "heart", "mask")
    return choices[int(hashlib.sha256(t.encode("utf-8")).hexdigest()[:8], 16) % len(choices)]

ICON_LIBRARY = ('brain', 'money', 'target', 'growth', 'book', 'heart', 'time', 'question', 'network', 'people', 'spark', 'bulb', 'shield', 'chat', 'trophy', 'compass', 'mask', 'ear', 'smile', 'warning', 'eye')
ICON_ALTERNATES = {
 "brain": ("book", "bulb", "eye", "puzzle", "network"),
 "target": ("compass", "flag", "mountain", "star", "growth"),
 "growth": ("stairs", "tree", "mountain", "star", "trophy"),
 "people": ("mask", "ear", "handshake", "partnership", "heart"),
 "heart": ("handshake", "people", "smile", "home", "bird"),
 "time": ("calendar", "sun", "moon", "sleep", "stairs"),
 "mask": ("ear", "eye", "shield", "warning", "people"),
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
        "mask": "<path d='M18 38Q60 8 102 38v28Q60 108 18 66Z'/><path d='M28 51q14-12 28 0M64 51q14-12 28 0M35 72q25 17 50 0'/><path d='M18 42 9 35M102 42l9-7' />",
        "smile": "<circle cx='60' cy='60' r='45'/><circle cx='43' cy='48' r='6' fill='currentColor'/><circle cx='77' cy='48' r='6' fill='currentColor'/><path d='M38 68a25 25 0 0 0 44 0'/>",
        "ear": "<path d='M31 61V48c0-21 13-35 31-35s31 14 31 34c0 17-10 23-19 32-6 6-7 15-15 20-11 7-24-1-24-13'/><path d='M48 51c0-9 6-15 14-15s14 6 14 14c0 9-7 13-13 19-4 4-4 10-9 13'/>",
        "eye": "<path d='M8 60s18-30 52-30 52 30 52 30-18 30-52 30S8 60 8 60Z'/><circle cx='60' cy='60' r='14'/>",
        "heart": "<path d='M60 99 20 61C-3 36 28 9 49 29l11 11 11-11c21-20 52 7 29 32Z'/>",
        "people": "<circle cx='60' cy='31' r='13'/><circle cx='28' cy='43' r='10'/><circle cx='92' cy='43' r='10'/><path d='M34 95V83c0-15 12-26 26-26s26 11 26 26v12'/>",
        "target": "<circle cx='60' cy='60' r='42'/><circle cx='60' cy='60' r='27'/><circle cx='60' cy='60' r='11'/><path d='M60 60 99 21M83 21h16v16'/>",
        "growth": "<path d='M18 100V20M18 100h86'/><path d='m28 82 23-24 17 12 31-39'/><path d='M81 31h18v18'/>",
        "shield": "<path d='M60 10 100 25v29c0 27-17 43-40 56C37 97 20 81 20 54V25Z'/><path d='m40 59 13 13 28-31'/>",
        "chat": "<path d='M18 23h84v58H55L32 99V81H18Z'/><path d='M34 42h52M34 58h38'/>",
        "lock": "<rect x='22' y='49' width='76' height='55' rx='8'/><path d='M38 49V34a22 22 0 0 1 44 0v15M60 68v17'/>",
        "warning": "<path d='M60 12 110 104H10Z'/><path d='M60 42v29'/><circle cx='60' cy='86' r='4' fill='currentColor'/>",
        "brain": "<path d='M48 20c-13-12-31-2-29 13-13 8-9 27 3 31-5 15 8 27 22 20 5 10 18 9 20-2V22C60 12 51 12 48 20Z'/><path d='M72 20c13-12 31-2 29 13 13 8 9 27-3 31 5 15-8 27-22 20-5 10-18 9-20-2V22c0-10 9-10 16-2Z'/>",
        "money": "<rect x='18' y='30' width='84' height='56' rx='9'/><path d='M18 42h84M78 54h24v22H78a11 11 0 0 1 0-22Z'/><circle cx='83' cy='65' r='3'/>",
        "book": "<path d='M15 25c20-7 34-2 45 8v61C49 83 34 79 15 86Z'/><path d='M105 25c-20-7-34-2-45 8v61c11-11 26-15 45-8Z'/>",
        "time": "<circle cx='60' cy='60' r='42'/><path d='M60 34v28l20 13'/>",
        "question": "<path d='M38 42c2-27 42-31 48-6 4 18-22 21-25 39'/><circle cx='60' cy='91' r='3'/>",
        "bulb": "<path d='M42 76c-3-9-16-17-16-34a34 34 0 1 1 68 0c0 17-13 25-16 34Z'/><path d='M45 88h30M49 99h22M47 76h26'/>",
        "trophy": "<path d='M36 14h48v26c0 21-10 32-24 32S36 61 36 40Z'/><path d='M36 25H17v13c0 15 10 22 23 22M84 25h19v13c0 15-10 22-23 22'/>",
        "compass": "<circle cx='60' cy='60' r='46'/><path d='m76 34-10 32-32 20 10-32Z'/>",
        "network": "<circle cx='60' cy='22' r='10'/><circle cx='25' cy='86' r='10'/><circle cx='95' cy='86' r='10'/><circle cx='60' cy='62' r='12'/><path d='M55 32 42 52M65 32 78 52M50 70 32 79M70 70 88 79'/>",
        "spark": "<path d='m60 8 12 37 37 15-37 12-12 37-12-37L11 60l37-15Z'/>"
    }
    if kind not in paths:
        kind = "spark"
    return (f"<svg width='{size}' height='{size}' viewBox='0 0 120 120' "
            f"fill='none' stroke='{color}' stroke-width='5.5' stroke-linecap='round' "
            f"stroke-linejoin='round' aria-label='{kind}' "
            f"style='filter:drop-shadow(0 12px 25px rgba(0,0,0,.6)) drop-shadow(0 0 24px rgba(255,210,31,.4));animation:iconMotion 2.6s ease-in-out infinite alternate;'>"
            f"<g transform='translate(0 -1)'>{paths[kind]}</g></svg>")

def cover_word(hook):
    return "نقاب"


def bg_html(image_path, idx, total, title):
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;"
        f"background:{BACKGROUND_CSS}}}"
        f".split-overlay {{"
        f"  position: absolute; width: 100%; height: 100%; top: 0; left: 0;"
        f"  background: radial-gradient(circle at 50% 30%, rgba(255,255,255,0.15) 0%, transparent 70%);"
        f"  pointer-events: none;"
        f"}}"
        f"</style></head><body>"
        f"<div class='split-overlay'></div>"
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
        f".sub-icon{{position:absolute;left:0;right:0;top:580px;height:340px;display:flex;justify-content:center;align-items:center;}}"
        f".sub-icon svg{{width:280px;height:280px;}}"
        f".w{{position:absolute;left:60px;right:60px;top:990px;display:flex;justify-content:center}}"
        f".b{{width:100%;max-width:960px;box-sizing:border-box;background:rgba(12,12,18,0.96);color:#f3f4f6;font-family:{FONT};"
        f"{FONT_RENDERING}font-weight:700;font-size:{size}px;line-height:1.72;padding:34px 46px;"
        f"border-radius:34px;text-align:center;direction:rtl;unicode-bidi:plaintext;letter-spacing:0;"
        f"text-wrap:pretty;overflow-wrap:anywhere;border:1.8px solid rgba(255,210,31,0.3);text-shadow:0 2px 4px rgba(0,0,0,.6);"
        f"box-shadow:0 30px 70px rgba(0,0,0,.8),inset 0 1px 0 rgba(255,255,255,.15);}}"
        f".kw{{color:{YELLOW};font-weight:800;text-shadow:0 0 18px rgba(255,210,31,0.4)}}"
        f".brand{{position:absolute;left:0;right:0;top:1530px;text-align:center;color:{YELLOW};"
        f"font-family:{FONT};{FONT_RENDERING}font-size:34px;font-weight:700;letter-spacing:0;opacity:0.95;text-shadow:0 0 18px rgba(255,210,31,0.35)}}"
        f"@keyframes iconMotion {{"
        f"  0% {{ transform: scale(0.85) translateY(12px); opacity: 0; filter: drop-shadow(0 0 0px rgba(255,210,31,0)); }}"
        f"  50% {{ transform: scale(1.06) translateY(-4px); opacity: 1; }}"
        f"  100% {{ transform: scale(1.0) translateY(0); opacity: 1; filter: drop-shadow(0 12px 25px rgba(0,0,0,.6)) drop-shadow(0 0 24px rgba(255,210,31,.4)); }}"
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
    glow_a = "rgba(255,210,31,.28)"
    cover_icon = icon_svg(hook + " " + word, color=YELLOW, size=340)
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;"
        f"background:{BACKGROUND_CSS}}}"
        f".visual{{width:540px;height:520px;display:flex;align-items:center;justify-content:center;z-index:2;"
        f"position:absolute;top:280px;left:50%;transform:translateX(-50%)}}"
        f".word{{position:absolute;top:840px;left:0;right:0;z-index:2;color:{accent};font-weight:800;font-size:118px;line-height:1.42;max-width:920px;"
        f"margin:0 auto;text-align:center;padding:0 48px;text-wrap:balance;text-shadow:0 8px 40px {glow_a}}}"
        f".rule{{position:absolute;top:1000px;left:50%;transform:translateX(-50%);width:130px;height:4px;border-radius:8px;background:linear-gradient(90deg,transparent,{accent},transparent);z-index:2}}"
        f".brand{{position:absolute;bottom:112px;left:0;right:0;color:{accent};font-family:{FONT};{FONT_RENDERING}"
        f"font-size:35px;font-weight:700;letter-spacing:0;opacity:.95;text-shadow:0 0 18px {glow_a};z-index:2;text-align:center}}"
        f"</style></head><body>"
        f"<div class='visual'>{cover_icon}</div><div class='word'>{html.escape(word)}</div><div class='rule'></div>"
        f"<div class='brand'>{html.escape(BRAND_NAME)}</div></body></html>"
    )


def outro_html(mascot=False):
    slogan = "با آقای استمرار، انگیزه بساز"
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;"
        f"background:{BACKGROUND_CSS}}}"
        f"body{{font-family:{FONT};{FONT_RENDERING}color:{YELLOW};display:flex;align-items:center;justify-content:center;position:relative;"
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
