/*
 * sqlite_min.h - Read-only queries through the system's libsqlite3, loaded at
 * run time (no headers needed). Linux only: used to import the old Python
 * library (data/library.db) and to read Firefox's cookies.
 */
#ifndef SQLITE_MIN_H
#define SQLITE_MIN_H

/* Calls row(ctx, ncol, values, lengths) for each result row; values may be NULL.
 * Returns the number of rows, or -1 (no libsqlite3, no such file, a bad query). */
int sq_each(const char *path, const char *sql,
            void (*row)(void *ctx, int ncol, const char **vals, const int *lens), void *ctx);

#endif
