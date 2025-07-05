# 1. 基础镜像
FROM python:3.10-slim

# 2. 切换国内源并安装系统依赖
RUN rm -rf /etc/apt/sources.list.d/* && \
    echo "deb https://mirrors.ustc.edu.cn/debian stable main contrib non-free\n\
deb https://mirrors.ustc.edu.cn/debian stable-updates main contrib non-free\n\
deb https://mirrors.ustc.edu.cn/debian-security stable-security main contrib non-free" > /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        pkg-config \
        libcairo2 \
        libcairo2-dev \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf2.0-0 \
        libffi-dev \
        libxml2 \
        libxslt1.1 \
        zlib1g-dev \
        unixodbc \
        unixodbc-dev \
        curl \
        gnupg \
        libreoffice \
        poppler-utils \
        fonts-noto-cjk \
    && mkdir -p /etc/apt/keyrings \
    && curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /etc/apt/keyrings/microsoft.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql17 \
    && rm -rf /var/lib/apt/lists/*

# 3. 设置工作目录
WORKDIR /app

# 4. 先复制 requirements.txt
COPY requirements.txt /app/

# 5. 安装 Python 依赖
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 6. 再复制项目所有代码
COPY . /app

# 7. 启动命令
CMD ["gunicorn", "-b", "0.0.0.0:5000", "app:app"]
