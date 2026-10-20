/*
 * linux_import.c - Linux only: bring in what the earlier Python version kept
 * in data/library.db (chapters, contents, notes) and data/account.json, and
 * read a churchofjesuschrist.org session from Firefox's cookie store.
 */
#include "linux_import.h"
#include "sqlite_min.h"
#include "store.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* ---- the Python library ---------------------------------------------------------- */

/* Its contents trees are JSON: [{"title", "children": [...]} | {"uri", "title", "type"}]. */
static void toc_from_json(toc_t *parent, const jnode *arr)
{
    int n = json_count(arr);
    parent->kids = n ? xcalloc(n, sizeof *parent->kids) : NULL;
    parent->nkids = 0;
    for (jnode *k = arr ? arr->kid : NULL; k; k = k->next) {
        toc_t *t = &parent->kids[parent->nkids++];
        t->title = xstrdup(json_str(k, "title") ? json_str(k, "title") : "");
        jnode *kids = json_get(k, "children");
        if (kids) toc_from_json(t, kids);
        else t->uri = xstrdup(json_str(k, "uri"));
    }
}

static const char *ORDER[] = { "ot", "nt", "bofm", "dc-testament", "pgp", "tg", "bd", "gs", "jst" };

typedef struct { int n; char langs[16][16]; int nlangs; } imp_t;

static void add_lang(imp_t *im, const char *lang)
{
    for (int i = 0; i < im->nlangs; i++)
        if (!strcmp(im->langs[i], lang)) return;
    if (im->nlangs < 16) snprintf(im->langs[im->nlangs++], 16, "%s", lang);
}

static void vol_row(void *ctx, int nc, const char **v, const int *l)
{
    (void)l;
    if (nc < 4 || !v[0] || !v[1] || !v[3]) return;
    jnode *j = json_parse(v[3], strlen(v[3]));
    toc_t *root = xcalloc(1, sizeof *root);
    toc_from_json(root, j);
    store_save_volume(v[0], v[1], v[2] ? v[2] : v[1], root, ORDER, COUNT(ORDER));
    toc_free(root);
    json_free(j);
    add_lang(ctx, v[0]);
}

static void ch_row(void *ctx, int nc, const char **v, const int *l)
{
    imp_t *im = ctx;
    if (nc < 6 || !v[0] || !v[1] || !v[3]) return;
    /* The same shape as the content API's answer, so one reader serves both. */
    buf_t b = {0};
    buf_puts(&b, "{\"meta\":{\"title\":");
    json_write_str(&b, v[2] ? v[2] : v[1]);
    buf_puts(&b, ",\"audio\":");
    buf_add(&b, v[5] && l[5] ? v[5] : "[]", v[5] && l[5] ? (size_t)l[5] : 2);
    buf_puts(&b, "},\"content\":{\"body\":");
    json_write_str(&b, v[3]);
    buf_puts(&b, ",\"footnotes\":");
    buf_add(&b, v[4] && l[4] ? v[4] : "{}", v[4] && l[4] ? (size_t)l[4] : 2);
    buf_puts(&b, "}}");
    if (!store_has_chapter(v[0], v[1])) store_save_chapter(v[0], v[1], b.p, b.n);
    buf_free(&b);
    im->n++;
}

static void note_row(void *ctx, int nc, const char **v, const int *l)
{
    (void)l;
    if (nc < 15 || !v[0] || note_find(v[0])) return;
    /* id lang uri anchor spans color style title content tags created updated deleted dirty remote */
    buf_t b = {0};
    buf_puts(&b, "{\"id\":"); json_write_str(&b, v[0]);
    buf_puts(&b, ",\"lang\":"); json_write_str(&b, v[1]);
    buf_puts(&b, ",\"uri\":"); json_write_str(&b, v[2]);
    if (v[3]) { buf_puts(&b, ",\"anchor\":"); json_write_str(&b, v[3]); }
    buf_printf(&b, ",\"spans\":%s,\"tags\":%s", v[4] ? v[4] : "[]", v[9] ? v[9] : "[]");
    if (v[5]) { buf_puts(&b, ",\"color\":"); json_write_str(&b, v[5]); }
    if (v[6]) { buf_puts(&b, ",\"style\":"); json_write_str(&b, v[6]); }
    buf_puts(&b, ",\"title\":"); json_write_str(&b, v[7] ? v[7] : "");
    buf_puts(&b, ",\"content\":"); json_write_str(&b, v[8] ? v[8] : "");
    buf_printf(&b, ",\"created_at\":%s,\"updated_at\":%s,\"deleted\":%s,\"dirty\":%s",
               v[10] ? v[10] : "0", v[11] ? v[11] : "0", v[12] ? v[12] : "0", v[13] ? v[13] : "1");
    if (v[14]) buf_printf(&b, ",\"remote\":%s", v[14]);
    buf_puts(&b, "}");
    jnode *j = json_parse(b.p, b.n);
    buf_free(&b);
    if (!j) return;
    note_add_json(j);
    json_free(j);
    (*(int *)ctx)++;
}

int import_python_library(void (*progress)(const char *msg, void *ctx), void *pctx)
{
    char *db = xprintf("%s/library.db", store_dir());
    if (!file_exists(db)) { free(db); return 0; }
    imp_t im = {0};
    if (progress) progress("Importing your library…", pctx);
    if (sq_each(db, "SELECT lang, code, title, toc FROM volumes", vol_row, &im) < 0) { free(db); return -1; }
    sq_each(db, "SELECT lang, uri, title, body, footnotes, audio FROM chapters", ch_row, &im);
    int nn = 0;
    notes_lock();
    sq_each(db, "SELECT id, lang, uri, anchor, spans, color, style, title, content, tags, created_at, "
                "updated_at, deleted, dirty, remote FROM notes", note_row, &nn);
    if (nn) notes_save();
    notes_unlock();
    for (int i = 0; i < im.nlangs; i++) {
        if (progress) progress("Building the search index…", pctx);
        store_rebuild_index(im.langs[i]);
    }
    /* the session the Python version saved */
    char *ap = xprintf("%s/account.json", store_dir());
    size_t len;
    char *t = read_file(ap, &len);
    jnode *j = t ? json_parse(t, len) : NULL;
    if (j && json_str(j, "cookie") && !account_cookie()) account_save(json_str(j, "cookie"));
    json_free(j);
    free(t);
    free(ap);
    free(db);
    return im.n;
}

/* ---- Firefox ---------------------------------------------------------------------------- */

static const char *ROOTS[] = {
    ".config/mozilla/firefox", ".mozilla/firefox", "snap/firefox/common/.mozilla/firefox",
    ".var/app/org.mozilla.firefox/.mozilla/firefox", ".var/app/org.mozilla.firefox/config/mozilla/firefox",
};

typedef struct { buf_t hdr; double newest; char names[64][96]; int n; } ck_t;

/* Cookies sent to www.churchofjesuschrist.org: that host, or a domain above it. */
static int domain_matches(const char *host)
{
    const char *target = "www.churchofjesuschrist.org";
    while (*host == '.') host++;
    size_t h = strlen(host), t = strlen(target);
    if (!strcmp(host, target)) return 1;
    return t > h && target[t - h - 1] == '.' && !strcmp(target + t - h, host);
}

static void cookie_row(void *ctx, int nc, const char **v, const int *l)
{
    (void)l;
    ck_t *c = ctx;
    if (nc < 6 || !v[0] || !v[1] || !v[2] || !domain_matches(v[0])) return;
    double exp = v[4] ? atof(v[4]) : 0;
    if (exp > 1e11) exp /= 1000;                  /* (newer Firefox: milliseconds) */
    if (exp && exp < now_s()) return;
    for (int i = 0; i < c->n; i++)
        if (!strcmp(c->names[i], v[1])) return;   /* (the first wins: the query sorts longer hosts first) */
    if (c->n < 64) snprintf(c->names[c->n++], 96, "%s", v[1]);
    if (c->hdr.n) buf_puts(&c->hdr, "; ");
    buf_printf(&c->hdr, "%s=%s", v[1], v[2]);
    double last = v[5] ? atof(v[5]) : 0;
    if (last > c->newest) c->newest = last;
}

static int copy_file(const char *from, const char *to)
{
    size_t n;
    char *d = read_file(from, &n);
    if (!d) return -1;
    int r = write_file(to, d, n, 0600);
    free(d);
    return r;
}

char *firefox_cookie(char **profile_out)
{
    const char *home = getenv("HOME");
    char *best = NULL, *best_profile = NULL;
    double best_t = -1;
    for (int r = 0; home && r < COUNT(ROOTS); r++) {
        char *ini = xprintf("%s/%s/profiles.ini", home, ROOTS[r]);
        char *text = read_file(ini, NULL);
        free(ini);
        for (char *line = text; line && *line;) {
            char *nl = strchr(line, '\n');
            if (nl) *nl = 0;
            if (!strncmp(line, "Path=", 5)) {
                char *dir = line[5] == '/' ? xstrdup(line + 5) : xprintf("%s/%s/%s", home, ROOTS[r], line + 5);
                char *db = xprintf("%s/cookies.sqlite", dir);
                if (file_exists(db)) {
                    /* Firefox keeps the file locked and writes through a WAL: query a copy. */
                    char tmpl[] = "/tmp/scriptures-ffXXXXXX";
                    char *tmp = mkdtemp(tmpl);
                    if (tmp) {
                        char *c1 = xprintf("%s/cookies.sqlite", tmp), *w0 = xprintf("%s-wal", db), *w1 = xprintf("%s-wal", c1);
                        if (!copy_file(db, c1)) {
                            copy_file(w0, w1);
                            ck_t ck = {0};
                            sq_each(c1, "SELECT host, name, value, path, expiry, lastAccessed FROM moz_cookies "
                                        "WHERE host LIKE '%churchofjesuschrist.org' ORDER BY length(host) DESC", cookie_row, &ck);
                            if (ck.hdr.n && ck.newest > best_t) {
                                free(best); free(best_profile);
                                best = buf_take(&ck.hdr);
                                best_profile = xstrdup(strrchr(dir, '/') ? strrchr(dir, '/') + 1 : dir);
                                best_t = ck.newest;
                            }
                            buf_free(&ck.hdr);
                        }
                        unlink(w1); unlink(c1); rmdir(tmp);
                        free(c1); free(w0); free(w1);
                    }
                }
                free(db);
                free(dir);
            }
            line = nl ? nl + 1 : NULL;
        }
        free(text);
    }
    if (profile_out) *profile_out = best_profile; else free(best_profile);
    return best;
}

/* ---- the default browser ------------------------------------------------------------------ */

const char *default_browser(int *is_firefox)
{
    static char name[64];
    static int ff = -1;
    if (ff < 0) {
        char desk[256] = "";
        FILE *p = popen("xdg-settings get default-web-browser 2>/dev/null", "r");
        if (p) { if (!fgets(desk, sizeof desk, p)) desk[0] = 0; pclose(p); }
        for (char *c = desk; *c; c++) *c = (char)(*c >= 'A' && *c <= 'Z' ? *c + 32 : *c);
        static const struct { const char *key, *name; } known[] = {
            {"firefox", "Firefox"}, {"librewolf", "LibreWolf"}, {"brave", "Brave"}, {"chromium", "Chromium"},
            {"chrome", "Chrome"}, {"edge", "Edge"}, {"vivaldi", "Vivaldi"}, {"opera", "Opera"},
            {"epiphany", "Web"}, {"falkon", "Falkon"},
        };
        snprintf(name, sizeof name, "your browser");
        for (int i = 0; i < COUNT(known); i++)
            if (strstr(desk, known[i].key)) { snprintf(name, sizeof name, "%s", known[i].name); break; }
        ff = strstr(desk, "firefox") || strstr(desk, "librewolf");
    }
    if (is_firefox) *is_firefox = ff;
    return name;
}
