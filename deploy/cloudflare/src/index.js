// 腫瘤衛教機器人：Cloudflare Worker → 單一容器（FastAPI，port 8000）
//
// - 整個網站用 SITE_PASSWORD 保護（瀏覽器跳出帳號密碼視窗，帳號隨意）：網址公開時，避免陌生人用示範帳號
//   消耗 Claude 額度。LINE webhook 例外（LINE 伺服器無法輸入密碼；程式本身會驗證 LINE 簽章）。
// - 只有一個容器實例；閒置 30 分鐘後休眠（不收運算費），下一個請求再喚醒（約 30–90 秒）。
// - 容器硬碟是暫時的：休眠或重新部署後資料庫清空，示範帳號會重新建立。
import { DurableObject } from "cloudflare:workers";

const PORT = 8000;
const INACTIVITY_TIMEOUT_MS = 30 * 60 * 1000;
const READY_TIMEOUT_MS = 4 * 60 * 1000;   // 載入兩個嵌入模型、同步索引、啟動 API
const PUBLIC_PATHS = ["/line/webhook"];

export class Bot extends DurableObject {
  starting;

  constructor(ctx, env) {
    super(ctx, env);
    const container = ctx.container;
    if (container.running) {
      void ctx.blockConcurrencyWhile(() => container.setInactivityTimeout(INACTIVITY_TIMEOUT_MS));
    }
  }

  async fetch(request) {
    // 同時進來的請求共用一次啟動與就緒檢查
    this.starting ??= this.startAndWait().finally(() => { this.starting = undefined; });
    try {
      await this.starting;
    } catch (e) {
      return new Response(`系統啟動失敗：${e.message}\n請到 Cloudflare 後台 Containers 查看紀錄。`,
        { status: 503, headers: { "content-type": "text/plain; charset=utf-8" } });
    }
    const url = new URL(request.url);
    url.protocol = "http:";
    url.host = "container";
    const forwarded = new Request(url, request);
    forwarded.headers.delete("host");
    forwarded.headers.delete("authorization");   // 網站密碼不轉給後端
    return this.ctx.container.getTcpPort(PORT).fetch(forwarded);
  }

  async startAndWait() {
    const container = this.ctx.container;
    if (!container.running) {
      const env = this.env;
      const hasKey = Boolean(env.ANTHROPIC_API_KEY);
      container.start({
        enableInternet: true,   // 呼叫 Anthropic API、LINE API
        env: {
          LLM_PROVIDER: hasKey ? "anthropic" : "mock",
          ANTHROPIC_API_KEY: env.ANTHROPIC_API_KEY || "",
          PRIMARY_MODEL: env.PRIMARY_MODEL || "claude-sonnet-5-5",
          LLM_EFFORT: env.LLM_EFFORT || "low",
          AUTH_SECRET: env.AUTH_SECRET || randomHex(32),
          ADMIN_PASSWORD: env.ADMIN_PASSWORD || "",
          DEMO_SEED: env.DEMO_SEED || "true",
          LINE_CHANNEL_SECRET: env.LINE_CHANNEL_SECRET || "",
          LINE_CHANNEL_ACCESS_TOKEN: env.LINE_CHANNEL_ACCESS_TOKEN || "",
          FORWARDED_ALLOW_IPS: "*",
        },
      });
    }
    await container.setInactivityTimeout(INACTIVITY_TIMEOUT_MS);

    const port = container.getTcpPort(PORT);
    const deadline = Date.now() + READY_TIMEOUT_MS;
    let lastError;
    while (Date.now() < deadline) {
      try {
        const res = await port.fetch("http://container/health", { signal: AbortSignal.timeout(2000) });
        await res.body?.cancel();
        if (res.ok) return;
        lastError = new Error(`health ${res.status}`);
      } catch (e) {
        lastError = e;
      }
      await scheduler.wait(1000);
    }
    throw new Error(`容器 ${READY_TIMEOUT_MS / 1000} 秒內沒有就緒（${lastError?.message ?? "未知原因"}）`);
  }
}

function randomHex(bytes) {
  return [...crypto.getRandomValues(new Uint8Array(bytes))].map(b => b.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqual(a, b) {
  const enc = new TextEncoder();
  const x = enc.encode(a), y = enc.encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

function authorized(request, password) {
  const header = request.headers.get("authorization") || "";
  if (!header.startsWith("Basic ")) return false;
  let decoded = "";
  try { decoded = atob(header.slice(6)); } catch { return false; }
  const given = decoded.slice(decoded.indexOf(":") + 1);
  return timingSafeEqual(given, password);
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (!PUBLIC_PATHS.includes(path)) {
      if (!env.SITE_PASSWORD) {
        return new Response(
          "尚未設定網站密碼。請到 Cloudflare 後台：Workers & Pages → tumor-education-bot → Settings → " +
          "Variables and Secrets，新增 Secret「SITE_PASSWORD」後重新整理。",
          { status: 503, headers: { "content-type": "text/plain; charset=utf-8" } });
      }
      if (!authorized(request, env.SITE_PASSWORD)) {
        return new Response("需要網站密碼（帳號隨意）", {
          status: 401,
          headers: { "www-authenticate": 'Basic realm="tumor-education-bot", charset="UTF-8"',
                     "content-type": "text/plain; charset=utf-8" },
        });
      }
    }
    return env.BOT.getByName("main").fetch(request);
  },
};
