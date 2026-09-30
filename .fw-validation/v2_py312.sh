#!/bin/bash
# python 3.12 for zephyr: deadsnakes PPA, fallback to source build (root)
export DEBIAN_FRONTEND=noninteractive
export http_proxy=http://proxyjp.huawei.com:8080
export https_proxy=http://proxyjp.huawei.com:8080

echo "== try deadsnakes =="
apt-get install -y -qq software-properties-common 2>&1 | tail -1
add-apt-repository -y ppa:deadsnakes/ppa 2>&1 | tail -3
apt-get update -qq 2>&1 | tail -1
if apt-get install -y -qq python3.12 python3.12-venv python3.12-dev 2>&1 | tail -2; then
  command -v python3.12 && { echo DEADSNAKES-OK; exit 0; }
fi
echo "== deadsnakes failed; source build =="
cd /opt
if [ ! -f Python-3.12.10.tgz ]; then
  curl -k -L -o Python-3.12.10.tgz --max-time 300 \
    https://mirrors.huaweicloud.com/python/3.12.10/Python-3.12.10.tgz || exit 3
fi
ls -la Python-3.12.10.tgz
tar xzf Python-3.12.10.tgz
cd Python-3.12.10
./configure --prefix=/opt/py312 --enable-optimizations=no > conf.log 2>&1 || { tail -10 conf.log; exit 4; }
make -j8 > make.log 2>&1 || { tail -10 make.log; exit 5; }
make install > install.log 2>&1 || { tail -10 install.log; exit 6; }
/opt/py312/bin/python3.12 --version && echo SOURCE-BUILD-OK
