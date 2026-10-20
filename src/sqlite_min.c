/*
 * sqlite_min.c - sq_each() over libsqlite3.so.0, opened with dlopen (sqlite_min.h).
 */
#include "sqlite_min.h"

#include <dlfcn.h>
#include <stddef.h>

typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;
#define SQLITE_OK 0
#define SQLITE_ROW 100
#define SQLITE_OPEN_READONLY 1

static struct {
    int tried;
    int (*open_v2)(const char *, sqlite3 **, int, const char *);
    int (*prepare_v2)(sqlite3 *, const char *, int, sqlite3_stmt **, const char **);
    int (*step)(sqlite3_stmt *);
    int (*column_count)(sqlite3_stmt *);
    const unsigned char *(*column_text)(sqlite3_stmt *, int);
    int (*column_bytes)(sqlite3_stmt *, int);
    int (*finalize)(sqlite3_stmt *);
    int (*close_v2)(sqlite3 *);
} sq;

static int load(void)
{
    if (sq.tried) return sq.open_v2 != NULL;
    sq.tried = 1;
    void *h = dlopen("libsqlite3.so.0", RTLD_NOW);
    if (!h) return 0;
#define SYM(field, name) *(void **)&sq.field = dlsym(h, name)
    SYM(open_v2, "sqlite3_open_v2");
    SYM(prepare_v2, "sqlite3_prepare_v2");
    SYM(step, "sqlite3_step");
    SYM(column_count, "sqlite3_column_count");
    SYM(column_text, "sqlite3_column_text");
    SYM(column_bytes, "sqlite3_column_bytes");
    SYM(finalize, "sqlite3_finalize");
    SYM(close_v2, "sqlite3_close_v2");
#undef SYM
    if (!sq.prepare_v2 || !sq.step || !sq.column_text || !sq.finalize || !sq.close_v2) sq.open_v2 = NULL;
    return sq.open_v2 != NULL;
}

int sq_each(const char *path, const char *sql,
            void (*row)(void *ctx, int ncol, const char **vals, const int *lens), void *ctx)
{
    if (!load()) return -1;
    sqlite3 *db = NULL;
    if (sq.open_v2(path, &db, SQLITE_OPEN_READONLY, NULL) != SQLITE_OK) { if (db) sq.close_v2(db); return -1; }
    sqlite3_stmt *st = NULL;
    if (sq.prepare_v2(db, sql, -1, &st, NULL) != SQLITE_OK) { sq.close_v2(db); return -1; }
    int n = 0, nc = sq.column_count(st);
    const char *vals[32];
    int lens[32];
    if (nc > 32) nc = 32;
    while (sq.step(st) == SQLITE_ROW) {
        for (int i = 0; i < nc; i++) {
            vals[i] = (const char *)sq.column_text(st, i);
            lens[i] = sq.column_bytes(st, i);
        }
        row(ctx, nc, vals, lens);
        n++;
    }
    sq.finalize(st);
    sq.close_v2(db);
    return n;
}
