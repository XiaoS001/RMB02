# RMB02 · AI 评测与数据服务平台
#
# 层次顺序「先依赖后代码」：requirements.txt 不变时，改业务代码命中缓存层。

FROM python:3.12-slim

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖层：单独一层，只在 requirements.txt 变化时重建
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 代码层
COPY . .

# 非 root 用户运行
# 🔴 chmod +x 必须在这一步做：Windows 上 git 不保存可执行位，
#    源码里 entrypoint.sh 是 644，直接 ENTRYPOINT 会报 permission denied。
RUN groupadd --system --gid 1001 appgroup \
 && useradd --system --uid 1001 --gid appgroup --no-create-home appuser \
 && mkdir -p /app/data \
 && chmod +x /app/entrypoint.sh \
 && chown -R appuser:appgroup /app
USER appuser

EXPOSE 5102

# 打 /healthz：只读配置快照，不触发任何业务逻辑，不写库
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5102/healthz', timeout=3).status==200 else 1)"

ENTRYPOINT ["./entrypoint.sh"]
