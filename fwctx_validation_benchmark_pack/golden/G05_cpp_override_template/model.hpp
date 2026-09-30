#pragma once
struct Base {
    virtual int run(int x) = 0;
    virtual ~Base() = default;
};
struct Derived : Base {
    int run(int x) override;
};
struct Other {
    int run(int x);
};
template <typename T>
T twice(T x) { return x + x; }
