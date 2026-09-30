#!/bin/bash
# rebuild python3.12 WITH ssl (libssl-dev now that apt lock is free), then zephyr deps
export DEBIAN_FRONTEND=noninteractive
export http_proxy=http://proxyjp.huawei.com:8080
export https_proxy=http://proxyjp.huawei.com:8080

apt-get install -y -qq libssl-dev zlib1g-dev libffi-dev 2>&1 | tail -2 || exit 9
cd /opt/Python-3.12.10
./configure --prefix=/opt/py312 --enable-optimizations=no > conf2.log 2>&1 || { tail -5 conf2.log; exit 4; }
make -j8 > make2.log 2>&1 || { tail -5 make2.log; exit 5; }
make install > install2.log 2>&1 || { tail -5 install2.log; exit 6; }
/opt/py312/bin/python3.12 -c "import ssl; print('ssl OK', ssl.OPENSSL_VERSION)" || exit 7

rm -rf ~/fwctx-venv312
/opt/py312/bin/python3.12 -m venv ~/fwctx-venv312
PIP=~/fwctx-venv312/bin/pip
$PIP install -q --upgrade pip 2>&1 | tail -1
$PIP install -q -i https://repo.huaweicloud.com/repository/pypi/simple \
  --trusted-host repo.huaweicloud.com west pyelftools pykwalify jsonschema PyYAML 2>&1 | tail -2
$PIP install -q -i https://repo.huaweicloud.com/repository/pypi/simple \
  --trusted-host repo.huaweicloud.com \
  -r ~/fwctx-bench/public/zephyr/scripts/requirements-base.txt 2>&1 | tail -2
~/fwctx-venv312/bin/python -c "import jsonschema, yaml; print('zephyr deps OK')" && echo ZEPHYR-ENV-READY
