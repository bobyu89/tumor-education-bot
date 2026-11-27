# 腫瘤衛教機器人：正式部署映像
# 建置：docker compose build　　說明：docs/deploy/部署說明.md
#
# 映像裡「不含」任何病患資料或模型權重：
#   /app/data       SQLite（病患資料）       → volume
#   /app/chroma_db  向量索引（首次啟動自動建立）→ volume
#   /app/models     嵌入模型（首次啟動自動下載）→ volume
FROM python:3.12-slim

ENV PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY backend/requirements-deploy.txt /tmp/requirements-deploy.txt
# 若建置環境經過會重新簽發 TLS 的代理（醫院網路常見），以 build secret 提供代理的 CA，
# 只在這一步使用、不寫進映像：docker build --secret id=ca_bundle,src=<CA 檔> .
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install -r /tmp/requirements-deploy.txt

COPY backend/ backend/
COPY frontend/ frontend/
COPY scripts/index_vault.py scripts/index_vault.py
# 科別設定檔與各科單張（新增科別時，若單張不在 Obsidian_Vault/衛教知識庫，記得在這裡加一行 COPY）
COPY knowledge/ knowledge/
COPY Obsidian_Vault/衛教知識庫/ Obsidian_Vault/衛教知識庫/
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh

# BAKE_MODELS=1：建置時就下載嵌入模型並建立向量索引（Cloudflare Containers 用：每次喚醒都是全新硬碟，
# 不能每次啟動都下載 0.9 GB）。一般 Docker 部署維持 0，模型與索引放在 volume、首次啟動才下載。
ARG BAKE_MODELS=0
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ "$BAKE_MODELS" = "1" ]; then \
      if [ -f /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle; fi; \
      python -X utf8 scripts/index_vault.py && \
      python -X utf8 -c "import sys; sys.path.insert(0, 'backend'); import embedding; embedding.make_embedding_function(embedding.REDFLAG_MODEL)"; \
    fi

# 以非 root 身分執行；volume 掛載點先建好並交給 app 使用者
RUN useradd --create-home --uid 10001 app \
 && mkdir -p data chroma_db models Obsidian_Vault/專案管理 \
 && rm -f backend/test_*.py backend/testutil.py \
 && chmod +x /usr/local/bin/entrypoint.sh \
 && chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["entrypoint.sh"]
