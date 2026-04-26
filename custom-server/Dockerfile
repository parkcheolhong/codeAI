FROM nvidia/cuda:12.1.0-cudnn8-devel-ubuntu22.04

WORKDIR /app

# 시스템 패키지 설치
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    git \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Python 패키지 설치
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Flash Attention 2 설치 (옵션 — CUDA/PyTorch 버전 불일치 시 스킵)
RUN pip3 install flash-attn --no-build-isolation || echo "flash-attn skipped (CUDA mismatch)"

# AWQ 모델 지원 패키지 설치 (옵션 — 실패 시 스킵)
RUN pip3 install autoawq --no-build-isolation || echo "autoawq skipped"

# pypcre 수동 빌드 (PyPI 메타데이터 깨짐 workaround: setuptools 59 compat)
RUN apt-get update -qq && apt-get install -y cmake ninja-build libpcre2-dev && rm -rf /var/lib/apt/lists/*
RUN pip3 install wheel "setuptools>=61"
COPY patch_pypcre.py /tmp/patch_pypcre.py
RUN mkdir -p /tmp/pypcre_fix && \
    python3 -c "import urllib.request; urllib.request.urlretrieve('https://files.pythonhosted.org/packages/source/P/PyPcre/pypcre-0.3.2.tar.gz', '/tmp/pypcre_fix/pypcre-0.3.2.tar.gz')" && \
    python3 -c "import tarfile; tarfile.open('/tmp/pypcre_fix/pypcre-0.3.2.tar.gz').extractall('/tmp/pypcre_fix/')" && \
    python3 /tmp/patch_pypcre.py && \
    pip3 install /tmp/pypcre_fix/pypcre-0.3.2/ --no-build-isolation && \
    python3 -c "import pcre; print('pcre OK')"

# defuser PyPI 메타데이터 깨짐 workaround (gptqmodel loader.py 필수 의존)
# logbar 선설치 필수 (defuser/__init__.py -> defuser/utils/hf.py가 logbar 임포트)
RUN pip3 install logbar && \
    python3 -c "import urllib.request; urllib.request.urlretrieve('https://files.pythonhosted.org/packages/f8/80/118eb879b9bfa5dc0fd3d2f085796b13abf5caf056c018ac195a211bbf45/defuser-0.0.8.tar.gz', '/tmp/defuser-0.0.8.tar.gz')" && \
    python3 -c "import tarfile; tarfile.open('/tmp/defuser-0.0.8.tar.gz').extractall('/tmp/')" && \
    python3 -c "import re; p='/tmp/defuser-0.0.8/pyproject.toml'; c=open(p).read(); open(p,'w').write(re.sub(r'name\s*=\s*\"unknown\"','name = \"defuser\"',c))" && \
    pip3 install /tmp/defuser-0.0.8/ --no-build-isolation && \
    python3 -c "import defuser; print('defuser OK')"

# gptqmodel prebuilt wheel 설치 (cu130 + torch2.11 대응)
# device-smi: gptqmodel utils/device.py 필수. tokenicer: PyPI 메타데이터 깨짐 — 실패 허용
RUN python3 -c "import urllib.request; urllib.request.urlretrieve('https://github.com/ModelCloud/GPTQModel/releases/download/v6.0.3/gptqmodel-6.0.3%2Bcu130torch2.11-cp310-cp310-linux_x86_64.whl', '/tmp/gptqmodel-6.0.3+cu130torch2.11-cp310-cp310-linux_x86_64.whl')" && \
    pip3 install "/tmp/gptqmodel-6.0.3+cu130torch2.11-cp310-cp310-linux_x86_64.whl" --no-deps && \
    pip3 install "pillow>=11.3.0" "threadpoolctl>=3.6.0" device-smi && \
    (pip3 install tokenicer || echo "tokenicer skipped") && \
    python3 -c "import gptqmodel; print('gptqmodel OK')"

# 애플리케이션 코드
COPY . .

EXPOSE 8000

CMD ["python3", "server.py"]