#ifndef I7_ERRNO_H
#define I7_ERRNO_H
int *__i7_errno(void);
#define set_errno(e) (*__i7_errno() = (e))
#define EINVAL 22
#define ERANGE 34
#define ENOMEM 12
#endif
