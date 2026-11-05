"""Aperçu temps réel : génère un shader utilisateur mpv (GLSL, exécuté sur le GPU).

Les graphes FFmpeg (filters.py) restent la référence pour l'export. Ce shader en est une
approximation assez rapide pour tourner en direct. Il n'a aucune dépendance."""
import math
import filters as F

LOOK_ID = {None: 0, "vhs": 1, "beta": 2, "ld": 3, "qt95": 4}
SCR_ID = {None: 0, "nagra": 1, "vcrypt": 2, "eurocrypt": 3, "vc2": 4}
# standard : (sigma luma, sigma chroma, mode vertical, saturation, noir relevé, lignes)
STD = {"PAL": (0.0, 2.0, 1, 1.0, 0, 576), "SECAM": (0.0, 2.0, 2, 0.92, 0, 576),
       "NTSC": (0.6, 3.0, 0, 1.08, 1, 480)}

TEMPLATE = r"""//!HOOK MAIN
//!BIND HOOKED
//!DESC Twins Video Player : effets analogiques

#define SIGL @SIGL@
#define SIGC @SIGC@
#define VMODE @VMODE@
#define CSHIFT @CSHIFT@
#define SAT @SAT@
#define CON @CON@
#define NOISE @NOISE@
#define SETUP @SETUP@
#define SHARP @SHARP@
#define LINES @LINES@
#define LOOK @LOOK@
#define LOOKK @LOOKK@
#define SPARK @SPARK@
#define RECEP @RECEP@
#define PALD @PALD@
#define SCR @SCR@
#define SCAN @SCAN@
#define HAS_SHARP @HAS_SHARP@
#define HAS_RECEP @HAS_RECEP@
#define HAS_NOISE @HAS_NOISE@
#define HAS_PALD @HAS_PALD@
#define HAS_SPARK @HAS_SPARK@

float H(float s) { return fract(sin(s) * 43758.5453); }
vec3 toYUV(vec3 c) {
    float y = dot(c, vec3(0.299, 0.587, 0.114));
    return vec3(y, 0.492 * (c.b - y), 0.877 * (c.r - y));
}
vec3 toRGB(vec3 v) {
    float r = v.x + v.z / 0.877;
    float b = v.x + v.y / 0.492;
    float g = (v.x - 0.299 * r - 0.114 * b) / 0.587;
    return vec3(r, g, b);
}
vec3 smp(vec2 p) { return toYUV(HOOKED_tex(p).rgb); }

// flou horizontal gaussien, séparé luma / chroma, avec décalage de chroma
vec3 blurH(vec2 p, vec2 px, float sk) {
    float sl = SIGL * sk, sc = SIGC * sk;
    float stl = max(1.0, sl / 2.2), stc = max(1.0, sc / 2.2);
    float wl = 0.0, wc = 0.0, yl = 0.0;
    vec2 uv = vec2(0.0);
    for (int i = -6; i <= 6; i++) {
        float x = float(i);
        float gl = sl > 0.05 ? exp(-x * x / (2.0 * sl * sl / (stl * stl))) : (i == 0 ? 1.0 : 0.0);
        float gc = sc > 0.05 ? exp(-x * x / (2.0 * sc * sc / (stc * stc))) : (i == 0 ? 1.0 : 0.0);
        yl += smp(p + vec2(x * stl * px.x, 0.0)).x * gl;
        wl += gl;
        uv += smp(p + vec2((x * stc - CSHIFT * sk) * px.x, 0.0)).yz * gc;
        wc += gc;
    }
    return vec3(yl / wl, uv / wc);
}

vec3 core(vec2 p, vec2 px, float sk, vec2 sz) {
    vec3 v = blurH(p, px, sk);
#if VMODE == 1
    vec3 b = blurH(p - vec2(0.0, px.y), px, sk);
    v.yz = (v.yz + b.yz) * 0.5;
#elif VMODE == 2
    vec3 b = blurH(p - vec2(0.0, px.y), px, sk);
    if (mod(floor(p.y * sz.y), 2.0) < 1.0) v.z = b.z; else v.y = b.y;
#endif
#if HAS_SHARP
    v.x += SHARP * (smp(p).x - v.x);
#endif
    v.yz *= SAT;
    v.x = (v.x - 0.5) * CON + 0.5;
#if SETUP > 0
    v.x = 0.075 + v.x * 0.925;
#endif
    return v;
}

vec4 hook() {
    vec2 sz = HOOKED_size;
    vec2 px = 1.0 / sz;
    vec2 p = HOOKED_pos;
    float F = float(frame);
    float t = F * 0.04;
    float sk = max(sz.y / 576.0, 0.5);

#if LOOK == 4                      // lecteur QuickTime sous Windows 95
    vec2 q = p;
    vec3 desk = vec3(0.0, 0.5, 0.5);
    if (q.x < 0.10 || q.x > 0.90 || q.y < 0.07 || q.y > 0.93) return vec4(desk, 1.0);
    vec3 grey = vec3(0.75);
    if (q.y < 0.14) {
        if (q.x > 0.85 && q.x < 0.88 && q.y > 0.09 && q.y < 0.125) return vec4(grey, 1.0);
        if (q.x > 0.11 && q.x < 0.89 && q.y > 0.08 && q.y < 0.13) return vec4(0.0, 0.0, 0.5, 1.0);
        return vec4(grey, 1.0);
    }
    if (q.x < 0.15 || q.x > 0.85 || q.y > 0.82) {
        if (q.y > 0.85 && q.y < 0.91 && q.x > 0.15 && q.x < 0.85)
            return vec4(q.x > 0.16 && q.x < 0.19 ? grey : vec3(0.5), 1.0);
        return vec4(grey, 1.0);
    }
    p = (q - vec2(0.15, 0.14)) / vec2(0.70, 0.68);                 // zone vidéo
    p = (floor(p * vec2(120.0, 90.0)) + 0.5) / vec2(120.0, 90.0);  // blocs 2x2 façon Cinepak
#endif

#if LINES > 0
    if (sz.y > float(LINES)) p.y = (floor(p.y * float(LINES)) + 0.5) / float(LINES);
#endif

    float ly = floor(p.y * sz.y);
#if SCR == 1                       // Nagravision : permutation des lignes par blocs de 32
    float a = 2.0 * floor(16.0 * H(F * 1.7)) + 1.0;
    float b = floor(32.0 * H(F * 3.3 + 5.0));
    p.y = (floor(ly / 32.0) * 32.0 + mod(a * mod(ly, 32.0) + b, 32.0) + 0.5) / sz.y;
#elif SCR == 2                     // VideoCrypt : coupe et rotation de ligne
    float cut = floor(256.0 * H(ly * 12.9898 + F * 78.233)) / 256.0;
    p.x = fract(p.x + cut);
#elif SCR == 3                     // Eurocrypt : coupe, rotation, 2e segment inversé
    float cut = floor(256.0 * H(ly * 12.9898 + F * 78.233)) / 256.0;
    p.x = p.x < 1.0 - cut ? p.x + cut : 1.0 - p.x;
#elif SCR == 4                     // VideoCipher II : défilement, déchirure, inversion
    p.y = fract(p.y + fract(t * 0.6));
    p.x += 8.0 * sin(ly / 18.0 + t * 7.0) * px.x;
#endif

#if LOOK == 1                      // VHS : saut de têtes en bas d'image
    float hs = 1.0 - 10.0 * px.y;
    if (p.y > hs) p.x += (p.y - hs) / px.y * 6.0 * LOOKK * px.x;
#endif

    float dx = 0.0;
#if HAS_RECEP
    dx = 3.0 * RECEP * sin(ly / 5.0 + t * 8.0) * px.x * sk;
#endif
    vec3 v = core(p + vec2(dx, 0.0), px, sk, sz);
#if HAS_RECEP
    float al = 0.12 + 0.35 * RECEP;
    float gh = (8.0 + 16.0 * RECEP) * px.x * sk;
    v = mix(v, core(p + vec2(dx - gh, 0.0), px, sk, sz), al);
    v.x = (v.x - 0.5) * (1.0 - 0.18 * RECEP * abs(sin(t * 0.9))) + 0.5;
    v.x += (H(dot(p * sz, vec2(1.1, 1.7)) + F * 13.1) - 0.5) * (0.03 + 0.31 * RECEP);
#endif

#if LOOK == 1                      // VHS : trous blancs (drop-outs)
    float x0 = H(ly * 5.3 + F * 2.9);
    if (H(ly * 3.17 + F * 17.3) < 0.004 * LOOKK && p.x > x0 && p.x < x0 + 0.18) v = vec3(0.92, 0.0, 0.0);
#endif

#if HAS_NOISE
    v.x += (H(dot(p * sz, vec2(1.3, 1.9)) + F * 7.7) - 0.5) * 2.0 * NOISE;
#endif

#if HAS_PALD                       // destruction des couleurs PAL : barres de Hanover
    float th = 0.9 * PALD + radians(40.0 * PALD * sin(t * 1.7));
    float sg = mod(ly, 2.0) < 1.0 ? -1.0 : 1.0;
    vec2 c0 = v.yz;
    v.y = cos(th) * c0.x - sg * sin(th) * c0.y;
    v.z = cos(th) * c0.y + sg * sin(th) * c0.x;
    v.yz *= 1.0 - 0.5 * PALD;
    v.yz += (vec2(H(dot(p * sz, vec2(2.1, 0.7)) + F * 3.3), H(dot(p * sz, vec2(0.9, 2.3)) + F * 5.1)) - 0.5) * 0.12 * PALD;
#endif

#if HAS_SPARK                    // sparklies : traits de 3 pixels, blancs ou colorés
    vec2 cell = vec2(floor(p.x * sz.x / 3.0), ly);
    float hq = H(cell.x * 12.9898 + cell.y * 78.233 + F * 37.719);
    if (hq < SPARK) {
        v.x = 0.92 + 0.08 * H(hq * 99.0 + 1.1);
        v.y = (H(hq * 37.0 + 2.2) - 0.5) * 0.9;
        v.z = (H(hq * 53.0 + 3.3) - 0.5) * 1.2;
    }
#endif

    vec3 rgb = toRGB(v);
#if SCR == 4
    rgb = 1.0 - rgb;
#endif
#if LOOK == 4                      // 256 couleurs (3-3-2 bits) avec tramage de Bayer
    const float B[16] = float[16](0.0, 8.0, 2.0, 10.0, 12.0, 4.0, 14.0, 6.0, 3.0, 11.0, 1.0, 9.0, 15.0, 7.0, 13.0, 5.0);
    vec2 g = floor(p * vec2(240.0, 180.0));
    int bi = int(mod(g.x, 4.0)) + 4 * int(mod(g.y, 4.0));
    float dth = (B[bi] + 0.5) / 16.0;
    vec3 lv = vec3(7.0, 7.0, 3.0);
    rgb = floor(clamp(rgb, 0.0, 1.0) * lv + dth) / lv;
#endif
#if SCAN > 0
    if (mod(floor(HOOKED_pos.y * sz.y), 2.0) > 0.5) rgb *= 0.86;
#endif
    return vec4(clamp(rgb, 0.0, 1.0), 1.0);
}
"""


def _f(x):
    return f"{x:.4f}"


def build_shader(std="Original", o=None):
    """Retourne le texte du shader, ou '' si aucun effet n'est actif."""
    o = o or {}
    look = LOOK_ID[o.get("look")]
    k = o.get("look_k", 70) / 100
    scr = SCR_ID[o.get("scrambler")]
    spark, recep, pald = o.get("sparkle", 0), o.get("reception", 0), o.get("pal_destroy", 0)
    scan = 1 if o.get("scan") else 0
    sigl, sigc, vmode, sat, setup, lines = (0.0, 0.0, 0, 1.0, 0, 0)
    if std in STD:
        sigl, sigc, vmode, sat, setup, lines = STD[std]
    cshift, con, noise, sharp = 0.0, 1.0, 0.0, 0.0
    if look == 1:      # VHS
        sigl, sigc = math.hypot(sigl, 0.6 + 1.2 * k), math.hypot(sigc, 2 + 5 * k)
        cshift, sat, con, noise = 2 + 4 * k, sat * (1 - 0.2 * k), 1 - 0.08 * k, (3 + 8 * k) / 255
    elif look == 2:    # Betamax
        sigl, sigc = math.hypot(sigl, 0.5 + 0.5 * k), math.hypot(sigc, 1.5 + 3 * k)
        cshift, con, noise = 1 + k, 0.98, (2 + 4 * k) / 255
    elif look == 3:    # LaserDisc
        sigl, sigc = math.hypot(sigl, 0.8), math.hypot(sigc, 1 + 1.5 * k)
        cshift, sat, noise, sharp = 1.0, sat * 1.05, (2 + 3 * k) / 255, 0.3 + 0.5 * k
    if not (look or scr or spark or recep or pald or scan or std in STD):
        return ""
    vals = {"SIGL": _f(sigl), "SIGC": _f(sigc), "VMODE": str(vmode), "CSHIFT": _f(cshift), "SAT": _f(sat),
            "CON": _f(con), "NOISE": _f(noise), "SETUP": str(setup), "SHARP": _f(sharp),
            "LINES": str(lines), "LOOK": str(look), "LOOKK": _f(k), "SPARK": _f(spark / 100 * 0.006),
            "RECEP": _f(recep / 100), "HAS_RECEP": str(int(recep > 0)), "HAS_PALD": str(int(pald > 0)),
            "HAS_SPARK": str(int(spark > 0)), "HAS_NOISE": str(int(noise > 0)), "HAS_SHARP": str(int(sharp > 0)), "PALD": _f(pald / 100), "SCR": str(scr), "SCAN": str(scan)}
    s = TEMPLATE
    for key, val in vals.items():
        s = s.replace(f"@{key}@", val)
    return s
