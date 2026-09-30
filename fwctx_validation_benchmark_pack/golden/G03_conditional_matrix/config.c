#ifndef LEVEL
#define LEVEL 0
#endif
int selected(void) {
#if defined(FEATURE_A) && LEVEL >= 2
    return 10;
#elif defined(FEATURE_B) || LEVEL == 1
    return 20;
#else
    return 30;
#endif
}
#if !defined(DISABLE_HELPER)
int helper(void) { return selected(); }
#endif
