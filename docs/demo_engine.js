// 靜態演示引擎：把後端 redflag.py / assessment.py / rag.py+quality.py / quiz.py 的規則
// 移植到瀏覽器內執行。無後端、無 LLM、無網路請求；資料只在頁面記憶體。
// 「生成」一步用規則從檢索章節組裝（對應後端 llm_client.MockClient）。
(function (root) {
  'use strict';

  // ── 紅旗（redflag.py）────────────────────────────────────────
  var HIGH_PHYSICAL = ['胸痛', '胸悶', '喘不過氣', '呼吸困難', '心跳很快', '心悸很嚴重',
    '手腳無力', '說話不清楚', '突然頭痛', '暈倒', '失去意識',
    '跌倒了', '流很多血', '大量出血', '骨折',
    '全身起疹子', '臉腫起來', '喉嚨腫', '吞不下'];
  var HIGH_SELF_HARM = ['想死', '不想活', '活不下去', '自殺', '傷害自己', '結束生命',
    '撐不下去', '活著好累', '沒有意義'];
  var MEDIUM = ['很痛', '痛很久', '發高燒', '高燒', '吐很多次', '一直吐', '一直拉肚子',
    '藥吃錯了', '忘記吃藥', '血糖很高', '血壓很高', '傷口紅腫', '尿尿有血', '血便'];
  var NEGATION = ['沒有', '沒', '不會', '不再', '未', '無', '如果', '假如', '萬一', '怕', '擔心會', '避免'];
  var NEG_WINDOW = 8;

  function negated(text, kw) {
    var idx = text.indexOf(kw);
    while (idx !== -1) {
      var win = text.slice(Math.max(0, idx - NEG_WINDOW), idx);
      if (!NEGATION.some(function (n) { return win.indexOf(n) !== -1; })) return false;
      idx = text.indexOf(kw, idx + 1);
    }
    return true;
  }

  function screenRedFlag(text) {
    var self = HIGH_SELF_HARM.filter(function (k) { return text.indexOf(k) !== -1; });
    if (self.length) return { severity: 'high', keywords: self, kind: 'self_harm' };
    var phys = HIGH_PHYSICAL.filter(function (k) { return text.indexOf(k) !== -1 && !negated(text, k); });
    if (phys.length) return { severity: 'high', keywords: phys, kind: 'physical' };
    var med = MEDIUM.filter(function (k) { return text.indexOf(k) !== -1 && !negated(text, k); });
    if (med.length) return { severity: 'medium', keywords: med, kind: 'medium' };
    return { severity: 'none', keywords: [], kind: 'none' };
  }

  // ── 症狀評估（assessment.py）─────────────────────────────────
  var ESAS_SEVERE = 7;
  var PROTOCOLS = [
    { key: 'nausea', name: '噁心嘔吐', keywords: ['想吐', '噁心', '嘔吐', '反胃', '吐了'], onc: 'ONC-21', field: 'count', question: '這一天下來大概吐了幾次呢？（請給個數字）', escalateCount: 5, escalates: true },
    { key: 'diarrhea', name: '腹瀉', keywords: ['拉肚子', '腹瀉', '一直拉', '水便'], onc: 'ONC-22', field: 'count', question: '今天大概拉了幾次呢？（請給個數字）', escalateCount: 6, escalates: true },
    { key: 'constipation', name: '便祕', keywords: ['便祕', '便秘', '大不出來', '解不出來', '沒排便'], onc: 'ONC-23', field: 'days', question: '已經幾天沒有排便了呢？（請給個數字）', escalateCount: 3, escalates: true },
    { key: 'mucositis', name: '口腔黏膜炎', keywords: ['嘴巴破', '口腔破', '口腔潰瘍', '嘴破', '黏膜'], onc: 'ONC-17', escalates: true },
    { key: 'neuropathy', name: '手腳麻木刺痛', keywords: ['手麻', '腳麻', '手腳麻', '刺痛', '麻木'], onc: 'ONC-38', escalates: true },
    { key: 'fatigue', name: '疲憊', keywords: ['很累', '疲憊', '沒力氣', '沒體力', '累到'], onc: 'ONC-37', escalates: true },
    { key: 'appetite', name: '食慾不振', keywords: ['吃不下', '沒胃口', '沒食慾', '不想吃'], onc: 'ONC-21', escalates: true },
    { key: 'rash', name: '皮膚紅疹', keywords: ['紅疹', '起疹子', '皮膚癢', '長疹子', '皮膚紅'], onc: 'ONC-40', escalates: true },
    { key: 'hair_loss', name: '落髮', keywords: ['掉髮', '落髮', '頭髮掉', '頭髮一直掉'], onc: 'ONC-24', escalates: false },
    { key: 'pain', name: '疼痛', keywords: ['好痛', '很痛', '疼痛', '在痛', '痛得'], onc: null, escalates: true }
  ];
  var BY_KEY = {};
  PROTOCOLS.forEach(function (p) { BY_KEY[p.key] = p; });
  var ABORT = ['先不用', '不用了', '算了', '沒事了', '不想說', '跳過', '不用問'];
  var CN = { '零': 0, '一': 1, '兩': 2, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9 };

  function detectSymptom(text) {
    for (var i = 0; i < PROTOCOLS.length; i++) {
      if (PROTOCOLS[i].keywords.some(function (k) { return text.indexOf(k) !== -1; })) return PROTOCOLS[i];
    }
    return null;
  }
  function cnToInt(s) {
    s = s.trim();
    if (!s) return null;
    if (s.indexOf('十') !== -1) {
      var parts = s.split('十'), left = parts[0], right = parts[1] || '';
      var tens = left ? (CN[left] !== undefined ? CN[left] : 1) : 1;
      var ones = right ? (CN[right] || 0) : 0;
      return tens * 10 + ones;
    }
    if (s.length === 1 && CN[s] !== undefined) return CN[s];
    return null;
  }
  function extractNumber(text) {
    var m = text.match(/\d+/);
    if (m) return parseInt(m[0], 10);
    m = text.match(/[零一二兩三四五六七八九十]+/);
    if (m) return cnToInt(m[0]);
    return null;
  }
  function extractCount(text, units) {
    var m = text.match(new RegExp('(\\d+)\\s*[' + units + ']'));
    if (m) return parseInt(m[1], 10);
    m = text.match(new RegExp('([零一二兩三四五六七八九十]+)\\s*[' + units + ']'));
    if (m) return cnToInt(m[1]);
    return extractNumber(text);
  }
  function has(text, words) { return words.some(function (w) { return text.indexOf(w) !== -1; }); }
  function parseSeverity(text) {
    var n = extractNumber(text);
    if (n !== null) return Math.max(0, Math.min(10, n));
    if (has(text, ['受不了', '非常', '超級', '最嚴重', '太痛', '崩潰', '撐不住'])) return 9;
    if (has(text, ['很', '蠻', '挺', '滿', '厲害', '難受'])) return 7;
    if (has(text, ['有點', '一點', '普通', '還好', '普普'])) return 4;
    if (has(text, ['輕微', '不太', '不會很', '還好啦', '沒很'])) return 2;
    if (has(text, ['不會', '沒有', '還好沒事', '不痛'])) return 0;
    return null;
  }

  // ── 檢索（rag.py 的靜態替代）─────────────────────────────────
  var STOP = '的了嗎我你您要什麼怎麼可以請問是有在會不會呢啊吧喔哦嘛也還都很就跟和與或之後之前一直現在今天';
  function bigrams(s) {
    s = s.replace(/[\s，。、；：！？（）()「」『』【】\[\]…—\-\.,!?:;"'0-9０-９]/g, '');
    s = s.split('').filter(function (c) { return STOP.indexOf(c) === -1; }).join('');
    var set = {};
    for (var i = 0; i + 1 < s.length; i++) set[s.slice(i, i + 2)] = true;
    return set;
  }
  function retrieve(query, data, opts) {
    opts = opts || {};
    var qb = bigrams(query), qn = Object.keys(qb).length || 1;
    var out = [];
    data.leaflets.forEach(function (lf) {
      if (opts.code && lf.code !== opts.code) return;
      var kwHits = lf.keywords.filter(function (k) { return query.indexOf(k) !== -1; }).length;
      var kwScore = Math.min(kwHits, 3) / 3;
      lf.sections.forEach(function (sec) {
        var sb = bigrams(sec.title + sec.text), hit = 0;
        for (var g in qb) if (sb[g]) hit++;
        var overlap = hit / qn;
        var score = opts.code ? 0.5 + 0.5 * overlap : 0.6 * kwScore + 0.4 * overlap;
        out.push({ code: lf.code, topic: lf.topic, section: sec.title, text: sec.text,
          score: Math.round(score * 1000) / 1000, kwHits: kwHits, overlap: Math.round(overlap * 100) / 100 });
      });
    });
    out.sort(function (a, b) { return b.score - a.score; });
    return out.slice(0, opts.topK || 5);
  }
  var MIN_SCORE = 0.2;   // 低於此視為「無可用來源」→ 品質閘轉介

  var DEFLECT_TEXT = '這個問題我需要請護理師為您解答，我先幫您記錄下來，護理師會盡快回覆您。';

  // ── 生成（對應 MockClient）───────────────────────────────────
  function sentences(text) {
    return text.split(/[。；\n]+/).map(function (s) {
      return s.replace(/^[\s\-•*　]+/, '').replace(/^[（(][一二三四五六七八九十\d]+[）)]/, '').trim();
    }).filter(function (s) { return s.length >= 8 && !/[：:]$/.test(s); });
  }
  function compose(docs, tone) {
    var cfg = { simple: [1, 2], general: [2, 3], detailed: [2, 4] }[tone] || [2, 3];
    var byLeaflet = {}, order = [];
    docs.filter(function (d) { return d.score >= MIN_SCORE; }).forEach(function (d) {
      var key = d.code + ' ' + d.topic;
      if (!byLeaflet[key]) { byLeaflet[key] = []; order.push(key); }
      sentences(d.text).forEach(function (s) { if (byLeaflet[key].indexOf(s) === -1) byLeaflet[key].push(s); });
    });
    if (!order.length) return null;
    var lines = [];
    order.slice(0, cfg[0]).forEach(function (key) {
      lines.push('根據衛教資料《' + key + '》：');
      byLeaflet[key].slice(0, cfg[1]).forEach(function (p) { lines.push('• ' + p + '。'); });
      lines.push('');
    });
    lines.push(tone === 'simple' ? '慢慢來，有不舒服隨時跟護理師說。' : '以上內容僅供衛教參考，若症狀持續或加重，請告知您的醫療團隊。');
    return lines.join('\n');
  }

  // ── 對話流程（main.py /chat）─────────────────────────────────
  var HIGH_REPLY = '這個狀況需要馬上讓護理師或醫師知道，請按旁邊的呼叫鈴，我已經同時通知護理站了。若情況緊急請直接撥打護理站電話或119。';

  function createSession(patient) {
    return { patient: patient, messages: [], assessment: null, scores: [], alerts: [], quiz: [], events: [] };
  }
  function now() { return new Date().toISOString(); }
  function log(s, type, payload) { s.events.push({ ts: now(), type: type, payload: payload || {} }); }

  function chat(session, text, data) {
    var s = session, trace = [], resp;
    s.messages.push({ role: 'patient', text: text, ts: now() });

    // ① 紅旗
    var flag = screenRedFlag(text);
    trace.push({ step: '① 紅旗篩檢', result: flag.severity === 'none' ? '未命中' :
      (flag.severity.toUpperCase() + '（' + flag.keywords.join('、') + (flag.kind === 'self_harm' ? '；自傷類不套否定規則' : '') + '）') });
    if (flag.severity === 'high') {
      s.alerts.push({ ts: now(), level: 'high', trigger: flag.keywords.join('；'), type: 'EMERGENCY_ALERT', message: text });
      log(s, 'redflag_high', { terms: flag.keywords });
      trace.push({ step: '短路', result: '立刻推播護理站 + 固定安全回覆，不進 RAG／LLM' });
      resp = { reply: HIGH_REPLY, sources: [], emergency: true, level: 'high', keywords: flag.keywords, quality: 'redflag_shortcut', trace: trace };
      s.messages.push({ role: 'bot', text: resp.reply, ts: now(), resp: resp });
      return resp;
    }
    var mediumAlert = function () {
      if (flag.severity === 'medium') s.alerts.push({ ts: now(), level: 'medium', trigger: flag.keywords.join('；'), type: 'MEDIUM_FLAG', message: text, silent: true });
    };

    // ② 症狀評估
    if (s.assessment) {
      var st = s.assessment, proto = BY_KEY[st.symptom];
      if (has(text, ABORT)) {
        s.assessment = null; log(s, 'assessment_abort', { symptom: proto.key });
        trace.push({ step: '② 症狀評估', result: '病患中止，清除狀態' });
        return finish(s, '好的，那我們先不談這個。您有其他想了解的嗎？', [], 'assessment', trace, flag, mediumAlert);
      }
      if (st.step === 'ask_severity') {
        var sc = parseSeverity(text);
        if (sc === null) {
          trace.push({ step: '② 症狀評估', result: '解析不出 0–10 分 → 重問' });
          return finish(s, '方便用 0 到 10 的數字告訴我嗎？（0＝完全不會，10＝最嚴重）', [], 'assessment', trace, flag, mediumAlert);
        }
        st.data.score = sc;
        if (proto.field) {
          st.step = 'ask_field';
          trace.push({ step: '② 症狀評估', result: proto.name + ' 嚴重度 ' + sc + '/10 → 續問' + (proto.field === 'count' ? '次數' : '天數') });
          return finish(s, proto.question, [], 'assessment', trace, flag, mediumAlert);
        }
        return complete(s, proto, st.data, trace, flag, data, mediumAlert);
      }
      if (st.step === 'ask_field') {
        var cnt = extractCount(text, proto.field === 'count' ? '次回趟遍' : '天日');
        if (cnt !== null) st.data[proto.field] = cnt;
        return complete(s, proto, st.data, trace, flag, data, mediumAlert);
      }
    } else {
      var p = detectSymptom(text);
      if (p) {
        s.assessment = { symptom: p.key, step: 'ask_severity', data: {} };
        log(s, 'assessment_start', { symptom: p.key });
        trace.push({ step: '② 症狀評估', result: '偵測到「' + p.name + '」→ 開始 ESAS-r 0–10 追問（不進 RAG／LLM）' });
        return finish(s, '聽起來您有「' + p.name + '」的不舒服，我先簡單了解一下狀況。如果 0 分是完全不會、10 分是最嚴重，您會給幾分呢？', [], 'assessment', trace, flag, mediumAlert);
      }
      trace.push({ step: '② 症狀評估', result: '未偵測到症狀關鍵字' });
    }

    // ③ RAG
    return ragAnswer(s, text, null, '', trace, flag, data, mediumAlert);
  }

  function complete(s, proto, d, trace, flag, data, mediumAlert) {
    var score = d.score, extra = {};
    for (var k in d) if (k !== 'score') extra[k] = d[k];
    s.scores.push({ ts: now(), symptom: proto.key, name: proto.name, score: score, extra: extra });
    s.assessment = null;
    var overScore = score !== null && score >= ESAS_SEVERE;
    var overCount = proto.escalateCount != null && extra[proto.field] != null && extra[proto.field] >= proto.escalateCount;
    var escalate = proto.escalates && (overScore || overCount);
    log(s, 'assessment_complete', { symptom: proto.key, score: score, extra: extra, escalate: escalate });
    var fieldTxt = extra[proto.field] != null ? ('，' + (proto.field === 'count' ? '次數約 ' : '天數約 ') + extra[proto.field] + (proto.field === 'count' ? ' 次' : ' 天')) : '';
    if (escalate) {
      s.alerts.push({ ts: now(), level: 'medium', trigger: proto.key + ':' + score, type: 'SYMPTOM_ALERT', message: proto.name + ' 嚴重度 ' + score + '/10' + fieldTxt });
      trace.push({ step: '② 症狀評估', result: '達門檻（≥' + ESAS_SEVERE + ' 分' + (proto.escalateCount ? '或 ≥' + proto.escalateCount + ' ' + (proto.field === 'count' ? '次' : '天') : '') + '）→ 升級、推播護理站' });
      var reply = '您的「' + proto.name + '」聽起來比較嚴重（嚴重度 ' + score + '/10' + fieldTxt + '）。我已經幫您記錄並通知護理師，請稍候，也可以直接按呼叫鈴。';
      var resp = { reply: reply, sources: [], emergency: true, level: 'medium', keywords: [proto.key], quality: 'assessment_escalated', trace: trace };
      s.messages.push({ role: 'bot', text: reply, ts: now(), resp: resp });
      return resp;
    }
    if (!proto.escalates) trace.push({ step: '② 症狀評估', result: proto.name + ' ' + score + '/10；此症狀永不升級（心理支持）→ 衛教' });
    else trace.push({ step: '② 症狀評估', result: proto.name + ' ' + score + '/10' + fieldTxt + '，未達門檻 → 衛教' });
    var lead = '了解了，您的「' + proto.name + '」嚴重度是 ' + score + '/10。以下提供一些照護建議：\n\n';
    return ragAnswer(s, proto.name + '的自我照顧與注意事項', proto.onc, lead, trace, flag, data, mediumAlert);
  }

  function ragAnswer(s, query, code, lead, trace, flag, data, mediumAlert) {
    var docs = retrieve(query, data, { code: code });
    var good = docs.filter(function (d) { return d.score >= MIN_SCORE; });
    trace.push({ step: '③ 檢索', result: (code ? '鎖定 ' + code + '；' : '') + (docs.length ? docs.slice(0, 3).map(function (d) { return d.code + '／' + d.section + '（' + d.score + '）'; }).join('、') : '無結果') });
    if (!good.length) {
      trace.push({ step: '④ 品質閘', result: '無合格來源（<' + MIN_SCORE + '）→ 不進 LLM，固定轉介文字' });
      return finish(s, DEFLECT_TEXT, [], 'deflected_no_source', trace, flag, mediumAlert);
    }
    var text = compose(good, s.patient.tone);
    trace.push({ step: '④ 生成', result: '規則組裝（' + s.patient.tone + ' 語氣）；正式版由 LLM 依病患年齡／教育程度改寫' });
    trace.push({ step: '⑤ 品質閘', result: 'grounded（有來源、未自述無法回答）' });
    var sources = [];
    good.forEach(function (d) { var k = d.code + ' ' + d.topic; if (sources.indexOf(k) === -1) sources.push(k); });
    return finish(s, lead + text, sources.slice(0, 2), 'grounded', trace, flag, mediumAlert);
  }

  function finish(s, reply, sources, quality, trace, flag, mediumAlert) {
    mediumAlert();
    var resp = { reply: reply, sources: sources, emergency: false, level: flag.severity, keywords: flag.keywords, quality: quality, trace: trace };
    s.messages.push({ role: 'bot', text: reply, ts: now(), resp: resp });
    return resp;
  }

  // ── 測驗（quiz.py）───────────────────────────────────────────
  function quizQuestions(data, onc) {
    var d = data.quiz[onc];
    if (!d) return null;
    return { onc: onc, topic: d.topic, questions: d.questions.map(function (q) {
      return { n: q.n, type: q.type, text: q.text, options: q.type === 'mc' ? q.options : ['是 (O)', '否 (X)'] };
    }) };
  }
  function quizScore(session, data, onc, answers, phase) {
    var d = data.quiz[onc];
    if (!d) return null;
    var details = [], correct = 0;
    d.questions.forEach(function (q) {
      var your = String(answers[q.n] || '').trim().toUpperCase(), ok = your === String(q.answer).toUpperCase();
      if (ok) correct++;
      details.push({ n: q.n, your: your, answer: q.answer, correct: ok, text: q.text });
    });
    session.quiz.push({ ts: now(), onc: onc, phase: phase, score: correct, total: d.questions.length });
    log(session, 'quiz_submit', { onc: onc, phase: phase, score: correct });
    return { onc: onc, topic: d.topic, phase: phase, score: correct, total: d.questions.length, details: details };
  }

  var Engine = {
    screenRedFlag: screenRedFlag, detectSymptom: detectSymptom, parseSeverity: parseSeverity,
    extractCount: extractCount, retrieve: retrieve, compose: compose, createSession: createSession,
    chat: chat, quizQuestions: quizQuestions, quizScore: quizScore, PROTOCOLS: PROTOCOLS,
    MIN_SCORE: MIN_SCORE, DEFLECT_TEXT: DEFLECT_TEXT, HIGH_REPLY: HIGH_REPLY
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = Engine;
  else root.DemoEngine = Engine;
})(typeof window !== 'undefined' ? window : this);
