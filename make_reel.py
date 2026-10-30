"""
Persian Reel builder v3.3 (Optimized Outro & Extended Smart Icons).

Input file (input.txt):
  line starting with "#"        -> cover topic/hook (spoken first, shown on the cover)
  every other non-empty line    -> one scene:   عنوان کوتاه :: متن روایت
                                   (the "عنوان ::" part is optional)
Each sentence is spoken and shown on screen one at a time.
Backgrounds feature dynamic dark animated gradient with extended smart vector icons.

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
PAGE_HANDLE = ""                 # no handle is displayed inside the video
MASCOT_DIR = Path(__file__).resolve().parent / "mascot"   
XFADE = 0.5                      # crossfade between scenes (s)
MUSIC_VOLUME = 0.12

COVER_TAG = "آقای استمرار | Mr.Continuity"
BRAND_NAME = "آقای استمرار | Mr.Continuity"
BRAND_HANDLE = ""  
YELLOW = "#FFD21F"

STYLE = (", realistic photo, natural light, shallow depth of field, high quality, "
         "vertical 9:16, no text, no letters, no logos")
FALLBACK_PROMPT = "candid photo of friendly people talking in a bright modern office, warm light"
MAX_SHOTS = 16                   
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

FONT_LINK = ('<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:'
             'wght@500;800&display=swap" rel="stylesheet">')
FONT = "'Vazirmatn','Tahoma','DejaVu Sans',sans-serif"

KEYWORDS = [
    (("ثبتنام", "عضویت", "فرم"), "candid photo of two people filling out a form"),
    (("تیمسازی", "تیم", "همتیم", "وابسته"), "candid photo of a diverse team collaborating"),
    (("قهوه", "کافه"), "candid photo of two people talking over coffee"),
    (("دیدار", "حضوری", "ملاقات", "رودرو"), "candid photo of two people shaking hands"),
    (("جلسه",), "candid photo of a small team meeting"),
    (("اعتماد", "همراهی", "کنارش"), "candid photo of trust and partnership"),
    (("هدف", "رشد", "موفقیت", "شروع", "توانمندسازی"), "candid photo of a hopeful person at sunrise"),
    (("رهبر", "استقلال", "مسئولیت", "تصمیم"), "candid photo of a confident leader guiding a team"),
    (("آموزش", "یادگیری", "تمرین"), "candid photo of people learning and training together"),
]

# ---------------------------------------------------------------- text
MARKS = re.compile("[\u064B-\u065F\u0670\u0640\u06D6-\u06ED]")


def strip_marks(s):
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


# ---------------------------------------------------------------- speech
async def tts(text, out_mp3):
    import edge_tts
    text = (text or "").strip()
    if not text:
        raise ValueError("TTS received empty text.")
    out_mp3 = Path(out_mp3)
    delays = (2, 4, 8, 12, 16)
    for attempt, delay in enumerate(delays, start=1):
        tmp_mp3 = out_mp3.with_name(out_mp3.stem + ".partial" + out_mp3.suffix)
        try:
            tmp_mp3.unlink(missing_ok=True)
            await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(tmp_mp3))
            if not tmp_mp3.exists() or tmp_mp3.stat().st_size < 1024:
                raise RuntimeError("Empty audio output")
            tmp_mp3.replace(out_mp3)
            return
        except Exception as e:
            tmp_mp3.unlink(missing_ok=True)
            if attempt < len(delays):
                await asyncio.sleep(delay)
    raise RuntimeError(f"TTS failed for text: {text}")


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


# ---------------------------------------------------------------- extended smart icons
def icon_kind(text):
    t = norm(text).lower()
    rules = [
        (("ذهن", "فکر", "تمرکز", "هوش", "مغز", "یادگیری", "تصمیمگیری"), "brain"),
        (("پول", "ثروت", "درآمد", "مالی", "سرمایه", "کیفپول", "اقتصاد"), "money"),
        (("هدف", "موفقیت", "برنامه", "مسیر", "تصمیم", "شروع", "نتیجه", "اولویت"), "target"),
        (("رشد", "پیشرفت", "افزایش", "آمار", "نمودار", "توسعه", "تغییر", "بهبود", "توانمندسازی"), "growth"),
        (("کتاب", "آموزش", "دانش", "مطالعه", "مهارت", "درس"), "book"),
        (("عشق", "رابطه", "خانواده", "دوستی", "اعتماد", "احترام", "ارتباط"), "heart"),
        (("زمان", "استمرار", "عادت", "صبر", "پشتکار", "تلاش", "انضباط"), "time"),
        (("سؤال", "سوال", "چرا", "چطور", "چگونه", "پرسش", "مشکل"), "question"),
        (("بلاکچین", "کریپتو", "تکنولوژی", "فناوری", "شبکه", "هوشمصنوعی"), "network"),
        (("تیم", "رهبری", "همکاری", "نتورک", "گروه", "مدیریت", "وابسته", "استقلال"), "people"),
        (("ایده", "خلاقیت", "الهام", "راهکار"), "bulb"),
        (("مسئولیت", "وظیفه", "تعهد", "پذیرش"), "shield"),
        (("قوانین", "اصول", "ساختار", "تنظیم"), "sliders"),
        (("قدرت", "انرژی", "شتاب", "فوری"), "zap"),
        (("برنده", "جایزه", "افتخار", "موفق"), "trophy"),
        (("انتخاب", "جهت", "قطب‌نما"), "compass"),
        (("مدارک", "قرارداد", "اسناد", "برنامه‌ریزی"), "calendar"),
    ]
    for words, kind in rules:
        if any(norm(w) in t for w in words):
            return kind
    choices = ("target", "brain", "network", "book", "people", "growth", "question", "time", "heart", "money", "shield", "sliders", "zap", "trophy", "compass", "calendar")
    return choices[int(hashlib.sha256(t.encode("utf-8")).hexdigest()[:8], 16) % len(choices)]


def icon_svg(text, color="#FFD21F", size=300, kind_override=None):
    kind = kind_override or icon_kind(text)
    paths = {
        "brain": "<path d='M48 20c-13-12-31-2-29 13-13 8-9 27 3 31-5 15 8 27 22 20 5 10 18 9 20-2V22C60 12 51 12 48 20Z'/><path d='M72 20c13-12 31-2 29 13 13 8 9 27-3 31 5 15-8 27-22 20-5 10-18 9-20-2V22c0-10 9-10 16-2Z'/>",
        "money": "<rect x='18' y='30' width='84' height='56' rx='9'/><path d='M18 42h84M78 54h24v22H78a11 11 0 0 1 0-22Z'/><circle cx='83' cy='65' r='3'/>",
        "target": "<circle cx='60' cy='60' r='42'/><circle cx='60' cy='60' r='27'/><circle cx='60' cy='60' r='11'/><path d='M60 60 99 21M83 21h16v16'/>",
        "growth": "<path d='M18 100V20M18 100h86'/><path d='m28 82 23-24 17 12 31-39'/><path d='M81 31h18v18'/>",
        "book": "<path d='M15 25c20-7 34-2 45 8v61C49 83 34 79 15 86Z'/><path d='M105 25c-20-7-34-2-45 8v61c11-11 26-15 45-8Z'/>",
        "heart": "<path d='M60 99 20 61C-3 36 28 9 49 29l11 11 11-11c21-20 52 7 29 32Z'/>",
        "time": "<circle cx='60' cy='60' r='42'/><path d='M60 34v28l20 13'/>",
        "question": "<path d='M38 42c2-27 42-31 48-6 4 18-22 21-25 39'/><circle cx='60' cy='91' r='3'/>",
        "network": "<circle cx='60' cy='22' r='10'/><circle cx='25' cy='86' r='10'/><circle cx='95' cy='86' r='10'/><circle cx='60' cy='62' r='12'/><path d='M55 32 42 52M65 32 78 52M50 70 32 79M70 70 88 79'/>",
        "people": "<circle cx='60' cy='31' r='13'/><circle cx='28' cy='43' r='10'/><circle cx='92' cy='43' r='10'/><path d='M34 95V83c0-15 12-26 26-26s26 11 26 26v12'/>",
        "bulb": "<path d='M42 76c-3-9-16-17-16-34a34 34 0 1 1 68 0c0 17-13 25-16 34Z'/><path d='M45 88h30M49 99h22M47 76h26'/>",
        "shield": "<path d='M60 10l40 15v29c0 27-17 43-40 56C37 97 20 81 20 54V25Z'/>",
        "sliders": "<path d='M18 32h84M18 60h84M18 88h84M40 24v16M70 52v16M50 80v16'/>",
        "zap": "<path d='M68 8L25 66h28l-5 46 47-65H65Z'/>",
        "trophy": "<path d='M36 14h48v26c0 21-10 32-24 32S36 61 36 40Z'/><path d='M36 25H17v13c0 15 10 22 23 22M84 25h19v13c0 15-10 22-23 22'/>",
        "compass": "<circle cx='60' cy='60' r='46'/><path d='m76 34-10 32-32 20 10-32Z'/>",
        "calendar": "<rect x='18' y='25' width='84' height='78' rx='8'/><path d='M18 44h84M39 15v20M81 15v20'/>",
    }
    path_data = paths.get(kind, paths["target"])
    return (f"<svg width='{size}' height='{size}' viewBox='0 0 120 120' "
            f"fill='none' stroke='{color}' stroke-width='5.5' stroke-linecap='round' "
            f"stroke-linejoin='round' "
            f"style='filter:drop-shadow(0 0 20px rgba(255,210,31,.4));'>"
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


# ---------------------------------------------------------------- HTML Layouts
def bg_html(image_path, idx, total, title):
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#1a0033}}"
        f".bg-animated {{"
        f"  position: absolute; width: 180%; height: 180%; top: -40%; left: -40%;"
        f"  background: radial-gradient(circle, rgba(255,210,31,0.25) 0%, rgba(26,0,51,0.95) 70%);"
        f"  animation: pulseBg 4s ease-in-out infinite alternate;"
        f"}}"
        f"@keyframes pulseBg {{"
        f"  0% {{ transform: scale(1); }}"
        f"  100% {{ transform: scale(1.15); }}"
        f"}}"
        f"</style></head><body>"
        f"<div class='bg-animated'></div>"
        f"</body></html>"
    )


def sub_html(text, icon_override=None):
    text = strip_marks(text)
    n = len(text)
    size = 64 if n < 30 else 58 if n < 48 else 52 if n < 62 else 48

    keyword_groups = ["استمرار", "موفقیت", "ثروت", "هدف", "ذهن", "رشد", "پشتکار", "بلاکچین", "درآمد", "تلاش",
                      "تیم", "رهبری", "اعتماد", "ترس", "شکست", "عادت", "تمرکز", "تصمیم", "انگیزه", "زمان", "استقلال", "مسئولیت"]
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

    icon = icon_svg(text, YELLOW, 260, icon_override)
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;background:transparent;overflow:hidden}}"
        f".sub-icon{{position:absolute;left:0;right:0;top:730px;display:flex;justify-content:center;"
        f"align-items:center;animation: popIcon 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);}}"
        f".w{{position:absolute;left:60px;right:60px;top:1010px;display:flex;justify-content:center}}"
        f".b{{max-width:920px;background:rgba(15,5,30,0.92);color:#fff;font-family:{FONT};"
        f"font-weight:800;font-size:{size}px;line-height:1.65;padding:26px 38px;"
        f"border-radius:28px;text-align:center;border:2px solid rgba(255,255,255,.14);"
        f"box-shadow: 0 10px 30px rgba(0,0,0,0.8);}}"
        f".kw{{color:{YELLOW};text-shadow:0 0 14px rgba(255,210,31,.4)}}"
        f".brand{{position:absolute;left:0;right:0;top:1500px;text-align:center;color:{YELLOW};"
        f"font-family:{FONT};font-size:38px;font-weight:800;text-shadow:0 0 20px rgba(255,210,31,.25)}}"
        f"@keyframes popIcon {{ 0% {{ transform: scale(0.8); opacity: 0; }} 100% {{ transform: scale(1); opacity: 1; }} }}"
        f"</style></head><body>"
        f"<div class='sub-icon'>{icon}</div>"
        f"<div class='w'><div class='b'>{safe}</div></div>"
        f"<div class='brand'>{html.escape(BRAND_NAME)}</div>"
        f"</body></html>"
    )


def cover_html(hook, tag=None, mascot=False, cover_image=None):
    word = cover_word(hook)
    icon = icon_svg(word, YELLOW, 330)
    return (
        f"<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>{FONT_LINK}"
        f"<style>"
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#1a0033}}"
        f"body{{font-family:{FONT};color:#fff;display:flex;flex-direction:column;"
        f"align-items:center;justify-content:center;text-align:center;position:relative}}"
        f".icon{{margin-bottom:36px;display:flex;align-items:center;justify-content:center}}"
        f".word{{color:{YELLOW};font-weight:800;font-size:120px;line-height:1.35;max-width:950px;"
        f"text-shadow: 0 0 35px rgba(255,210,31,0.3);}}"
        f".brand{{position:absolute;bottom:165px;left:0;right:0;font-size:39px;font-weight:800;color:#fff}}"
        f"</style></head><body>"
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
        f"html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#1a0033}}"
        f"body{{font-family:{FONT};color:{YELLOW};display:flex;align-items:center;justify-content:center;"
        f"text-align:center}}.wrap{{width:980px;display:flex;flex-direction:column;align-items:center;"
        f"justify-content:center;gap:32px}}"
        f".slogan{{font-size:72px;font-weight:800;line-height:1.4;color:{YELLOW};"
        f"white-space:nowrap;text-shadow:0 0 30px rgba(255,210,31,.3)}}"
        f".brand{{font-size:44px;font-weight:800;color:#fff;line-height:1.5}}"
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


# ---------------------------------------------------------------- video rendering
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


# ---------------------------------------------------------------- main build engine
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
        icon_kinds = ("brain", "money", "target", "growth", "book", "heart", "time", "question", "network", "people", "bulb", "shield", "sliders", "zap", "trophy", "compass", "calendar")
        
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
            recent_icon_kinds = recent_icon_kinds[-2:] # keep window smaller to allow higher variety rotation
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
