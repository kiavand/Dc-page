"""Persian Reel builder v4.3 (Ultra-Professional Cinematic Matte Dark & Precision Alignment).

Input file (input.txt):
  line starting with "#"        -> cover topic/hook (spoken first, shown on the cover)
  every other non-empty line    -> one scene:   عنوان کوتاه :: متن روایت
Each sentence is spoken and shown on screen one at a time.
Backgrounds feature high-end cinematic matte dark aesthetics with pixel-perfect optical alignment.

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
import edge_tts
from pathlib import Path

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
YELLOW = "#FFD21F"

FONT_LINK = ('<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:'
             'wght@500;800&display=swap" rel="stylesheet">')
FONT = "'Vazirmatn','Tahoma','DejaVu Sans',sans-serif"

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
async def tts(text, out_wav):
    text = (text or "").strip()
    if not text:
        raise ValueError("TTS received empty text.")
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(str(out_wav))


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
    wavs, timing, t = [], [], 0.0
    if lead > 0:
        lead_wav = d / f"a{idx:02d}_lead.wav"
        silence_wav(lead_wav, lead)
        wavs.append(lead_wav)
        t += lead
    for j, ch in enumerate(chunks):
        raw_audio = d / f"a{idx:02d}_{j:02d}_raw.wav"
        wav = d / f"a{idx:02d}_{j:02d}.wav"
        await tts(ch, raw_audio)
        to_wav(raw_audio, wav, tail if j == len(chunks) - 1 else GAP)
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


# ---------------------------------------------------------------- smart icons mapping (High-End Vector Precision)
def icon_kind(text):
    t = norm(text).lower()
    rules = [
        (("ذهن", "فکر", "تمرکز", "هوش", "مغز", "یادگیری", "تصمیمگیری", "آرامش خیال"), "brain"),
        (("پول", "ثروت", "درآمد", "مالی", "سرمایه", "اقتصاد"), "money"),
        (("هدف", "موفقیت", "مسیر", "شروع", "نتیجه", "اولویت", "قفل گشودن", "درها"), "target"),
        (("رشد", "پیشرفت", "افزایش", "آمار", "نمودار", "توسعه", "تغییر", "بهبود", "افق", "رشد فردی"), "growth"),
        (("کتاب", "آموزش", "دانش", "مطالعه", "مهارت"), "book"),
        (("عشق", "رابطه", "اعتماد", "ارتباط", "قلب", "جان"), "heart"),
        (("زمان", "استمرار", "تداوم", "عادت", "صبر", "پشتکار", "تلاش", "انضباط", "هر روز"), "time"),
        (("سؤال", "سوال", "چرا", "چطور", "چگونه", "مشکل"), "question"),
        (("تیم", "رهبری", "همکاری", "گروه", "استقلال", "انعطاف", "انسان"), "people"),
        (("ایده", "خلاقیت", "الهام", "راهکار"), "bulb"),
        (("مسئولیت", "تعهد", "پذیرش"), "shield"),
        (("قوانین", "اصول", "ساختار"), "sliders"),
        (("قدرت", "انرژی", "شتاب"), "zap"),
        (("برنده", "جایزه", "افتخار", "قله", "فتح"), "trophy"),
        (("انتخاب", "جهت", "قطب‌نما", "قطب نمای"), "compass"),
        (("برنامه‌ریزی", "تقویم", "اسناد"), "calendar"),
        (("موبایل", "تکنولوژی", "اپلیکیشن"), "mobile"),
        (("صندلی", "مدیریت", "جایگاه"), "chair"),
        (("مغازه", "فروشگاه", "بیزینس", "تجارت"), "shop"),
        (("سکه", "ارزش", "پاداش"), "coin"),
        (("درخت", "ریشه", "پایداری"), "tree"),
        (("خواب", "استراحت", "ریکاوری", "ماه"), "sleep"),
    ]
    for words, kind in rules:
        if any(norm(w) in t for w in words):
            return kind
    choices = ("target", "brain", "growth", "time", "bulb", "compass", "trophy", "shield", "compass")
    return choices[int(hashlib.sha256(t.encode("utf-8")).hexdigest()[:8], 16) % len(choices)]


def icon_svg(text, color="#FFD21F", size=320, kind_override=None):
    kind = kind_override or icon_kind(text)
    paths = {
        "compass": "<circle cx='60' cy='60' r='46' stroke-width='4'/><path d='m76 34-10 32-32 20 10-32Z' fill='rgba(255,210,31,0.12)'/><circle cx='60' cy='60' r='6' fill='#FFD21F'/>",
        "brain": "<path d='M48 18c-14-13-33-2-31 14-14 9-10 29 3 33-5 16 9 29 24 22 5 11 19 10 21-2V20c0-11-10-11-17-2Z'/><path d='M72 18c14-13 33-2 31 14 14 9 10 29-3 33 5 16-9 29-24 22-5 11-19 10-21-2V20c0-11 10-11 17-2Z'/><path d='M60 22v76' stroke-dasharray='3 3'/>",
        "growth": "<path d='M16 104V16M16 104h88'/><path d='m26 84 24-26 18 13 30-42' stroke-width='6'/><path d='M78 29h20v20'/><circle cx='98' cy='29' r='4' fill='#FFD21F'/>",
        "target": "<circle cx='60' cy='60' r='44' stroke-width='4'/><circle cx='60' cy='60' r='28' stroke-width='4'/><circle cx='60' cy='60' r='12' fill='rgba(255,210,31,0.2)'/><path d='M60 60 102 18'/><path d='M84 18h18v18'/>",
        "trophy": "<path d='M34 16h52v26c0 22-11 34-26 34S34 64 34 42Z'/><path d='M34 26H14v12c0 16 11 24 24 24M86 26h20v12c0 16-11 24-24 24'/><path d='M60 76v18M46 94h28' stroke-width='6'/>",
        "money": "<rect x='16' y='28' width='88' height='64' rx='10' stroke-width='4'/><circle cx='60' cy='60' r='16' stroke-width='4'/><path d='M26 40h8M86 80h8'/>",
        "coin": "<ellipse cx='60' cy='60' rx='40' ry='44' stroke-width='5'/><path d='M60 30v60M46 44h28M46 76h28'/><circle cx='60' cy='60' r='30' fill='rgba(255,210,31,0.08)'/>",
        "book": "<path d='M14 26c22-8 36-2 46 8v62C50 86 36 80 14 88Z'/><path d='M106 26c-22-8-36-2-46 8v62c22-6 36-2 46-8Z'/><path d='M60 34v62'/>",
        "heart": "<path d='M60 102 18 60C-4 35 26 8 48 28l12 12 12-12c22-20 52 7 30 32Z' fill='rgba(255,210,31,0.1)'/>",
        "time": "<circle cx='60' cy='60' r='44' stroke-width='4'/><path d='M60 32v28l22 14'/><circle cx='60' cy='60' r='5' fill='#FFD21F'/>",
        "people": "<circle cx='60' cy='28' r='12'/><circle cx='28' cy='42' r='9'/><circle cx='92' cy='42' r='9'/><path d='M36 96V82c0-14 11-25 24-25s24 11 24 25v14'/>",
        "bulb": "<path d='M40 74c-3-9-15-17-15-32a35 35 0 1 1 70 0c0 15-12 23-15 32Z'/><path d='M44 86h32M48 96h24M46 74h28' stroke-width='4'/>",
        "shield": "<path d='M60 10l42 16v30c0 28-18 45-42 58C36 101 18 84 18 56V26Z'/>",
        "mobile": "<rect x='32' y='14' width='56' height='92' rx='10' stroke-width='4'/><path d='M54 24h12M52 100h16' stroke-width='5'/><circle cx='60' cy='88' r='3' fill='#FFD21F'/>",
        "chair": "<path d='M36 34h48v30H36Z'/><path d='M42 64v32M78 64v32M30 46h60M46 34V18h28v16' stroke-width='4'/>",
        "shop": "<path d='M18 46h84v50H18Z'/><path d='M14 46l10-24h72l10 24M44 64h32v32H44Z' stroke-width='4'/>",
        "tree": "<path d='M60 14c-20 0-36 16-36 36 0 12 6 22 16 28v28h40V78c10-6 16-16 16-28 0-20-16-36-36-36Z'/><path d='M52 106h16' stroke-width='6'/>",
        "sleep": "<path d='M76 22a36 36 0 1 1-42 42c4-18 20-30 42-42Z' fill='rgba(255,210,31,0.1)'/><path d='M82 34l12 12M94 34L82 46' stroke-width='3'/>",
        "question": "<path d='M38 40c2-26 42-30 48-6 4 18-22 21-25 38'/><circle cx='60' cy='90' r='4' fill='#FFD21F'/>",
        "sliders": "<path d='M16 30h88M16 60h88M16 90h88M38 20v20M72 50v20M48 80v20' stroke-width='4'/>",
        "zap": "<path d='M68 6L24 64h30l-6 50 50-62H68Z' fill='rgba(255,210,31,0.15)'/>",
        "calendar": "<rect x='18' y='24' width='84' height='80' rx='8' stroke-width='4'/><path d='M18 46h84M38 14v20M82 14v20'/>"
    }
    path_data = paths.get(kind, paths["target"])
    return (f"<svg width='{size}' height='{size}' viewBox='0 0 120 120' "
            f"fill='none' stroke='{color}' stroke-width='4.5' stroke-linecap='round' "
            f"stroke-linejoin='round' class='animated-svg'>"
            f"<g transform='translate(0 -1)'>{path_data}</g></svg>")


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
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#050508}}"
        f".bg-cinematic {{"
        f"  position: absolute; width: 100%; height: 100%; top: 0; left: 0;"
        f"  background: radial-gradient(circle at 50% 35%, rgba(35,35,55,0.7) 0%, rgba(8,8,12,0.99) 80%);"
        f"}}"
        f".glow-orb {{"
        f"  position: absolute; width: 650px; height: 650px; top: 25%; left: 50%; transform: translate(-50%, -50%);"
        f"  background: radial-gradient(circle, rgba(255,210,31,0.08) 0%, rgba(0,0,0,0) 70%);"
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

    icon = icon_svg(text, YELLOW, 300, icon_override)
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;background:transparent;overflow:hidden}}"
        f".sub-icon{{position:absolute;left:0;right:0;top:660px;height:300px;display:flex;justify-content:center;"
        f"align-items:center;}}"
        f".animated-svg {{"
        f"  animation: softPulse 2.4s ease-in-out infinite alternate;"
        f"  filter: drop-shadow(0 0 22px rgba(255,210,31,0.5));"
        f"}}"
        f".w{{position:absolute;left:60px;right:60px;top:990px;display:flex;justify-content:center}}"
        f".b{{width:100%;max-width:960px;background:rgba(14,14,20,0.97);color:#f3f4f6;font-family:{FONT};"
        f"font-weight:800;font-size:{size}px;line-height:1.75;padding:36px 48px;"
        f"border-radius:36px;text-align:center;border:1.8px solid rgba(255,210,31,0.22);"
        f"box-shadow: 0 28px 60px rgba(0,0,0,0.85);}}"
        f".kw{{color:{YELLOW};text-shadow:0 0 20px rgba(255,210,31,0.5)}}"
        f".brand{{position:absolute;left:0;right:0;top:1530px;text-align:center;color:{YELLOW};"
        f"font-family:{FONT};font-size:36px;font-weight:800;letter-spacing:-0.5px;opacity:0.94;text-shadow:0 0 20px rgba(255,210,31,0.3)}}"
        f"@keyframes softPulse {{"
        f"  0% {{ transform: scale(0.93); opacity: 0.85; }}"
        f"  100% {{ transform: scale(1.05); opacity: 1; }}"
        f"}}"
        f"</style></head><body>"
        f"<div class='sub-icon'>{icon}</div>"
        f"<div class='w'><div class='b'>{safe}</div></div>"
        f"<div class='brand'>{html.escape(BRAND_NAME)}</div>"
        f"</body></html>"
    )


def cover_html(hook, tag=None, mascot=False, cover_image=None):
    word = cover_word(hook)
    icon = icon_svg(word, YELLOW, 380)
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#050508}}"
        f"body{{font-family:{FONT};color:#fff;display:flex;flex-direction:column;"
        f"align-items:center;justify-content:center;text-align:center;position:relative}}"
        f".bg-glow{{position:absolute;width:850px;height:850px;background:radial-gradient(circle, rgba(255,210,31,0.08) 0%, rgba(0,0,0,0) 70%);border-radius:50%;}}"
        f".icon{{margin-bottom:50px;display:flex;align-items:center;justify-content:center;z-index:2;height:380px}}"
        f".animated-svg {{ animation: coverPulse 2.4s ease-in-out infinite alternate; filter: drop-shadow(0 0 30px rgba(255,210,31,0.6)); }}"
        f".word{{color:{YELLOW};font-weight:800;font-size:130px;line-height:1.3;max-width:980px;"
        f"text-shadow: 0 0 45px rgba(255,210,31,0.35);z-index:2}}"
        f".brand{{position:absolute;bottom:165px;left:0;right:0;font-size:38px;font-weight:800;letter-spacing:-0.5px;color:#fff;z-index:2;opacity:0.96}}"
        f"@keyframes coverPulse {{"
        f"  0% {{ transform: scale(0.92); }}"
        f"  100% {{ transform: scale(1.06); }}"
        f"}}"
        f"</style></head><body>"
        f"<div class='bg-glow'></div>"
        f"<div class='icon'>{icon}</div>"
        f"<div class='word'>{html.escape(word)}</div>"
        f"<div class='brand'>{html.escape(BRAND_NAME)}</div>"
        f"</body></html>"
    )


def outro_html(mascot=False):
    slogan = "با آقای استمرار، انگیزه بساز"
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#050508}}"
        f"body{{font-family:{FONT};color:{YELLOW};display:flex;align-items:center;justify-content:center;"
        f"text-align:center}}.wrap{{width:980px;display:flex;flex-direction:column;align-items:center;"
        f"justify-content:center;gap:30px}}"
        f".slogan{{font-size:70px;font-weight:800;line-height:1.4;color:{YELLOW};"
        f"white-space:nowrap;text-shadow:0 0 35px rgba(255,210,31,0.4)}}"
        f".brand{{font-size:42px;font-weight:800;color:#f3f4f6;line-height:1.5;letter-spacing:-0.5px}}"
        f"</style></head><body><div class='wrap'>"
        f"<div class='slogan'>{html.escape(slogan)}</div>"
        f"<div class='brand'>{html.escape(BRAND_NAME)}</div>"
        f"</div></body></html>"
    )


def render_pages(jobs):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for content, out, transparent in jobs:
            page.set_content(content, wait_until="networkidle")
            page.evaluate("document.fonts.ready.then(() => true)")
            page.screenshot(path=str(out), omit_background=transparent)
        browser.close()


def scene_video(idx, bg_png, subs, dur, is_last, out, mascots=()):
    length = dur + (0 if is_last else XFADE)
    frames = int(length * FPS) + 2
    inc = 0.08 / frames
    z = f"min(zoom+{inc:.6f},1.08)"
    
    cmd = ["ffmpeg", "-y", "-i", str(bg_png)]
    for png, _, _ in list(mascots) + list(subs):
        cmd += ["-i", str(png)]
        
    fc = (f"[0:v]scale={W*2}:{H*2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':"
          f"y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p[v0]")
    last = "v0"
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
        icon_kinds = ("brain", "money", "target", "growth", "book", "heart", "time", "question", "people", "bulb", "shield", "sliders", "zap", "trophy", "compass", "calendar", "mobile", "chair", "shop", "coin", "tree", "sleep")
        
        for j, (txt, a, b) in enumerate(audio[k][1]):
            sp = d / f"sub{k:02d}_{j:02d}.png"
            chosen_kind = icon_kind(txt)
            if chosen_kind in recent_icon_kinds:
                seed = int(hashlib.sha256(norm(txt).encode("utf-8")).hexdigest()[:8], 16)
                alternatives = [kind for kind in icon_kinds if kind not in recent_icon_kinds]
                if alternatives:
                    chosen_kind = alternatives[seed % len(alternatives)]
            jobs.append((sub_html(txt, chosen_kind), sp, True))
            row.append((sp, a, b))
            recent_icon_kinds.append(chosen_kind)
            recent_icon_kinds = recent_icon_kinds[-2:]
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
