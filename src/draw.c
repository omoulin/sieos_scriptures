/*
 * draw.c - The drawing layer (draw.h), after Atlas's draw.c: the same
 * palettes (day and night), the same gloss on the accent, the same
 * arithmetic (integers only).
 *
 * Unlike Atlas (a display list rasterised in strips, straight to video
 * memory), the picture here is a whole image in memory, drawn in palette
 * colours; draw_present() then copies it into the window.
 */
#include "draw.h"
#include "font.h"
#include "util.h"

#include <stdlib.h>
#include <string.h>

/* The palettes (the order of PAL_...): Atlas's day and night, as a window
 * shows them (glass over the panel: solid). */
static const uint32_t pal_day[NPAL] = {
    0xdfe5f4, 0xfbfcff, 0xb9c1d6, 0xffc93c, 0x1a2238, 0x4a5470, 0xd9e0f2, 0xffffff, 0x1a2238, 0xc8322a,
    0xbcd0f7, 0x3a4a8c, 0x2a1d00, 0xf3f5fa, 0xffffff, 0x2a3350, 0x8a5200 };
static const uint32_t pal_night[NPAL] = {
    0x0a0e1a, 0x242b44, 0x3b4460, 0xffc93c, 0xe4e8f4, 0x9aa3bf, 0x2d3552, 0x171c2c, 0xd6dcec, 0xff6b5e,
    0x34477d, 0x222b4d, 0x2a1d00, 0x1c2236, 0x10141f, 0x0a0e1a, 0xffc93c };
uint32_t pal[NPAL] = {
    0xdfe5f4, 0xfbfcff, 0xb9c1d6, 0xffc93c, 0x1a2238, 0x4a5470, 0xd9e0f2, 0xffffff, 0x1a2238, 0xc8322a,
    0xbcd0f7, 0x3a4a8c, 0x2a1d00, 0xf3f5fa, 0xffffff, 0x2a3350, 0x8a5200 };
/* Highlights: the pastels of a highlighter on paper; at night, the same inks
 * deep enough for light text over them. */
static const uint32_t hl_day[8] = { 0xfff07a, 0xbfe8b0, 0xb9d6f5, 0xf6b7b0, 0xdcc6f2, 0xf9cf9f, 0xf6c1dd, 0xd8d6d0 };
static const uint32_t hl_night[8] = { 0x6a5a14, 0x2f5a34, 0x2a4c7a, 0x6e3330, 0x4f3d75, 0x70491e, 0x6a3458, 0x464a58 };
static int night;

static uint32_t *P;                 /* the picture */
static int W, H;
static int cx0, cy0, cx1, cy1;      /* the clip */
static int clips[16][4], nclip;

uint32_t hl_color(int i) { return (night ? hl_night : hl_day)[i & 7]; }
void draw_night(int on) { night = !!on; memcpy(pal, night ? pal_night : pal_day, sizeof pal); }
int  draw_night_get(void) { return night; }

void draw_target(uint32_t *px, int w, int h)
{
    P = px; W = w; H = h;
    nclip = 0;
    cx0 = cy0 = 0; cx1 = w; cy1 = h;
}

void draw_clip(int x0, int y0, int x1, int y1)
{
    if (nclip < 16) { clips[nclip][0] = cx0; clips[nclip][1] = cy0; clips[nclip][2] = cx1; clips[nclip][3] = cy1; nclip++; }
    cx0 = MAX(cx0, x0); cy0 = MAX(cy0, y0); cx1 = MIN(cx1, x1); cy1 = MIN(cy1, y1);
}
void draw_unclip(void)
{
    if (!nclip) { cx0 = cy0 = 0; cx1 = W; cy1 = H; return; }
    nclip--;
    cx0 = clips[nclip][0]; cy0 = clips[nclip][1]; cx1 = clips[nclip][2]; cy1 = clips[nclip][3];
}

static int isqrt(int v) { int r = 0; while ((r + 1) * (r + 1) <= v) r++; return r; }
static int inset(int y, int y0, int y1, int r)          /* how far a rounded corner pulls row y in */
{
    if (r <= 0) return 0;
    int d = y < y0 + r ? y0 + r - y : y >= y1 - r ? y - (y1 - r) + 1 : 0;
    return d ? r - isqrt(r * r - (d - 1) * (d - 1)) : 0;
}
static void span(int y, int x0, int x1, uint32_t c)
{
    if (y < cy0 || y >= cy1) return;
    x0 = MAX(x0, cx0); x1 = MIN(x1, cx1);
    uint32_t *d = P + (size_t)y * W;
    for (int x = x0; x < x1; x++) d[x] = c;
}

static uint32_t shine(uint32_t v, int l)              /* l/256 of the way to white */
{
    return ((v >> 16 & 255) + ((255 - (v >> 16 & 255)) * l >> 8)) << 16 | ((v >> 8 & 255) + ((255 - (v >> 8 & 255)) * l >> 8)) << 8 |
           ((v & 255) + ((255 - (v & 255)) * l >> 8));
}

/* The accent filled is a gloss (Atlas's): solid, its top row lit. */
static int gloss(uint32_t c, int x0, int y0, int x1, int y1) { return c == C_ACC && x1 - x0 >= 6 && y1 - y0 >= 6; }

void draw_rect(int x0, int y0, int x1, int y1, int r, uint32_t c)
{
    r = MIN(r, MIN(x1 - x0, y1 - y0) / 2);
    int lit = gloss(c, x0, y0, x1, y1);
    for (int y = MAX(y0, cy0); y < MIN(y1, cy1); y++) {
        int i = inset(y, y0, y1, r);
        span(y, x0 + i, x1 - i, lit && y == y0 ? shine(c, 150) : c);
    }
}

void draw_top_round(int x0, int y0, int x1, int y1, int r, uint32_t c)
{
    r = MIN(r, MIN(x1 - x0, y1 - y0) / 2);
    int lit = gloss(c, x0, y0, x1, y1);
    for (int y = MAX(y0, cy0); y < MIN(y1, cy1); y++) {
        int i = y < y0 + r ? inset(y, y0, y0 + 2 * r, r) : 0;
        span(y, x0 + i, x1 - i, lit && y == y0 ? shine(c, 150) : c);
    }
}

void draw_frame(int x0, int y0, int x1, int y1, int r, int t, uint32_t c)
{
    r = MIN(r, MIN(x1 - x0, y1 - y0) / 2);
    int ir = r > t ? r - t : 0;
    for (int y = MAX(y0, cy0); y < MIN(y1, cy1); y++) {
        int i = inset(y, y0, y1, r);
        if (y < y0 + t || y >= y1 - t) { span(y, x0 + i, x1 - i, c); continue; }
        int j = inset(y, y0 + t, y1 - t, ir);
        span(y, x0 + i, x0 + t + j, c);
        span(y, x1 - t - j, x1 - i, c);
    }
}

void draw_shade(int x0, int y0, int x1, int y1, int r, int k)
{
    for (int y = MAX(y0, cy0); y < MIN(y1, cy1); y++) {
        int i = inset(y, y0, y1, r), a = MAX(x0 + i, cx0), b = MIN(x1 - i, cx1);
        uint32_t *d = P + (size_t)y * W;
        if (k == 3) for (int x = a; x < b; x++) d[x] -= d[x] >> 3 & 0x1f1f1f;
        else if (k == 2) for (int x = a; x < b; x++) d[x] = d[x] >> 1 & 0x7f7f7f;
        else for (int x = a; x < b; x++) d[x] -= d[x] >> 2 & 0x3f3f3f;
    }
}

/* As Atlas floats its menus over the desk: three soft shadows, wider and
 * lower each. */
void draw_float(int x0, int y0, int x1, int y1, int r)
{
    for (int k = 3; k >= 1; k--) {
        int d = k * MAX(1, r / 4);
        draw_shade(x0 - d + r / 2, y0 + d, x1 + d - r / 2, y1 + d + d / 2, r + d, 3);
    }
}

int draw_text(int x, int y, const char *s, int n, int face, int px, uint32_t c)
{
    for (int i = 0, k; i < n && s[i]; i += k) {
        int cp = utf8_dec(s + i, &k);
        if (cp == '\n' || cp == 0xad) continue;
        const glyph_t *g = font_glyph(face, px, cp);
        int gx = x + g->x0, gy = y + g->y0;
        if (gx < cx1 && gx + g->w > cx0 && gy < cy1 && gy + g->h > cy0)
            for (int r = 0; r < g->h; r++) {
                int yy = gy + r;
                if (yy < cy0 || yy >= cy1) continue;
                uint32_t *d = P + (size_t)yy * W;
                const uint8_t *src = g->a + r * g->w;
                for (int col = 0; col < g->w; col++) {
                    int xx = gx + col, a = src[col];
                    if (!a || xx < cx0 || xx >= cx1) continue;
                    a += a >> 7;                                 /* (0..255 -> 0..256) */
                    uint32_t o = d[xx];
                    d[xx] = a >= 256 ? c : ((((o >> 16 & 255) * (256 - a) + (c >> 16 & 255) * a) >> 8) << 16) |
                                           ((((o >> 8 & 255) * (256 - a) + (c >> 8 & 255) * a) >> 8) << 8) |
                                           (((o & 255) * (256 - a) + (c & 255) * a) >> 8);
                }
            }
        x += g->adv;
    }
    return x;
}

int text_w(const char *s, int n, int face, int px)
{
    int w = 0;
    for (int i = 0, k; i < n && s[i]; i += k) {
        int cp = utf8_dec(s + i, &k);
        if (cp != '\n' && cp != 0xad) w += font_glyph(face, px, cp)->adv;
    }
    return w;
}

int text_fit(const char *s, int n, int face, int px, int maxw)
{
    int w = 0, i = 0;
    for (int k; i < n && s[i]; i += k) {
        int a = font_glyph(face, px, utf8_dec(s + i, &k))->adv;
        if (w + a > maxw) break;
        w += a;
    }
    return i;
}

void draw_text_clipped(int x, int y, int maxw, const char *s, int face, int px, uint32_t c)
{
    int n = (int)strlen(s);
    if (text_w(s, n, face, px) <= maxw) { draw_text(x, y, s, n, face, px, c); return; }
    int ell = text_w("…", 3, face, px), k = text_fit(s, n, face, px, maxw - ell);
    x = draw_text(x, y, s, k, face, px, c);
    draw_text(x, y, "…", 3, face, px, c);
}

void draw_present(uint32_t *out, int pitch)
{
    for (int y = 0; y < H; y++) memcpy(out + (size_t)y * pitch, P + (size_t)y * W, (size_t)W * 4);
}
