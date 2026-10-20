/*
 * font_tt.c - Glyphs from TrueType files with stb_truetype (font.h). Linux.
 */
#include "font.h"
#include "util.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define STB_TRUETYPE_IMPLEMENTATION
#define STBTT_STATIC
#include "stb_truetype.h"

#define DEJAVU "/usr/share/fonts/truetype/dejavu/"
static const char *FACE_FILES[NFACE][3] = {
    { DEJAVU "DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf" },
    { DEJAVU "DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" },
    { DEJAVU "DejaVuSans-Oblique.ttf", "/usr/share/fonts/TTF/DejaVuSans-Oblique.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Oblique.ttf" },
    { DEJAVU "DejaVuSansMono.ttf", "/usr/share/fonts/TTF/DejaVuSansMono.ttf", "/usr/share/fonts/dejavu/DejaVuSansMono.ttf" },
};
/* Characters the face lacks: symbols, then CJK. */
static const char *FALLBACKS[] = {
    DEJAVU "DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/unifont/unifont.ttf",
};

typedef struct { unsigned char *data; stbtt_fontinfo info; int ok; } face_t;
static face_t faces[NFACE], fallbacks[COUNT(FALLBACKS)];
static face_t *base;              /* metrics come from the regular face (DejaVu Sans and its Mono share them) */

static int load(face_t *f, const char *path)
{
    size_t n;
    f->data = (unsigned char *)read_file(path, &n);
    if (!f->data) return 0;
    int off = stbtt_GetFontOffsetForIndex(f->data, 0);
    f->ok = off >= 0 && stbtt_InitFont(&f->info, f->data, off);
    if (!f->ok) { free(f->data); f->data = NULL; }
    return f->ok;
}

int font_init(void)
{
    for (int i = 0; i < NFACE; i++)
        for (int k = 0; k < 3 && !faces[i].ok; k++) load(&faces[i], FACE_FILES[i][k]);
    for (int i = 0; i < COUNT(FALLBACKS); i++) load(&fallbacks[i], FALLBACKS[i]);
    if (!faces[F_REG].ok) {
        for (int i = 0; i < COUNT(FALLBACKS) && !base; i++) if (fallbacks[i].ok) base = &fallbacks[i];
        if (!base) { fprintf(stderr, "scriptures: no font found (install fonts-dejavu-core)\n"); return -1; }
        faces[F_REG] = *base;
    }
    base = &faces[F_REG];
    for (int i = 0; i < NFACE; i++) if (!faces[i].ok) faces[i] = faces[F_REG];
    return 0;
}

int font_ascent(int px)
{
    int a, d, g;
    stbtt_GetFontVMetrics(&base->info, &a, &d, &g);
    return (int)ceilf(a * stbtt_ScaleForPixelHeight(&base->info, (float)px));
}
int font_descent(int px)
{
    int a, d, g;
    stbtt_GetFontVMetrics(&base->info, &a, &d, &g);
    return (int)ceilf(-d * stbtt_ScaleForPixelHeight(&base->info, (float)px));
}

/* The cache: open addressing on (face, px, code point). */
typedef struct { int key_face, key_px, key_cp, used; glyph_t g; } slot_t;
static slot_t *cache;
static int cap, used;

static uint32_t hkey(int face, int px, int cp) { return (uint32_t)cp * 2654435761u ^ (uint32_t)px * 40503u ^ (uint32_t)face * 97u; }

static void render(glyph_t *g, int face, int px, int cp)
{
    face_t *f = &faces[face];
    int gi = stbtt_FindGlyphIndex(&f->info, cp);
    if (!gi)
        for (int i = 0; i < COUNT(fallbacks) && !gi; i++)
            if (fallbacks[i].ok && (gi = stbtt_FindGlyphIndex(&fallbacks[i].info, cp))) f = &fallbacks[i];
    if (!gi) { f = &faces[face]; gi = stbtt_FindGlyphIndex(&f->info, 0xfffd); if (!gi) gi = stbtt_FindGlyphIndex(&f->info, '?'); }
    /* Every face is scaled to the base face's line, so mixed text shares a baseline. */
    float s = stbtt_ScaleForPixelHeight(&f->info, (float)px);
    int adv, lsb;
    stbtt_GetGlyphHMetrics(&f->info, gi, &adv, &lsb);
    g->adv = (int)lroundf(adv * s);
    int x0, y0, x1, y1;
    stbtt_GetGlyphBitmapBox(&f->info, gi, s, s, &x0, &y0, &x1, &y1);
    g->w = x1 - x0;
    g->h = y1 - y0;
    g->x0 = x0;
    g->y0 = y0;
    g->a = NULL;
    if (g->w > 0 && g->h > 0) {
        g->a = xcalloc(g->w * g->h, 1);
        stbtt_MakeGlyphBitmap(&f->info, g->a, g->w, g->h, g->w, s, s, gi);
    } else g->w = g->h = 0;
}

const glyph_t *font_glyph(int face, int px, int cp)
{
    if (face < 0 || face >= NFACE) face = F_REG;
    if (px < 4) px = 4;
    if (px > 200) px = 200;
    if (used * 2 >= cap) {                          /* grow (and rehash) */
        int ocap = cap;
        slot_t *old = cache;
        cap = cap ? cap * 2 : 4096;
        cache = xcalloc(cap, sizeof *cache);
        used = 0;
        for (int i = 0; i < ocap; i++)
            if (old[i].used) {
                uint32_t h = hkey(old[i].key_face, old[i].key_px, old[i].key_cp) & (cap - 1);
                while (cache[h].used) h = (h + 1) & (cap - 1);
                cache[h] = old[i];
                used++;
            }
        free(old);
    }
    uint32_t h = hkey(face, px, cp) & (cap - 1);
    while (cache[h].used) {
        slot_t *s = &cache[h];
        if (s->key_face == face && s->key_px == px && s->key_cp == cp) return &s->g;
        h = (h + 1) & (cap - 1);
    }
    slot_t *s = &cache[h];
    s->used = 1;
    s->key_face = face; s->key_px = px; s->key_cp = cp;
    render(&s->g, face, px, cp);
    used++;
    return &s->g;
}
