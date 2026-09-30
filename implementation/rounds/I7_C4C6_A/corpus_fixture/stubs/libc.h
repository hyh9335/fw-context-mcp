/* [I7 stub] libc internal helpers */
#ifndef I7_LIBC_H
#define I7_LIBC_H
FAR void *lib_malloc(size_t);
size_t strlcpy(FAR char *, FAR const char *, size_t);
FAR char *getenv(FAR const char *);
#endif
