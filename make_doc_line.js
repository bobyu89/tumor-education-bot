const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, PageBreak, LevelFormat,
  TableOfContents,
} = require("docx");
const fs = require("fs");

// ── 色彩常數 ──
const TEAL   = "006B5F";
const LTEAL  = "4CB7A7";
const MINT   = "E8F5F3";
const LINE_GREEN = "00B900";
const LINE_LIGHT = "E6F9E6";
const GRAY1  = "F2F4F4";
const GRAY2  = "ECEEEE";
const WHITE  = "FFFFFF";
const RED    = "C62828";
const AMBER  = "E65100";
const DARK   = "191C1D";

// ── 共用 border ──
const cellBorder = (color = "C8D8D6") => ({ style: BorderStyle.SINGLE, size: 1, color });
const borders = (color) => ({
  top: cellBorder(color), bottom: cellBorder(color),
  left: cellBorder(color), right: cellBorder(color),
});
const noBorder = { style: BorderStyle.NIL, size: 0, color: "FFFFFF" };
const noBorders = { top: noBorder, bottom: noBorder, left: noBorder, right: noBorder };
const cm = { top: 100, bottom: 100, left: 150, right: 150 };

// ── 工具函式 ──
function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 360, after: 160 },
    children: [new TextRun({ text, font: "Arial", size: 32, bold: true, color: WHITE })],
    shading: { fill: TEAL, type: ShadingType.CLEAR },
    indent: { left: 160, right: 160 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: LTEAL } },
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 280, after: 120 },
    border: { left: { style: BorderStyle.SINGLE, size: 20, color: TEAL } },
    indent: { left: 200 },
    children: [new TextRun({ text, font: "Arial", size: 26, bold: true, color: TEAL })],
  });
}

function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 200, after: 80 },
    children: [new TextRun({ text, font: "Arial", size: 24, bold: true, color: "3D4947" })],
  });
}

function body(text, opts = {}) {
  return new Paragraph({
    spacing: { before: 60, after: 80, line: 360 },
    children: [new TextRun({ text, font: "Arial", size: 22, color: DARK, ...opts })],
  });
}

function bullet(text) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    spacing: { before: 40, after: 40, line: 340 },
    children: [new TextRun({ text, font: "Arial", size: 22, color: DARK })],
  });
}

function code(text) {
  return new Paragraph({
    spacing: { before: 40, after: 40 },
    shading: { fill: GRAY1, type: ShadingType.CLEAR },
    indent: { left: 360, right: 360 },
    children: [new TextRun({ text, font: "Courier New", size: 18, color: "1A4731" })],
  });
}

function spacer(n = 1) {
  return Array.from({ length: n }, () =>
    new Paragraph({ spacing: { before: 0, after: 0 }, children: [new TextRun("")] })
  );
}

function infoBox(label, text, fillColor = MINT, labelColor = TEAL) {
  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [1500, 7860],
    rows: [new TableRow({
      children: [
        new TableCell({
          borders: borders("4CB7A7"),
          shading: { fill: labelColor, type: ShadingType.CLEAR },
          margins: cm,
          verticalAlign: VerticalAlign.CENTER,
          width: { size: 1500, type: WidthType.DXA },
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [new TextRun({ text: label, font: "Arial", size: 20, bold: true, color: WHITE })],
          })],
        }),
        new TableCell({
          borders: borders("4CB7A7"),
          shading: { fill: fillColor, type: ShadingType.CLEAR },
          margins: cm,
          width: { size: 7860, type: WidthType.DXA },
          children: [new Paragraph({
            children: [new TextRun({ text, font: "Arial", size: 21, color: DARK })],
          })],
        }),
      ],
    })],
  });
}

function makeTable(headers, rows, colWidths) {
  const totalW = colWidths.reduce((a, b) => a + b, 0);
  const headerRow = new TableRow({
    tableHeader: true,
    children: headers.map((h, i) => new TableCell({
      borders: borders(LTEAL),
      shading: { fill: TEAL, type: ShadingType.CLEAR },
      margins: cm,
      width: { size: colWidths[i], type: WidthType.DXA },
      children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: h, font: "Arial", size: 20, bold: true, color: WHITE })],
      })],
    })),
  });
  const bodyRows = rows.map((row, ri) => new TableRow({
    children: row.map((cell, ci) => new TableCell({
      borders: borders("C8D8D6"),
      shading: { fill: ri % 2 === 0 ? WHITE : GRAY1, type: ShadingType.CLEAR },
      margins: cm,
      width: { size: colWidths[ci], type: WidthType.DXA },
      children: [new Paragraph({
        children: [new TextRun({ text: cell, font: "Arial", size: 20, color: DARK })],
      })],
    })),
  }));
  return new Table({
    width: { size: totalW, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [headerRow, ...bodyRows],
  });
}

// 流程步驟方塊
function stepBox(num, title, desc, color = TEAL) {
  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [900, 8460],
    rows: [new TableRow({
      children: [
        new TableCell({
          borders: borders(color),
          shading: { fill: color, type: ShadingType.CLEAR },
          margins: { top: 120, bottom: 120, left: 150, right: 150 },
          verticalAlign: VerticalAlign.CENTER,
          width: { size: 900, type: WidthType.DXA },
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [new TextRun({ text: num, font: "Arial", size: 32, bold: true, color: WHITE })],
          })],
        }),
        new TableCell({
          borders: borders(color),
          shading: { fill: MINT, type: ShadingType.CLEAR },
          margins: { top: 80, bottom: 80, left: 200, right: 150 },
          width: { size: 8460, type: WidthType.DXA },
          children: [
            new Paragraph({
              children: [new TextRun({ text: title, font: "Arial", size: 22, bold: true, color: color })],
            }),
            new Paragraph({
              children: [new TextRun({ text: desc, font: "Arial", size: 20, color: DARK })],
            }),
          ],
        }),
      ],
    })],
  });
}

// ── 主文件 ──
const doc = new Document({
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [{
          level: 0, format: LevelFormat.BULLET, text: "\u25AA",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
      {
        reference: "nums",
        levels: [{
          level: 0, format: LevelFormat.DECIMAL, text: "%1.",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
    ],
  },
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: WHITE },
        paragraph: { spacing: { before: 360, after: 160 }, outlineLevel: 0 },
      },
      {
        id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: TEAL },
        paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1 },
      },
      {
        id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Arial", color: "3D4947" },
        paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2 },
      },
    ],
  },
  sections: [
    // ════════════════════════════════════════
    // 封面
    // ════════════════════════════════════════
    {
      properties: {
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1134, bottom: 1134, left: 1134, right: 1134 },
        },
      },
      children: [
        ...spacer(6),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 0, after: 200 },
          children: [new TextRun({ text: "AI  腫瘤衛教機器人", font: "Arial", size: 56, bold: true, color: TEAL })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 0, after: 160 },
          children: [new TextRun({ text: "LINE Bot 整合版系統架構書", font: "Arial", size: 40, bold: false, color: LTEAL })],
        }),
        ...spacer(1),
        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [9360],
          rows: [new TableRow({
            children: [new TableCell({
              borders: { top: cellBorder(LTEAL), bottom: cellBorder(LTEAL), left: { style: BorderStyle.NIL }, right: { style: BorderStyle.NIL } },
              shading: { fill: MINT, type: ShadingType.CLEAR },
              margins: { top: 200, bottom: 200, left: 400, right: 400 },
              width: { size: 9360, type: WidthType.DXA },
              children: [
                new Paragraph({
                  alignment: AlignmentType.CENTER,
                  children: [new TextRun({ text: "保留研究者管理儀表板  |  病患端改用 LINE Bot 互動", font: "Arial", size: 24, color: TEAL })],
                }),
                new Paragraph({
                  alignment: AlignmentType.CENTER,
                  children: [new TextRun({ text: "RAG + Gemma + LINE Messaging API + FastAPI Webhook", font: "Courier New", size: 20, color: "3D4947" })],
                }),
              ],
            })],
          })],
        }),
        ...spacer(3),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "版本 2.0  |  2026 年 4 月", font: "Arial", size: 22, color: "6B7280" })],
        }),
        new Paragraph({ children: [new PageBreak()] }),
      ],
    },

    // ════════════════════════════════════════
    // 目錄 + 本文
    // ════════════════════════════════════════
    {
      properties: {
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1134, bottom: 1200, left: 1134, right: 1134 },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: LTEAL } },
            children: [
              new TextRun({ text: "AI 腫瘤衛教機器人  —  LINE Bot 整合版系統架構書", font: "Arial", size: 18, color: TEAL }),
            ],
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.RIGHT,
            border: { top: { style: BorderStyle.SINGLE, size: 4, color: LTEAL } },
            children: [
              new TextRun({ text: "第 ", font: "Arial", size: 18, color: "6B7280" }),
              new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 18, color: "6B7280" }),
              new TextRun({ text: " 頁", font: "Arial", size: 18, color: "6B7280" }),
            ],
          })],
        }),
      },
      children: [
        // 目錄
        new TableOfContents("目  錄", { hyperlink: true, headingStyleRange: "1-3" }),
        new Paragraph({ children: [new PageBreak()] }),

        // ────────────────────────────────────
        // 第一章：整合動機與目標
        // ────────────────────────────────────
        h1("第一章　整合動機與目標"),
        ...spacer(1),
        h2("1.1  為什麼要串接 LINE？"),
        body("原有系統的病患介面為網頁版聊天室，病患需打開瀏覽器並記住網址才能使用。對年長病患而言，學習成本偏高且黏著度低。LINE 在台灣的普及率超過 90%，幾乎每位病患手機中都已安裝，透過官方帳號（LINE Bot）提供服務可大幅降低使用門檻。"),
        ...spacer(1),
        makeTable(
          ["面向", "原有網頁版", "LINE Bot 整合版"],
          [
            ["病患入口", "需開啟瀏覽器、輸入網址", "點開 LINE 官方帳號即可對話"],
            ["學習成本", "需學習新介面", "沿用熟悉的 LINE 操作"],
            ["通知推播", "無主動推播", "可主動推送衛教提醒"],
            ["身份識別", "輸入代號 P001", "綁定一次後，自動識別身份"],
            ["研究者介面", "瀏覽器網頁（保留不動）", "瀏覽器網頁（保留不動）"],
          ],
          [2400, 3480, 3480]
        ),
        ...spacer(1),
        h2("1.2  整合目標"),
        bullet("病患端：完全改用 LINE Bot，移除原本的網頁聊天介面"),
        bullet("研究者端：保留原有瀏覽器儀表板（完全不需修改）"),
        bullet("核心邏輯：RAG、Prompt Engine、緊急關鍵字偵測全部複用"),
        bullet("新增功能：LINE userId 與病患代號綁定機制、Webhook 接收端點"),
        ...spacer(1),
        new Paragraph({ children: [new PageBreak()] }),

        // ────────────────────────────────────
        // 第二章：整體系統架構
        // ────────────────────────────────────
        h1("第二章　整體系統架構"),
        ...spacer(1),
        h2("2.1  高階架構圖"),
        body("整合 LINE 後，系統分為三條主要資料流："),
        ...spacer(1),

        // 架構圖文字版
        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [9360],
          rows: [new TableRow({
            children: [new TableCell({
              borders: borders(LTEAL),
              shading: { fill: GRAY1, type: ShadingType.CLEAR },
              margins: { top: 150, bottom: 150, left: 300, right: 300 },
              width: { size: 9360, type: WidthType.DXA },
              children: [
                new Paragraph({ children: [new TextRun({ text: "【病患 LINE Bot 流】", font: "Courier New", size: 20, bold: true, color: LINE_GREEN })] }),
                new Paragraph({ children: [new TextRun({ text: "  病患 LINE App", font: "Courier New", size: 19, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "       |  HTTPS POST（含簽名）", font: "Courier New", size: 19, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "  LINE Platform (Webhook)", font: "Courier New", size: 19, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "       |  HTTPS POST /webhook", font: "Courier New", size: 19, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "  FastAPI /webhook  -->  驗簽 --> 身份查找 --> /chat 邏輯", font: "Courier New", size: 19, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "                                             |", font: "Courier New", size: 19, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "                                   RAG + Prompt + Gemma API", font: "Courier New", size: 19, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "                                             |", font: "Courier New", size: 19, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "                                   LINE Reply API  -->  病患", font: "Courier New", size: 19, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "", font: "Courier New", size: 19, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "【緊急事件流】", font: "Courier New", size: 20, bold: true, color: RED })] }),
                new Paragraph({ children: [new TextRun({ text: "  偵測到緊急關鍵字  -->  WebSocket 推播  -->  研究者儀表板紅燈警示", font: "Courier New", size: 19, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "", font: "Courier New", size: 19 })] }),
                new Paragraph({ children: [new TextRun({ text: "【研究者瀏覽器流（不變）】", font: "Courier New", size: 20, bold: true, color: TEAL })] }),
                new Paragraph({ children: [new TextRun({ text: "  研究者瀏覽器  -->  GET /sessions, /history  -->  儀表板顯示所有對話紀錄", font: "Courier New", size: 19, color: DARK })] }),
              ],
            })],
          })],
        }),
        ...spacer(1),

        h2("2.2  元件職責對照"),
        makeTable(
          ["元件", "職責", "是否需修改"],
          [
            ["LINE Platform", "接收病患訊息、轉發 Webhook、發送回覆", "外部服務，無需修改"],
            ["FastAPI /webhook", "驗證簽名、解析事件、呼叫 chat 邏輯、回覆 LINE", "新增"],
            ["line_user_map", "LINE userId <-> 病患代號綁定表", "新增（記憶體 dict）"],
            ["/chat 核心邏輯", "RAG 查詢 + Prompt 組裝 + Gemma 呼叫", "不需修改，抽成共用函式"],
            ["alert.py", "緊急關鍵字偵測", "不需修改"],
            ["rag.py", "向量檢索衛教文件", "不需修改"],
            ["prompt.py", "個人化 Prompt 組裝", "不需修改"],
            ["websocket_manager.py", "WebSocket 推播護理師警示", "不需修改"],
            ["frontend/index.html 研究者端", "對話紀錄儀表板、Red Flag 顯示", "不需修改"],
            ["frontend/index.html 病患端", "原有聊天介面（停用）", "移除或隱藏"],
          ],
          [2800, 4160, 2400]
        ),
        ...spacer(1),
        new Paragraph({ children: [new PageBreak()] }),

        // ────────────────────────────────────
        // 第三章：LINE Messaging API 申請與設定
        // ────────────────────────────────────
        h1("第三章　LINE Messaging API 申請與設定"),
        ...spacer(1),
        h2("3.1  申請流程（逐步說明）"),
        ...spacer(1),
        stepBox("1", "登入 LINE Developers Console", "前往 developers.line.biz，以個人 LINE 帳號登入"),
        ...spacer(1),
        stepBox("2", "建立 Provider", "點選「Create a new provider」，輸入醫院或研究團隊名稱（例：ONC-Education-Team）"),
        ...spacer(1),
        stepBox("3", "建立 Messaging API Channel", "在 Provider 頁面點「Create a new channel」→ 選「Messaging API」\n填寫：Channel name（衛教小幫手）、Category（醫療 / Healthcare）"),
        ...spacer(1),
        stepBox("4", "取得金鑰", "Basic settings 頁籤 → 複製 Channel Secret\nMessaging API 頁籤 → 點「Issue」取得 Channel Access Token（長期）"),
        ...spacer(1),
        stepBox("5", "設定 Webhook URL", "Messaging API 頁籤 → Webhook settings\n填入：https://{你的公開網域}/webhook\n勾選「Use webhook」，點「Verify」確認連線"),
        ...spacer(1),
        stepBox("6", "關閉自動回覆訊息", "LINE Official Account Manager → 回應設定 → 關閉「自動回覆」和「加入好友的歡迎訊息」（改由程式控制）"),
        ...spacer(1),

        h2("3.2  取得的金鑰資訊"),
        infoBox("重要", "Channel Secret 和 Channel Access Token 不可洩漏。請勿上傳至 GitHub 或以明文分享。存放於伺服器 .env 檔案中。", MINT),
        ...spacer(1),
        makeTable(
          ["金鑰名稱", "用途", "存放位置"],
          [
            ["Channel Secret", "驗證 Webhook 請求是否來自 LINE 平台（HMAC-SHA256 簽名驗證）", ".env  LINE_CHANNEL_SECRET"],
            ["Channel Access Token", "呼叫 LINE Reply API 發送回覆訊息", ".env  LINE_CHANNEL_ACCESS_TOKEN"],
          ],
          [2800, 4360, 2200]
        ),
        ...spacer(1),
        new Paragraph({ children: [new PageBreak()] }),

        // ────────────────────────────────────
        // 第四章：公開網址取得方式
        // ────────────────────────────────────
        h1("第四章　公開 HTTPS 網址取得方式"),
        ...spacer(1),
        body("LINE Webhook 要求接收端必須是有效的 HTTPS 公開網址，localhost 無法使用。依不同階段選擇方式如下："),
        ...spacer(1),
        makeTable(
          ["階段", "工具", "費用", "優缺點"],
          [
            ["開發 / 測試", "ngrok", "免費（有限制）", "最快速，重啟後網址會變；適合個人開發測試"],
            ["展示 / 驗收", "ngrok 固定網域（付費）或 Render.com 免費方案", "低", "穩定網址，適合給醫療團隊展示"],
            ["正式上線", "Railway / GCP Cloud Run / Azure App Service", "依用量計費", "最穩定、可綁定自訂網域"],
          ],
          [1600, 2600, 1400, 3760]
        ),
        ...spacer(1),

        h2("4.1  開發期使用 ngrok"),
        body("步驟一：安裝 ngrok（https://ngrok.com/download）並完成帳號設定"),
        code("ngrok config add-authtoken <你的 authtoken>"),
        ...spacer(1),
        body("步驟二：啟動本地 FastAPI 伺服器（port 8000）後，另開終端機執行："),
        code("ngrok http 8000"),
        ...spacer(1),
        body("步驟三：複製 ngrok 顯示的 Forwarding 網址，填入 LINE Console 的 Webhook URL："),
        code("https://xxxx-xx-xxx-xxx-xx.ngrok-free.app/webhook"),
        ...spacer(1),
        infoBox("注意", "每次重新啟動 ngrok，網址都會改變（免費版），需重新填入 LINE Console。開發完成後建議部署到雲端取得固定網址。", MINT),
        ...spacer(1),

        h2("4.2  雲端部署（正式上線建議）"),
        body("以 Railway 為例（最簡單的部署方式）："),
        bullet("將專案推送至 GitHub Repository"),
        bullet("在 railway.app 連結 GitHub Repo，Railway 自動偵測 Python 環境"),
        bullet("在 Railway 的 Variables 面板設定 .env 中的所有環境變數"),
        bullet("Railway 自動提供 HTTPS 網址，直接填入 LINE Webhook URL"),
        bullet("啟動指令：uvicorn backend.main:app --host 0.0.0.0 --port $PORT"),
        ...spacer(1),
        new Paragraph({ children: [new PageBreak()] }),

        // ────────────────────────────────────
        // 第五章：後端修改說明
        // ────────────────────────────────────
        h1("第五章　後端程式修改說明"),
        ...spacer(1),
        h2("5.1  新增套件"),
        body("在 requirements.txt 新增 LINE Bot SDK（Python v3 版本）："),
        code("line-bot-sdk>=3.0.0"),
        ...spacer(1),
        body("安裝指令："),
        code("pip install line-bot-sdk"),
        ...spacer(1),

        h2("5.2  環境變數新增（.env）"),
        makeTable(
          ["變數名稱", "說明", "範例"],
          [
            ["LINE_CHANNEL_SECRET", "Webhook 簽名驗證金鑰", "取自 LINE Console Basic settings"],
            ["LINE_CHANNEL_ACCESS_TOKEN", "呼叫 Reply API 用的 Access Token", "取自 LINE Console Messaging API"],
          ],
          [3000, 3660, 2700]
        ),
        ...spacer(1),
        body("config.py 新增對應設定欄位："),
        code("LINE_CHANNEL_SECRET: str = ''"),
        code("LINE_CHANNEL_ACCESS_TOKEN: str = ''"),
        ...spacer(1),

        h2("5.3  核心邏輯重構（抽成共用函式）"),
        body("原本的 /chat 端點邏輯需抽取成獨立的 process_chat() 協程函式，讓 HTTP 端點和 LINE Webhook 都能共用，避免重複程式碼："),
        ...spacer(1),
        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [9360],
          rows: [new TableRow({
            children: [new TableCell({
              borders: borders(LTEAL),
              shading: { fill: GRAY1, type: ShadingType.CLEAR },
              margins: { top: 120, bottom: 120, left: 300, right: 300 },
              width: { size: 9360, type: WidthType.DXA },
              children: [
                new Paragraph({ children: [new TextRun({ text: "# 抽取共用邏輯", font: "Courier New", size: 18, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "async def process_chat(patient_id: str, message: str) -> str:", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: '    """RAG + Prompt + Gemma 呼叫，回傳回覆文字"""', font: "Courier New", size: 18, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "    alert = detect_emergency(message)", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    profile = patient_profiles.get(patient_id)", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    rag_docs = retriever.query(message)", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    system_prompt = build_prompt(profile, rag_docs)", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    # ... Gemma 呼叫 ...", font: "Courier New", size: 18, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "    if alert.is_emergency:", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "        await manager.broadcast_alert({...})", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    return reply", font: "Courier New", size: 18, color: DARK })] }),
              ],
            })],
          })],
        }),
        ...spacer(1),

        h2("5.4  LINE Webhook 端點"),
        body("新增 /webhook 端點，負責驗證簽名、解析事件、呼叫核心邏輯、透過 LINE Reply API 回覆病患："),
        ...spacer(1),
        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [9360],
          rows: [new TableRow({
            children: [new TableCell({
              borders: borders(LTEAL),
              shading: { fill: GRAY1, type: ShadingType.CLEAR },
              margins: { top: 120, bottom: 120, left: 300, right: 300 },
              width: { size: 9360, type: WidthType.DXA },
              children: [
                new Paragraph({ children: [new TextRun({ text: "from linebot.v3 import WebhookHandler", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "from linebot.v3.messaging import (", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    Configuration, ApiClient, MessagingApi,", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    ReplyMessageRequest, TextMessage)", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "from linebot.v3.webhooks import MessageEvent, TextMessageContent", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "", font: "Courier New", size: 18 })] }),
                new Paragraph({ children: [new TextRun({ text: "line_config = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "handler = WebhookHandler(settings.LINE_CHANNEL_SECRET)", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "line_user_map: dict[str, str] = {}   # userId -> patient_id", font: "Courier New", size: 18, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "", font: "Courier New", size: 18 })] }),
                new Paragraph({ children: [new TextRun({ text: "@app.post('/webhook')", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "async def line_webhook(request: Request):", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    signature = request.headers.get('X-Line-Signature', '')", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    body = await request.body()", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    handler.handle(body.decode(), signature)  # 簽名驗證", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    return 'OK'", font: "Courier New", size: 18, color: DARK })] }),
              ],
            })],
          })],
        }),
        ...spacer(1),

        h2("5.5  病患綁定與訊息處理邏輯"),
        ...spacer(1),
        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [9360],
          rows: [new TableRow({
            children: [new TableCell({
              borders: borders(LTEAL),
              shading: { fill: GRAY1, type: ShadingType.CLEAR },
              margins: { top: 120, bottom: 120, left: 300, right: 300 },
              width: { size: 9360, type: WidthType.DXA },
              children: [
                new Paragraph({ children: [new TextRun({ text: "@handler.add(MessageEvent, message=TextMessageContent)", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "def handle_message(event: MessageEvent):", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    user_id = event.source.user_id", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    text = event.message.text.strip()", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "", font: "Courier New", size: 18 })] }),
                new Paragraph({ children: [new TextRun({ text: "    # 尚未綁定：要求輸入病患代號", font: "Courier New", size: 18, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "    if user_id not in line_user_map:", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "        if text.upper() in VALID_PATIENT_CODES:", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "            line_user_map[user_id] = text.upper()", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: '            reply = "綁定成功！您現在可以開始提問。"', font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "        else:", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: '            reply = "請先輸入您的病患代號（例如：P001）"', font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "    else:", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "        patient_id = line_user_map[user_id]", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "        # 呼叫共用 process_chat（需用 asyncio.run）", font: "Courier New", size: 18, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "        reply = asyncio.run(process_chat(patient_id, text))", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "", font: "Courier New", size: 18 })] }),
                new Paragraph({ children: [new TextRun({ text: "    with ApiClient(line_config) as api_client:", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "        MessagingApi(api_client).reply_message(", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "            ReplyMessageRequest(", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "                reply_token=event.reply_token,", font: "Courier New", size: 18, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "                messages=[TextMessage(text=reply)]))", font: "Courier New", size: 18, color: DARK })] }),
              ],
            })],
          })],
        }),
        ...spacer(1),
        new Paragraph({ children: [new PageBreak()] }),

        // ────────────────────────────────────
        // 第六章：病患身份識別設計
        // ────────────────────────────────────
        h1("第六章　病患身份識別設計"),
        ...spacer(1),
        h2("6.1  綁定流程說明"),
        body("LINE userId 是 LINE 平台指派給每位用戶的唯一識別碼（格式如 U1234abc...），但後端的病患資料是以病患代號（P001）管理。因此需要一個綁定步驟，將兩者關聯起來。"),
        ...spacer(1),

        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [9360],
          rows: [new TableRow({
            children: [new TableCell({
              borders: borders(LINE_GREEN),
              shading: { fill: LINE_LIGHT, type: ShadingType.CLEAR },
              margins: { top: 150, bottom: 150, left: 300, right: 300 },
              width: { size: 9360, type: WidthType.DXA },
              children: [
                new Paragraph({ children: [new TextRun({ text: "【綁定流程】", font: "Arial", size: 21, bold: true, color: LINE_GREEN })] }),
                new Paragraph({ children: [new TextRun({ text: "病患加入 LINE 官方帳號", font: "Arial", size: 20, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "  ↓", font: "Arial", size: 20, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "Bot 自動回覆：「歡迎！請輸入您的病患代號（如 P001）完成身份認證」", font: "Arial", size: 20, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "  ↓", font: "Arial", size: 20, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "病患輸入：P001", font: "Arial", size: 20, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "  ↓", font: "Arial", size: 20, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "後端驗證代號有效 → 儲存 line_user_map[userId] = 'P001'", font: "Arial", size: 20, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "  ↓", font: "Arial", size: 20, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "Bot 回覆：「身份綁定成功，您可以開始提問了！」", font: "Arial", size: 20, color: DARK })] }),
                new Paragraph({ children: [new TextRun({ text: "  ↓", font: "Arial", size: 20, color: "6B7280" })] }),
                new Paragraph({ children: [new TextRun({ text: "往後每次發訊息 → 直接識別為 P001，走 RAG + Gemma 流程", font: "Arial", size: 20, color: DARK })] }),
              ],
            })],
          })],
        }),
        ...spacer(1),

        h2("6.2  儲存方式比較"),
        makeTable(
          ["方式", "實作複雜度", "重啟後保留", "適用情境"],
          [
            ["記憶體 dict（目前採用）", "低", "否（重啟需重新綁定）", "開發測試、原型展示"],
            ["JSON 檔案持久化", "低", "是", "小型部署、不需資料庫"],
            ["SQLite 資料庫", "中", "是", "正式上線建議方案"],
            ["PostgreSQL / MySQL", "高", "是", "多機部署、大規模使用"],
          ],
          [2800, 1800, 1800, 2960]
        ),
        ...spacer(1),
        infoBox("建議", "正式上線時改用 SQLite 或 PostgreSQL，確保伺服器重啟後病患不需要重新綁定身份。", MINT),
        ...spacer(1),
        new Paragraph({ children: [new PageBreak()] }),

        // ────────────────────────────────────
        // 第七章：API 端點總覽（更新版）
        // ────────────────────────────────────
        h1("第七章　API 端點總覽（更新版）"),
        ...spacer(1),
        makeTable(
          ["方法", "路徑", "呼叫者", "說明", "是否新增"],
          [
            ["POST", "/webhook", "LINE Platform", "接收 LINE 事件，驗簽後處理訊息", "新增"],
            ["POST", "/chat", "（內部共用）", "RAG + Gemma 核心邏輯", "重構為共用函式"],
            ["GET", "/verify/{code}", "研究者瀏覽器", "驗證登入代號（R001）", "不變"],
            ["GET", "/sessions", "研究者瀏覽器", "取得所有 session 摘要與 Red Flag", "不變"],
            ["GET", "/history/{patient_id}", "研究者瀏覽器", "取得指定病患完整對話紀錄", "不變"],
            ["POST", "/patient/{id}", "管理者", "新增 / 更新病患資料", "不變"],
            ["WS", "/ws/nurse", "研究者瀏覽器", "接收緊急警示 WebSocket 推播", "不變"],
            ["GET", "/health", "監控工具", "系統健康狀態檢查", "不變"],
          ],
          [900, 2000, 1800, 3260, 1400]
        ),
        ...spacer(1),
        h2("7.1  /webhook 端點規格"),
        makeTable(
          ["項目", "說明"],
          [
            ["方法", "POST"],
            ["URL", "https://{domain}/webhook"],
            ["呼叫者", "LINE Platform（由 LINE 伺服器自動呼叫，非人工呼叫）"],
            ["Header", "X-Line-Signature: {HMAC-SHA256 簽名字串}"],
            ["Body", "JSON 格式 LINE Events 陣列"],
            ["回應", "200 OK（純文字 'OK'）"],
            ["簽名驗證失敗", "拋出 InvalidSignatureError，LINE SDK 自動返回 400"],
          ],
          [2200, 7160]
        ),
        ...spacer(1),
        new Paragraph({ children: [new PageBreak()] }),

        // ────────────────────────────────────
        // 第八章：安全設計
        // ────────────────────────────────────
        h1("第八章　安全設計"),
        ...spacer(1),
        h2("8.1  Webhook 簽名驗證"),
        body("所有送往 /webhook 的請求都必須通過 LINE 簽名驗證，防止偽造請求。LINE SDK 會自動處理："),
        bullet("取出 X-Line-Signature header"),
        bullet("計算 Request Body 的 HMAC-SHA256（使用 Channel Secret 為金鑰）"),
        bullet("比對兩者是否一致，不一致則拋出 InvalidSignatureError"),
        ...spacer(1),
        infoBox("安全", "Channel Secret 是 Webhook 安全的核心。請確保只有 .env 檔案中保存此值，不可硬編碼在程式碼中，不可上傳至版本控制系統（加入 .gitignore）。", MINT),
        ...spacer(1),

        h2("8.2  病患代號驗證"),
        body("綁定時驗證輸入的代號必須存在於 VALID_PATIENT_CODES 白名單，避免任意字串被當作有效病患 ID 注入。"),
        ...spacer(1),

        h2("8.3  AI 安全邊界（不變）"),
        makeTable(
          ["規則", "內容"],
          [
            ["不診斷", "Bot 不做疾病診斷，遇診斷相關問題引導就醫"],
            ["不調整藥量", "嚴禁建議增減藥物劑量或停藥"],
            ["緊急關鍵字", "偵測到高風險關鍵字立即回覆「請按呼叫鈴」並觸發 WebSocket 警示"],
            ["知識邊界", "僅依據 RAG 檢索到的衛教文件回答，資料不足時誠實說明"],
          ],
          [2400, 6960]
        ),
        ...spacer(1),
        h2("8.4  LINE 訊息長度限制"),
        infoBox("注意", "LINE 單則訊息最大 5000 字元。Gemma 回覆若超過此限制，需在 process_chat() 中加入截斷邏輯（建議限制 max_output_tokens=800 並加入安全截斷）。", MINT),
        ...spacer(1),
        new Paragraph({ children: [new PageBreak()] }),

        // ────────────────────────────────────
        // 第九章：變更影響範圍總整理
        // ────────────────────────────────────
        h1("第九章　變更影響範圍總整理"),
        ...spacer(1),
        makeTable(
          ["檔案 / 元件", "變更類型", "具體變更內容"],
          [
            ["requirements.txt", "新增依賴", "line-bot-sdk>=3.0.0"],
            [".env", "新增設定", "LINE_CHANNEL_SECRET、LINE_CHANNEL_ACCESS_TOKEN"],
            ["backend/config.py", "新增欄位", "LINE_CHANNEL_SECRET: str、LINE_CHANNEL_ACCESS_TOKEN: str"],
            ["backend/main.py", "新增約 60 行", "LINE SDK 初始化、line_user_map dict、/webhook 端點、handle_message handler、process_chat() 共用函式"],
            ["frontend/index.html", "移除病患介面", "移除或隱藏病患登入畫面與聊天介面，研究者畫面完全保留"],
            ["backend/alert.py", "不需修改", "緊急關鍵字偵測邏輯不變"],
            ["backend/rag.py", "不需修改", "向量檢索邏輯不變"],
            ["backend/prompt.py", "不需修改", "Prompt 組裝邏輯不變"],
            ["backend/websocket_manager.py", "不需修改", "WebSocket 推播邏輯不變"],
            ["chroma_db/", "不需修改", "知識庫向量資料不變"],
          ],
          [2800, 1800, 4760]
        ),
        ...spacer(1),
        h2("9.1  實作工作量估計"),
        makeTable(
          ["工作項目", "估計工時"],
          [
            ["LINE Channel 申請與設定", "30 分鐘"],
            ["ngrok 安裝與設定（開發環境）", "15 分鐘"],
            ["後端程式修改（config / main.py）", "1–2 小時"],
            ["前端病患介面移除", "30 分鐘"],
            ["端到端測試（手機 LINE 實際測試）", "1 小時"],
            ["文件更新", "30 分鐘"],
            ["合計", "約 4–5 小時"],
          ],
          [5580, 3780]
        ),
        ...spacer(1),
        new Paragraph({ children: [new PageBreak()] }),

        // ────────────────────────────────────
        // 第十章：測試計畫
        // ────────────────────────────────────
        h1("第十章　測試計畫"),
        ...spacer(1),
        h2("10.1  功能測試項目"),
        makeTable(
          ["測試項目", "測試方式", "預期結果"],
          [
            ["Webhook 簽名驗證", "LINE Console 的「Verify」按鈕", "回傳 200 OK"],
            ["未綁定用戶發訊息", "手機 LINE 直接輸入任意文字", "Bot 回覆「請先輸入病患代號」"],
            ["輸入錯誤代號", "輸入 XXXX", "Bot 回覆「代號無效」"],
            ["正確綁定", "輸入 P001", "Bot 回覆「綁定成功」"],
            ["衛教提問（正常）", "輸入「化療後嘴巴破了怎麼辦」", "Bot 依 RAG 資料回覆口腔護理建議"],
            ["緊急關鍵字", "輸入「我想死」", "Bot 回覆呼叫護理師，研究者儀表板出現紅色警示"],
            ["研究者儀表板", "瀏覽器登入 R001", "顯示 P001 的對話紀錄（包含 LINE 對話）"],
            ["WebSocket 即時推播", "輸入緊急關鍵字時儀表板已開啟", "即時收到紅色警示卡片"],
          ],
          [2800, 2600, 3960]
        ),
        ...spacer(1),
        h2("10.2  帳號資訊"),
        makeTable(
          ["角色", "登入方式", "代號 / 身份"],
          [
            ["病患", "LINE App → 加入官方帳號 → 輸入代號", "P001"],
            ["研究者", "瀏覽器 → 輸入代號", "R001"],
          ],
          [1800, 4560, 3000]
        ),
        ...spacer(1),
        new Paragraph({ children: [new PageBreak()] }),

        // ────────────────────────────────────
        // 附錄
        // ────────────────────────────────────
        h1("附錄　參考資源"),
        ...spacer(1),
        makeTable(
          ["資源", "網址 / 說明"],
          [
            ["LINE Developers Console", "developers.line.biz — 申請 Channel、取得金鑰、設定 Webhook"],
            ["LINE Bot SDK Python v3", "github.com/line/line-bot-sdk-python — 官方 SDK 文件"],
            ["LINE Messaging API 文件", "developers.line.biz/en/docs/messaging-api/ — 完整 API 規格"],
            ["ngrok 下載", "ngrok.com/download — 本地開發 HTTPS 通道"],
            ["Railway 部署平台", "railway.app — 最簡易的雲端部署方式"],
            ["Google AI Studio（Gemma）", "aistudio.google.com — API Key 管理、用量監控"],
          ],
          [2800, 6560]
        ),
        ...spacer(1),
        ...spacer(2),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          border: { top: { style: BorderStyle.SINGLE, size: 4, color: LTEAL } },
          spacing: { before: 200 },
          children: [new TextRun({ text: "AI 腫瘤衛教機器人  LINE Bot 整合版系統架構書  |  版本 2.0  |  2026 年 4 月", font: "Arial", size: 18, color: "6B7280" })],
        }),
      ],
    },
  ],
});

// 輸出
Packer.toBuffer(doc).then((buffer) => {
  const out = "腫瘤衛教機器人_LINE整合版系統架構書.docx";
  fs.writeFileSync(out, buffer);
  console.log("DONE:", require("path").resolve(out));
});
