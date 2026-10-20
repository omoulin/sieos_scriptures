/*
 * net_curl.c - HTTPS through the curl program (net.h). Linux only.
 */
#include "net.h"
#include "util.h"

#include <errno.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#define UA "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 scriptures/0.2"
#define MARK "\n@@status@@"

static void conf_str(buf_t *b, const char *key, const char *v)
{
    buf_printf(b, "%s = \"", key);
    for (; *v; v++) {
        if (*v == '"' || *v == '\\') buf_putc(b, '\\');
        if (*v == '\n') { buf_puts(b, "\\n"); continue; }
        buf_putc(b, *v);
    }
    buf_puts(b, "\"\n");
}

static char *drain(int fd, size_t *n)
{
    buf_t b = {0};
    char tmp[65536];
    for (;;) {
        ssize_t r = read(fd, tmp, sizeof tmp);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) break;
        buf_add(&b, tmp, r);
    }
    if (n) *n = b.n;
    return buf_take(&b);
}

int http_do(const char *method, const char *url, const char *cookie, const char *body, http_t *out)
{
    memset(out, 0, sizeof *out);
    buf_t c = {0};
    buf_puts(&c, "silent\nshow-error\ncompressed\nmax-time = 60\nconnect-timeout = 20\n");
    conf_str(&c, "url", url);
    conf_str(&c, "request", method);
    conf_str(&c, "header", "User-Agent: " UA);
    conf_str(&c, "header", "Accept: application/json");
    if (cookie) {
        char *h = xprintf("Cookie: %s", cookie);
        conf_str(&c, "header", h);
        free(h);
        conf_str(&c, "header", "Origin: " SITE);
        conf_str(&c, "header", "Referer: " SITE "/notes");
    }
    if (body) {
        conf_str(&c, "header", "Content-Type: application/json");
        conf_str(&c, "data-binary", body);
    }
    conf_str(&c, "write-out", MARK "%{http_code}");

    int in[2], o[2], e[2];
    if (pipe(in) || pipe(o) || pipe(e)) { buf_free(&c); out->err = xstrdup("pipe failed"); return -1; }
    pid_t pid = fork();
    if (pid == 0) {
        dup2(in[0], 0); dup2(o[1], 1); dup2(e[1], 2);
        close(in[0]); close(in[1]); close(o[0]); close(o[1]); close(e[0]); close(e[1]);
        execlp("curl", "curl", "--config", "-", (char *)NULL);
        _exit(127);
    }
    close(in[0]); close(o[1]); close(e[1]);
    signal(SIGPIPE, SIG_IGN);
    for (size_t done = 0; done < c.n;) {
        ssize_t w = write(in[1], c.p + done, c.n - done);
        if (w < 0) { if (errno == EINTR) continue; break; }
        done += (size_t)w;
    }
    close(in[1]);
    buf_free(&c);
    size_t n;
    char *data = drain(o[0], &n), *err = drain(e[0], NULL);
    close(o[0]); close(e[0]);
    int st = 0;
    while (waitpid(pid, &st, 0) < 0 && errno == EINTR) ;

    char *m = NULL;                                   /* the status: after the last mark */
    for (char *p = data; (p = strstr(p, MARK)); p++) m = p;
    if (m) {
        out->status = atoi(m + strlen(MARK));
        *m = 0;
        n = (size_t)(m - data);
    }
    out->body = data;
    out->len = n;
    if (!out->status) {
        size_t l = strlen(err);
        while (l && err[l - 1] == '\n') err[--l] = 0;
        out->err = WIFEXITED(st) && WEXITSTATUS(st) == 127 ? xstrdup("the curl program is not installed")
                   : xstrdup(*err ? err : "no answer");
    }
    free(err);
    return out->status ? 0 : -1;
}

void http_free(http_t *h) { free(h->body); free(h->err); memset(h, 0, sizeof *h); }

char *url_escape(const char *s)
{
    buf_t b = {0};
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || strchr("-_.~", c))
            buf_putc(&b, c);
        else buf_printf(&b, "%%%02X", c);
    }
    return buf_take(&b);
}
