#pragma once
static inline int role_value(void) {
#ifdef ROLE_MASTER
    return 11;
#else
    return 22;
#endif
}
static inline int feature_value(void) {
#if FEATURE_X
    return 100;
#else
    return 200;
#endif
}
