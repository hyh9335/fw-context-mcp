struct Base {
    virtual void run();
    void send(int);
    void send(double);
};

struct Derived final : Base {
    void run() override;
};

struct Other {
    void run();
};

void exact_overload(Base& b) { b.send(1); }
void virtual_call(Base* b) { b->run(); }
void final_virtual(Derived* d) { d->run(); }
void unrelated(Other* o) { o->run(); }
