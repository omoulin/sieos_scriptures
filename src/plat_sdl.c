/*
 * plat_sdl.c - The window through SDL2 (plat.h). Linux.
 *
 * The window manager decorates the window (title bar, minimize, maximize, close,
 * moving and resizing); the app draws only inside it.
 */
#include "plat.h"

#include <SDL.h>
#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>

static SDL_Window *win;
static SDL_Renderer *ren;
static SDL_Texture *tex;
static int tw, th;
static Uint32 wake_ev;
static SDL_Cursor *cursors[NCUR];
static int cur_kind = -1;
int plat_open(const char *title, int w, int h)
{
    SDL_SetHint(SDL_HINT_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR, "0");
    SDL_SetHint(SDL_HINT_MOUSE_FOCUS_CLICKTHROUGH, "1");
    SDL_SetHint(SDL_HINT_APP_NAME, "Scriptures");
    SDL_SetHint("SDL_VIDEO_X11_WMCLASS", "scriptures");          /* (the desktop entry's StartupWMClass) */
    SDL_SetHint("SDL_VIDEO_WAYLAND_WMCLASS", "scriptures");
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) != 0) {
        SDL_Log("SDL_Init: %s", SDL_GetError());
        return -1;
    }
    win = SDL_CreateWindow(title, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, w, h,
                           SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI);
    if (!win) { SDL_Log("SDL_CreateWindow: %s", SDL_GetError()); return -1; }
    SDL_SetWindowMinimumSize(win, 420, 320);
    ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!ren) ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE);
    if (!ren) { SDL_Log("SDL_CreateRenderer: %s", SDL_GetError()); return -1; }
    wake_ev = SDL_RegisterEvents(1);
    cursors[CUR_ARROW] = SDL_CreateSystemCursor(SDL_SYSTEM_CURSOR_ARROW);
    cursors[CUR_HAND] = SDL_CreateSystemCursor(SDL_SYSTEM_CURSOR_HAND);
    cursors[CUR_TEXT] = SDL_CreateSystemCursor(SDL_SYSTEM_CURSOR_IBEAM);
    SDL_StartTextInput();
    SDL_EventState(SDL_DROPFILE, SDL_DISABLE);
    return 0;
}

void plat_size(int *w, int *h) { SDL_GetRendererOutputSize(ren, w, h); }

uint32_t *plat_begin(int *pitch)
{
    int w, h;
    plat_size(&w, &h);
    if (!tex || w != tw || h != th) {
        if (tex) SDL_DestroyTexture(tex);
        tex = SDL_CreateTexture(ren, SDL_PIXELFORMAT_RGB888, SDL_TEXTUREACCESS_STREAMING, w, h);
        tw = w; th = h;
    }
    void *px;
    int p;
    if (SDL_LockTexture(tex, NULL, &px, &p)) return NULL;
    *pitch = p / 4;
    return px;
}

void plat_end(void)
{
    SDL_UnlockTexture(tex);
    SDL_RenderCopy(ren, tex, NULL, NULL);
    SDL_RenderPresent(ren);
}

static int map_key(SDL_Keycode k)
{
    switch (k) {
    case SDLK_ESCAPE: return K_ESC;
    case SDLK_RETURN: case SDLK_KP_ENTER: return K_ENTER;
    case SDLK_TAB: return K_TAB;
    case SDLK_BACKSPACE: return K_BACKSPACE;
    case SDLK_DELETE: return K_DELETE;
    case SDLK_LEFT: return K_LEFT;
    case SDLK_RIGHT: return K_RIGHT;
    case SDLK_UP: return K_UP;
    case SDLK_DOWN: return K_DOWN;
    case SDLK_HOME: return K_HOME;
    case SDLK_END: return K_END;
    case SDLK_PAGEUP: return K_PGUP;
    case SDLK_PAGEDOWN: return K_PGDN;
    case SDLK_F1: return K_F1;
    case SDLK_PLUS: case SDLK_KP_PLUS: return '+';
    case SDLK_MINUS: case SDLK_KP_MINUS: return '-';
    default: return k > 0 && k < 128 ? (int)k : 0;
    }
}

/* Window coordinates -> pixels (they differ on HiDPI screens). */
static void to_px(int *x, int *y)
{
    int ww, wh, pw, ph;
    SDL_GetWindowSize(win, &ww, &wh);
    SDL_GetRendererOutputSize(ren, &pw, &ph);
    if (ww > 0 && wh > 0) { *x = *x * pw / ww; *y = *y * ph / wh; }
}

int plat_wait(ev_t *e, int timeout)
{
    SDL_Event s;
    memset(e, 0, sizeof *e);
    for (;;) {
        if (!(timeout < 0 ? SDL_WaitEvent(&s) : SDL_WaitEventTimeout(&s, timeout))) return 0;
        SDL_Keymod m = SDL_GetModState();
        e->mod = (m & KMOD_SHIFT ? M_SHIFT : 0) | (m & KMOD_CTRL ? M_CTRL : 0) | (m & KMOD_ALT ? M_ALT : 0);
        switch (s.type) {
        case SDL_QUIT: e->type = EV_QUIT; return 1;
        case SDL_WINDOWEVENT:
            if (s.window.event == SDL_WINDOWEVENT_SIZE_CHANGED || s.window.event == SDL_WINDOWEVENT_EXPOSED ||
                s.window.event == SDL_WINDOWEVENT_MAXIMIZED || s.window.event == SDL_WINDOWEVENT_RESTORED) {
                e->type = EV_RESIZE; return 1;
            }
            if (s.window.event == SDL_WINDOWEVENT_LEAVE) { e->type = EV_LEAVE; return 1; }
            continue;
        case SDL_MOUSEBUTTONDOWN:
        case SDL_MOUSEBUTTONUP:
            e->type = s.type == SDL_MOUSEBUTTONDOWN ? EV_DOWN : EV_UP;
            e->x = s.button.x; e->y = s.button.y;
            to_px(&e->x, &e->y);
            e->button = s.button.button == SDL_BUTTON_LEFT ? 1 : s.button.button == SDL_BUTTON_MIDDLE ? 2 : 3;
            e->clicks = s.button.clicks;
            return 1;
        case SDL_MOUSEMOTION:
            e->type = EV_MOVE;
            e->x = s.motion.x; e->y = s.motion.y;
            to_px(&e->x, &e->y);
            e->button = s.motion.state & SDL_BUTTON_LMASK ? 1 : 0;
            return 1;
        case SDL_MOUSEWHEEL: {
            e->type = EV_WHEEL;
            float y = s.wheel.preciseY;
            if (s.wheel.direction == SDL_MOUSEWHEEL_FLIPPED) y = -y;
            e->wheel = (int)(y * 120);
            if (!e->wheel) continue;
            SDL_GetMouseState(&e->x, &e->y);
            to_px(&e->x, &e->y);
            return 1;
        }
        case SDL_KEYDOWN:
            e->type = EV_KEY;
            e->key = map_key(s.key.keysym.sym);
            if (!e->key) continue;
            return 1;
        case SDL_TEXTINPUT:
            if (e->mod & M_CTRL) continue;           /* (Ctrl+letter: a shortcut, not text) */
            e->type = EV_TEXT;
            snprintf(e->text, sizeof e->text, "%s", s.text.text);
            return 1;
        default:
            if (s.type == wake_ev) { e->type = EV_WAKE; return 1; }
            continue;
        }
    }
}

void plat_wake(void)
{
    SDL_Event s;
    memset(&s, 0, sizeof s);
    s.type = wake_ev;
    SDL_PushEvent(&s);
}

void plat_cursor(int k)
{
    if (k == cur_kind || k < 0 || k >= NCUR || !cursors[k]) return;
    cur_kind = k;
    SDL_SetCursor(cursors[k]);
}

void plat_clip_set(const char *s) { SDL_SetClipboardText(s); }
char *plat_clip_get(void)
{
    char *s = SDL_GetClipboardText(), *r = NULL;
    if (s && *s) r = strdup(s);
    SDL_free(s);
    return r;
}
void plat_open_url(const char *url) { SDL_OpenURL(url); }
void plat_title(const char *t) { SDL_SetWindowTitle(win, t); }
void plat_resize(int w, int h)
{
    SDL_DisplayMode m;
    if (!SDL_GetCurrentDisplayMode(SDL_GetWindowDisplayIndex(win), &m)) {   /* (at most 90% of the screen) */
        if (w > m.w * 9 / 10) w = m.w * 9 / 10;
        if (h > m.h * 9 / 10) h = m.h * 9 / 10;
    }
    SDL_SetWindowSize(win, w, h);
    SDL_SetWindowPosition(win, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED);
}
uint32_t plat_ms(void) { return SDL_GetTicks(); }

/* The desktop's text scale for X clients: the Xft.dpi resource (GNOME, KDE and others set it;
 * 96 is 100%). With XWayland's native scaling, X windows get real pixels and this is the only
 * hint of the scale the user chose. libX11 is loaded at run time (no headers needed). */
static int xft_scale(void)
{
    const char *drv = SDL_GetCurrentVideoDriver();
    if (!drv || strcmp(drv, "x11")) return 0;
    void *x = dlopen("libX11.so.6", RTLD_NOW);
    if (!x) return 0;
    void *(*open_display)(const char *) = (void *(*)(const char *))dlsym(x, "XOpenDisplay");
    char *(*resources)(void *) = (char *(*)(void *))dlsym(x, "XResourceManagerString");
    int (*close_display)(void *) = (int (*)(void *))dlsym(x, "XCloseDisplay");
    int scale = 0;
    void *d = open_display && resources && close_display ? open_display(NULL) : NULL;
    if (d) {
        const char *r = resources(d), *p = r ? strstr(r, "Xft.dpi:") : NULL;
        if (p) {
            double dpi = atof(p + 8);
            if (dpi >= 48 && dpi <= 960) scale = (int)(dpi * 100 / 96 + 0.5);
        }
        close_display(d);
    }
    dlclose(x);
    return scale;
}

int plat_scale(void)
{
    int ww, wh, pw, ph;
    SDL_GetWindowSize(win, &ww, &wh);
    SDL_GetRendererOutputSize(ren, &pw, &ph);
    int s = ww > 0 ? pw * 100 / ww : 100;
    int xs = xft_scale();
    if (xs > s) s = xs;
    const char *env = getenv("SCRIPTURES_SCALE");     /* (a fractional-scaling desktop: say it here) */
    if (env && atoi(env) > 0) s = atoi(env);
    return s < 50 ? 100 : s;
}
