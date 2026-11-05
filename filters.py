"""Chaînes de filtres FFmpeg (utilisées par libmpv) : surround/Pro Logic et standards TV.
Ce module n'a aucune dépendance : il peut être testé seul."""

AUDIO_MODES = {
    "Stéréo (sans traitement)": "stereo",
    "Stéréo élargie": "wide",
    "Dolby Surround (matriciel)": "dss",
    "Dolby Pro Logic (4 canaux)": "pl",
    "Pro Logic II Movie": "pl2m",
    "Pro Logic II Music": "pl2x",
    "Pro Logic II Matrix": "pl2mx",
    "Upmix 5.1 FFmpeg": "ff",
}
# mode: (poids d'extraction du centre, surround mono ?, niveau surround)
PARAMS = {
    "dss": (0.0, True, 1.0),
    "pl": (1.0, True, 1.0),
    "pl2m": (1.0, False, 1.0),
    "pl2x": (0.7, False, 0.75),
    "pl2mx": (1.0, False, 0.9),
}

EQ_PRESETS = {
    "Aucun": [],
    "Voix / dialogues": [(100, -3), (300, -1), (1000, 2), (3000, 4), (6000, 1)],
    "Rock": [(60, 4), (250, 2), (1000, -2), (4000, 3), (10000, 4)],
    "Classique": [(60, 3), (250, 1), (4000, -1), (10000, 2)],
    "Basses fortes": [(40, 7), (100, 5), (250, 2)],
    "Aigus clairs": [(4000, 3), (8000, 4), (12000, 4)],
}

STANDARDS = {
    "PAL": dict(fps="25", lines=576, chroma="gblur=sigma=2:sigmaV=0.8:planes=6",
                luma="", sat=1.0, setup=False),
    "SECAM": dict(fps="25", lines=576, chroma="gblur=sigma=2:sigmaV=1.2:planes=6",
                  luma="", sat=0.92, setup=False),
    "NTSC": dict(fps="30000/1001", lines=480, chroma="gblur=sigma=3:sigmaV=0:planes=6",
                 luma="gblur=sigma=0.6:sigmaV=0:planes=1", sat=1.08, setup=True),
}


def is_surround(mode):
    return mode in PARAMS or mode == "ff"


def audio_graph(mode, out51, surround=1.0, center=1.0, delay_ms=15, bass_db=0, reverb=0,
                eq="Aucun", night=False, scramble=False):
    """Retourne le graphe FFmpeg (sans l'enveloppe lavfi de mpv)."""
    f = []
    if bass_db:
        f.append(f"bass=g={bass_db}:f=120")
    if reverb:
        d = reverb / 100 * 0.5
        f.append(f"aecho=0.9:0.9:50|90|140:{d:.2f}|{d * .7:.2f}|{d * .5:.2f}")
    if mode == "wide":
        f.append(f"stereotools=slev={max(surround, 0.02):.2f}")
    elif mode == "ff":
        f.append("surround=chl_out=5.1")
    elif mode in PARAMS:
        c, mono, sm = PARAMS[mode]
        k = sm * surround
        a, b = 1 - 0.5 * c, -0.5 * c            # avant : FL' = a*FL + b*FR (centre retiré)
        cc = 0.707 * c * center
        if mono:                                 # surround mono (Dolby Surround / Pro Logic)
            bl = br = f"{.707 * k:.3f}*c0{-.707 * k:+.3f}*c1"
            d2 = delay_ms
        else:                                    # surround stéréo (Pro Logic II)
            bl = f"{k:.3f}*c0{-.6 * k:+.3f}*c1"
            br = f"{.6 * k:.3f}*c0{-k:+.3f}*c1"
            d2 = delay_ms + 3
        f.append("pan=5.1|"
                 f"c0={a:.3f}*c0{b:+.3f}*c1|"
                 f"c1={a:.3f}*c1{b:+.3f}*c0|"
                 f"c2={cc:.3f}*c0+{cc:.3f}*c1|"
                 "c3=0*c0|"
                 f"c4={bl}|c5={br}")
        if delay_ms:
            f.append(f"adelay=delays=0|0|0|0|{delay_ms}|{d2}")
    for freq, gain in EQ_PRESETS.get(eq, []):
        f.append(f"equalizer=f={freq}:t=q:w=1:g={gain}")
    if night:
        f.append("dynaudnorm=f=250:g=15:p=0.9")
    if is_surround(mode) and not out51:          # downmix casque
        f.append("pan=stereo|c0=c0+0.707*c2+0.707*c4|c1=c1+0.707*c2+0.707*c5")
        f.append("aresample=44100")
        f.append("earwax")
    if scramble:
        # simulation d'un brouilleur vocal : inversion de spectre (porteuse 3,8 kHz), sortie inintelligible
        n = 6 if (is_surround(mode) and out51) else 2
        if n == 2:
            f.append("aformat=channel_layouts=stereo")
        ex = "|".join(f"val({i})*2*cos(2*PI*3800*t)" for i in range(n))
        f += ["lowpass=f=3800", f"aeval=exprs='{ex}':c=same", "lowpass=f=3800"]
    return ",".join(f)


LOOKS = {"Aucun": None, "VHS": "vhs", "Betamax": "beta", "LaserDisc": "ld",
         "QuickTime Windows 95": "qt95", "Rendu PAL": "pal", "Rendu SECAM": "secam", "Rendu NTSC": "ntsc"}
STD_LOOKS = ("pal", "secam", "ntsc")
SCRAMBLERS = {"Désactivé": None, "Nagravision (Syster)": "nagra", "VideoCrypt": "vcrypt",
              "Eurocrypt": "eurocrypt", "VideoCipher II": "vc2"}
# norme d'origine habituelle de chaque système (utilisée pour pré-régler le standard)
SCRAMBLER_STD = {"nagra": "SECAM", "vcrypt": "PAL", "eurocrypt": "PAL", "vc2": "NTSC"}

FR = "floor(T*25)"                      # numéro de trame (25 trames/s)


def _h(seed):
    """Pseudo-aléatoire 0..1 déterministe, utilisable dans une expression geq."""
    return f"mod(abs(sin({seed})*43758.5453),1)"


def _geq(fn):
    """Applique fn(plan) -> expression sur les 3 plans, en 4:4:4, puis revient en 4:2:0."""
    e = {p: fn(p) for p in ("lum", "cb", "cr")}
    return (f"format=yuv444p,geq=lum='{e['lum']}':cb='{e['cb']}':cr='{e['cr']}',format=yuv420p")


def _look(kind, k):
    f = ["format=yuv420p"]
    if kind == "vhs":
        f += [f"gblur=sigma={0.6 + 1.2 * k:.2f}:sigmaV=0:planes=1",
              f"gblur=sigma={2 + 5 * k:.2f}:sigmaV={0.5 + k:.2f}:planes=6",
              f"chromashift=crh={round(2 + 4 * k)}",
              f"eq=saturation={1 - 0.2 * k:.2f}:contrast={1 - 0.08 * k:.2f}",
              f"noise=alls={3 + 8 * k:.1f}:allf=t+u"]
        sk = f"if(gt(Y,H-10),(Y-H+10)*{6 * k:.2f},0)"            # saut des têtes (bas d'image)
        cond = (f"lt({_h(f'Y*3.17+{FR}*17.3')},{0.004 * k:.4f})*gte(X,W*{_h(f'Y*5.3+{FR}*2.9')})"
                f"*lt(X,W*({_h(f'Y*5.3+{FR}*2.9')}+0.18))")       # drop-outs blancs
        f.append(_geq(lambda p: f"if({cond},{235 if p == 'lum' else 128},{p}(X+{sk},Y))"))
    elif kind == "beta":
        f += [f"gblur=sigma={0.5 + 0.5 * k:.2f}:sigmaV=0:planes=1",
              f"gblur=sigma={1.5 + 3 * k:.2f}:sigmaV=0.3:planes=6",
              f"chromashift=crh={round(1 + k)}",
              f"eq=saturation=1.0:contrast=0.98",
              f"noise=alls={2 + 4 * k:.1f}:allf=t+u"]
    elif kind == "ld":
        f += [f"unsharp=5:5:{0.3 + 0.5 * k:.2f}:5:5:0.0",
              f"gblur=sigma={1 + 1.5 * k:.2f}:sigmaV=0:planes=6",
              "chromashift=crh=1",
              "eq=saturation=1.05",
              f"noise=alls={2 + 3 * k:.1f}:allf=t+u"]
    elif kind == "ntsc":      # teinte instable (« Never Twice the Same Color »), bruit
        f += [f"hue=h='{6 * k:.1f}*sin(t*0.5)'", f"noise=alls={2 + 3 * k:.1f}:allf=t+u"]
    elif kind == "pal":       # image stable, léger bruit de composite
        f += [f"noise=alls={1 + 2 * k:.1f}:allf=t+u"]
    elif kind == "secam":     # chroma bruitée et décalée
        f += [f"noise=c1s={10 * k:.1f}:c1f=t+u:c2s={10 * k:.1f}:c2f=t+u", f"chromashift=crh={round(1 + 2 * k)}"]
    return f


def _qt95(k):
    pix = "rgb8" if k >= 0.5 else "rgb555le"
    return ["fps=15", "scale=240:180:force_original_aspect_ratio=decrease",
            "pad=240:180:(ow-iw)/2:(oh-ih)/2", "scale=120:90:flags=area", "scale=240:180:flags=neighbor",
            f"format={pix}", "format=rgb24", "pad=264:236:12:36:color=0xC0C0C0",
            "drawbox=x=3:y=3:w=258:h=18:color=0x000080:t=fill",
            "drawbox=x=243:y=5:w=14:h=14:color=0xC0C0C0:t=fill",
            "drawbox=x=12:y=218:w=240:h=14:color=0x808080:t=fill",
            "drawbox=x=14:y=220:w=10:h=10:color=0xC0C0C0:t=fill",
            "scale=528:472:flags=neighbor", "pad=640:480:(ow-iw)/2:(oh-ih)/2:color=0x008080",
            "format=yuv420p"]


def _reception(level):
    k = level / 100
    a, g, amp = 0.12 + 0.35 * k, int(8 + 16 * k), 3.0 * k
    dx = f"{amp:.2f}*sin(Y/5+T*8)"
    f = [_geq(lambda p: f"(1-{a:.2f})*{p}(X+{dx},Y)+{a:.2f}*{p}(X+{dx}-{g},Y)"),
         f"noise=alls={4 + 40 * k:.1f}:allf=t+u",
         f"eq=contrast='1-{0.18 * k:.2f}*abs(sin(t*0.9))':eval=frame"]
    return f


def _pal_destroy(level):
    k = level / 100
    th = 0.9 * k
    sg = "if(mod(Y,2),1,-1)"                       # barres de Hanover : phase alternée ±
    e = {"lum": "lum(X,Y)",
         "cb": f"128+cos({th:.3f})*(cb(X,Y)-128)-{sg}*sin({th:.3f})*(cr(X,Y)-128)",
         "cr": f"128+cos({th:.3f})*(cr(X,Y)-128)+{sg}*sin({th:.3f})*(cb(X,Y)-128)"}
    return [_geq(lambda p: e[p]),
            f"hue=h='{40 * k:.1f}*sin(t*1.7)':s='{1 - 0.5 * k:.2f}'",
            f"noise=c1s={30 * k:.1f}:c1f=t+u:c2s={30 * k:.1f}:c2f=t+u"]


def _pal_total():
    """Destruction totale de la chroma : phase aléatoire à chaque ligne + bruit couleur massif."""
    th = f"2*PI*{_h(f'Y*7.13+{FR}*3.7')}"
    e = {"lum": "lum(X,Y)",
         "cb": f"128+cos({th})*(cb(X,Y)-128)-sin({th})*(cr(X,Y)-128)",
         "cr": f"128+cos({th})*(cr(X,Y)-128)+sin({th})*(cb(X,Y)-128)"}
    return [_geq(lambda p: e[p]), "noise=c1s=90:c1f=t+u:c2s=90:c2f=t+u", "eq=saturation=1.6"]


def _sparkles(white, black, color):
    """Traits de 3 pixels : blancs, noirs et colorés, avec une quantité (0..100) pour chacun."""
    seed = f"floor(X/3)*12.9898+Y*78.233+{FR}*37.719"
    cw = f"lt({_h(seed)},{white / 100 * 0.006:.5f})"
    cb = f"lt({_h(seed + '+9.9')},{black / 100 * 0.006:.5f})"
    cc = f"lt({_h(seed + '+5.5')},{color / 100 * 0.006:.5f})"
    lum = (f"if({cw},235+20*{_h(seed + '+1.1')},if({cb},16*{_h(seed + '+4.4')},"
           f"if({cc},235+20*{_h(seed + '+1.1')},lum(X,Y))))")
    cbx = f"if({cw},128,if({cb},128,if({cc},255*{_h(seed + '+2.2')},cb(X,Y))))"
    crx = f"if({cw},128,if({cb},128,if({cc},255*{_h(seed + '+3.3')},cr(X,Y))))"
    return [_geq(lambda pl: {"lum": lum, "cb": cbx, "cr": crx}[pl])]


def _scramble(kind):
    cut = f"floor(256*{_h(f'Y*12.9898+{FR}*78.233')})*W/256"   # point de coupe sur 256 positions
    if kind == "vcrypt":                                       # coupe + rotation de ligne
        return [_geq(lambda p: f"{p}(mod(X+{cut},W),Y)")]
    if kind == "eurocrypt":                                    # coupe + rotation + 2e segment inversé
        return [_geq(lambda p: f"{p}(if(lt(X,W-({cut})),X+{cut},W-1-X),Y)")]
    if kind == "nagra":                                        # permutation des lignes par blocs de 32
        a = f"2*floor(16*{_h(f'{FR}*1.7')})+1"
        b = f"floor(32*{_h(f'{FR}*3.3+5')})"
        return [_geq(lambda p: f"{p}(X,floor(Y/32)*32+mod(({a})*mod(Y,32)+{b},32))")]
    if kind == "vc2":                                          # inversion vidéo + perte de synchro
        roll, dx = "floor(mod(T*0.6,1)*H)", "8*sin(Y/18+T*7)"
        return [_geq(lambda p: f"255-{p}(X+{dx},mod(Y+{roll},H))")]
    return []


EXPORT_FORMATS = {
    "MP4 (H.264 + AAC)": dict(ext="mp4", ovc="libx264", oac="aac", video=True),
    "MKV (H.264 + FLAC)": dict(ext="mkv", ovc="libx264", oac="flac", video=True),
    "WebM (VP9 + Opus)": dict(ext="webm", ovc="libvpx-vp9", oac="libopus", video=True),
    "AVI (MPEG-4 + MP3)": dict(ext="avi", ovc="mpeg4", oac="libmp3lame", video=True),
    "Audio MP3": dict(ext="mp3", ovc=None, oac="libmp3lame", video=False),
    "Audio FLAC": dict(ext="flac", ovc=None, oac="flac", video=False),
    "Audio WAV": dict(ext="wav", ovc=None, oac="pcm_s16le", video=False),
}


def video_opts(fmt, quality):
    """Options du codec vidéo pour une qualité (CRF 14..32, plus bas = meilleur)."""
    ovc = EXPORT_FORMATS[fmt]["ovc"]
    if ovc == "libx264":
        return f"crf={quality},preset=medium"
    if ovc == "libvpx-vp9":
        return f"crf={quality + 8},b=0"
    if ovc == "mpeg4":
        return f"qscale={max(1, (quality - 14) // 2 + 2)}"
    return ""


def video_graph(std="Original", aspect="4:3", o=None):
    """o : dict d'options (deint, rot, flip, denoise, sharpen, scan, look, look_k,
    sparkle, reception, pal_destroy, scrambler)."""
    o = o or {}
    light = o.get("light")                      # aperçu allégé : moitié de définition
    look = o.get("look")
    if look in STD_LOOKS:
        std = look.upper()                      # « Rendu PAL / SECAM / NTSC » impose la norme
    if o.get("pal_total") and o.get("scrambler") in ("nagra", "vcrypt"):
        std = "PAL"
    f = []
    if light and std not in STANDARDS and any(o.get(k) for k in
            ("look", "reception", "pal_destroy", "spk_w", "spk_b", "spk_c", "scrambler")) and look != "qt95":
        f.append("scale=-2:360")
    if o.get("deint"):
        f.append("yadif=0:-1:0")
    rot = o.get("rot", 0)
    if rot == 90:
        f.append("transpose=1")
    elif rot == 180:
        f.append("hflip,vflip")
    elif rot == 270:
        f.append("transpose=2")
    if o.get("flip"):
        f.append("hflip")
    if o.get("denoise"):
        f.append("hqdn3d=3:2:4:3")
    if o.get("sharpen"):
        f.append(f"unsharp=5:5:{o['sharpen'] * 0.15:.2f}:5:5:0.0")
    if std in STANDARDS:
        s = STANDARDS[std]
        h = s["lines"] // (2 if light else 1)
        w = round(h * (16 / 9 if aspect == "16:9" else 4 / 3) / 2) * 2   # largeur paire
        f += [f"fps={s['fps']}",
              f"scale={w}:{h}:force_original_aspect_ratio=decrease",
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
              "setsar=1", "format=yuv420p", s["chroma"]]
        if s["luma"]:
            f.append(s["luma"])
        if s["setup"]:
            f.append("lutyuv=y=val*0.925+19")
        if s["sat"] != 1.0:
            f.append(f"eq=saturation={s['sat']}")
    k = o.get("look_k", 70) / 100
    if look in ("vhs", "beta", "ld") + STD_LOOKS:
        f += _look(look, k)
    if o.get("reception"):
        f += _reception(o["reception"])
    if o.get("pal_destroy"):
        f += _pal_destroy(o["pal_destroy"])
    if o.get("pal_total") and o.get("scrambler") in ("nagra", "vcrypt"):
        f += _pal_total()
    if o.get("spk_w") or o.get("spk_b") or o.get("spk_c"):
        f += _sparkles(o.get("spk_w", 0), o.get("spk_b", 0), o.get("spk_c", 0))
    if o.get("scrambler"):
        f += _scramble(o["scrambler"])
    if o.get("scan"):
        f.append("drawgrid=w=8192:h=2:x=8000:y=1:t=1:c=black@0.14")
    if look == "qt95":
        f += _qt95(k)
    return ",".join(f)


def ffmpeg_cmd(ff, src, out, fmt_name, vf="", af="", quality=20, start=None, duration=None):
    """Commande FFmpeg complète pour exporter avec les mêmes graphes que le lecteur."""
    fmt = EXPORT_FORMATS[fmt_name]
    c = [ff, "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
    if start:
        c += ["-ss", str(start)]
    c += ["-i", src]
    if duration:
        c += ["-t", str(duration)]
    if fmt["video"]:
        if vf:
            c += ["-vf", vf]
        ovc = fmt["ovc"]
        if ovc == "libx264":
            c += ["-c:v", "libx264", "-crf", str(quality), "-preset", "medium", "-pix_fmt", "yuv420p"]
        elif ovc == "libvpx-vp9":
            c += ["-c:v", "libvpx-vp9", "-crf", str(quality + 8), "-b:v", "0", "-pix_fmt", "yuv420p"]
        else:
            c += ["-c:v", "mpeg4", "-q:v", str(max(1, (quality - 14) // 2 + 2)), "-pix_fmt", "yuv420p"]
    else:
        c += ["-vn"]
    if af:
        c += ["-af", af]
    c += ["-c:a", fmt["oac"]]
    if fmt["ext"] in ("mp3", "avi"):
        c += ["-ac", "2"]
    c.append(out)
    return c


def mpv_wrap(graph):
    return f"lavfi=[{graph}]" if graph else ""
