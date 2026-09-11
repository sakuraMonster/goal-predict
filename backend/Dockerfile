FROM python:3.12-slim
WORKDIR /app
# 替换 apt 源为阿里云镜像（兼容 .list 和 .sources 格式），安装 libgomp1
RUN find /etc/apt -name '*.list' -o -name '*.sources' | xargs sed -i \
    -e 's|deb.debian.org|mirrors.aliyun.com|g' \
    -e 's|security.debian.org|mirrors.aliyun.com|g' \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
