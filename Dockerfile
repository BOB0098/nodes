# P11：Docker 部署。只装采集链路真正用到的东西。
#
# 不用 python:3.11-slim 之上的任何东西：mihomo 是单个静态二进制（nodecheck.py
# 自己下载），PyYAML 是唯一的第三方包。镜像里不装 requests、不装 curl 之外的
# 网络工具——装得越多，出问题时越难判断是谁在出网。
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PERSIST_DIR=/app/data \
    NODES_CACHE=/app/.cache

# curl + gzip 是 nodecheck.py 下载并解压 mihomo 用的，ca-certificates 是 TLS 根证书。
# --no-install-recommends 是必须的，默认推荐包会把镜像撑到 400MB 以上。
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gzip \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖单独一层：改代码不会让 pip 重装
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY sources.yaml ./
# 少了 template.html，collector 能采能算，但渲染那一步会 FileNotFoundError，
# 整轮跑到最后一步才失败——前面十几分钟白跑
COPY template.html ./
COPY og.png ./
COPY docker/ ./docker/
COPY dockerprobe/ ./dockerprobe/
# collector 每轮会重写 docs/P4-source-quality.md（来源质量报告）。
# 少了这个空目录，采集能跑完、检测能跑完，最后写报告时 FileNotFoundError
COPY docs/ ./docs/

# 非 root 运行。采集不需要写系统目录，给一个固定 uid 免得挂载出来的文件属主乱跳
RUN useradd -u 10001 -m runner && mkdir -p /app/data /app/.cache && chown -R runner /app
USER runner

# 默认跑一轮完整采集；`docker compose run --rm collect python3 nodecheck.py` 之类
# 的单步调用也走同一个镜像
ENTRYPOINT ["/bin/bash", "/app/docker/run.sh"]
