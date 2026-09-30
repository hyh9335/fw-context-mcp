#!/bin/bash
# parallel: uv + python 3.13 venv for zephyr (does not touch the apt job)
if [ ! -x ~/.local/bin/uv ]; then
  curl -k -LsSf https://astral.sh/uv/install.sh -o /tmp/uv_install.sh --max-time 60 \
    && sh /tmp/uv_install.sh >/dev/null 2>&1
fi
UV=$(command -v uv || echo ~/.local/bin/uv)
[ -x "$UV" ] || { echo UV-INSTALL-FAILED; exit 2; }
"$UV" --version
export UV_PYTHON_INSTALL_MIRROR=""
export UV_INSECURE_HOST=astral.sh
export http_proxy=${http_proxy:-http://proxyjp.huawei.com:8080}
export https_proxy=${https_proxy:-http://proxyjp.huawei.com:8080}
export SSL_CERT_DIR=/etc/ssl/certs
export CURL_CA_BUNDLE=""
"$UV" python install 3.13 --insecure 2>&1 | tail -2
"$UV" venv --python 3.13 ~/fwctx-venv313 2>&1 | tail -2
~/fwctx-venv313/bin/python --version
~/fwctx-venv313/bin/pip install -q west pyelftools pykwalify 2>&1 | tail -2 || \
  ~/fwctx-venv313/bin/python -m pip install -q west pyelftools pykwalify 2>&1 | tail -2
~/fwctx-venv313/bin/west --version && echo ZEPHYR-PY-READY
