#include "model.hpp"
int Derived::run(int x) { return twice<int>(x); }
int Other::run(int x) { return x - 1; }
double use_double(double x) { return twice<double>(x); }
