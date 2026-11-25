// 靜態演示引擎：把後端 redflag.py / assessment.py / rag.py+quality.py / quiz.py 的規則
// 移植到瀏覽器內執行。無後端、無 LLM、無網路請求；資料只在頁面記憶體。
// 「生成」一步用規則從檢索章節組裝（對應後端 llm_client.MockClient）。
(function (root) {
  'use strict';

  // ── 紅旗（redflag.py v2）─────────────────────────────────────
  // 規則（詞庫、否定詞、假設語境、體溫樣式）一律來自 data.redflag —— 由 scripts/build_pages.py
  // 從 backend/redflag.py 匯出，演示引擎不另外手抄，避免兩邊分歧。邏輯對應 redflag._keyword_screen；
  // 一致性由 scripts/test_pages_engine.js 以 backend/redflag_cases.json 驗證。
  var _rfCache = null;
  function rf(data) {
    if (_rfCache && _rfCache.src === data.redflag) return _rfCache;
    var R = data.redflag;
    _rfCache = {
      src: R,
      split: new RegExp(R.clauseSplit, 'g'),
      temp: new RegExp(R.tempRe, 'g'), tempCtx: new RegExp(R.tempCtxRe, 'g'), zh: new RegExp(R.zhTempRe, 'g')
    };
    return _rfCache;
  }
  function normalizeText(t) { return String(t || '').normalize('NFKC').replace(/\s+/g, '').toLowerCase(); }
  function splitClauses(t, data) { return t.split(rf(data).split).filter(function (c) { return c; }); }
  function findAll(clause, pat) {
    var out = [];
    if (pat.indexOf('re:') === 0) {
      var re = new RegExp(pat.slice(3), 'g'), m;
      while ((m = re.exec(clause)) !== null) { out.push({ pos: m.index, label: m[0] }); if (m[0] === '') re.lastIndex++; }
      return out;
    }
    var i = clause.indexOf(pat);
    while (i !== -1) { out.push({ pos: i, label: pat }); i = clause.indexOf(pat, i + 1); }
    return out;
  }
  function endsWith(s, x) { return s.length >= x.length && s.slice(s.length - x.length) === x; }
  function immediatelyNegated(prefix, R) {
    var p = prefix, changed = true;
    while (changed && p) {
      changed = false;
      for (var i = 0; i < R.fillers.length; i++) {
        if (endsWith(p, R.fillers[i])) { p = p.slice(0, p.length - R.fillers[i].length); changed = true; }
      }
    }
    return R.negCues.some(function (n) { return endsWith(p, n); });
  }
  function suppressed(clause, pos, R) {
    var prefix = clause.slice(0, pos);
    if (R.hypothetical.some(function (h) { return prefix.indexOf(h) !== -1; })) return true;
    return immediatelyNegated(prefix, R);
  }
  function matchRules(clauses, rules, R) {
    var hits = [];
    rules.forEach(function (rule) {
      var cat = rule[0], pat = rule[1];
      for (var c = 0; c < clauses.length; c++) {
        var cl = clauses[c];
        var hit = findAll(cl, pat).filter(function (h) { return !suppressed(cl, h.pos, R); })[0];
        if (hit) { hits.push([cat, hit.label]); break; }
      }
    });
    return hits;
  }
  function parseTemperature(clause, data) {
    var R = data.redflag, X = rf(data), vals = [], m;
    if (R.ambient.some(function (a) { return clause.indexOf(a) !== -1; })) return null;
    [X.temp, X.tempCtx].forEach(function (re) {
      re.lastIndex = 0;
      while ((m = re.exec(clause)) !== null) vals.push(parseFloat(m[1]));
    });
    if (/度|燒|溫/.test(clause)) {
      X.zh.lastIndex = 0;
      while ((m = X.zh.exec(clause)) !== null) vals.push(R.zhNum[m[1]] + (m[2] ? 0.5 : 0));
    }
    vals = vals.filter(function (v) { return v >= 35 && v <= 43; });
    return vals.length ? Math.max.apply(null, vals) : null;
  }
  function fmtTemp(t) { return String(Math.round(t * 10) / 10); }
  function temperatureScreen(clauses, data) {
    var R = data.redflag, best = null;
    clauses.forEach(function (cl) {
      var t = parseTemperature(cl, data);
      if (t === null) return;
      var m = /\d|三十|四十/.exec(cl);
      if (m && immediatelyNegated(cl.slice(0, m.index), R)) return;
      if (R.hypothetical.some(function (h) { return cl.indexOf(h) !== -1; })) return;
      if (best === null || t > best) best = t;
    });
    if (best === null) return null;
    // 門檻依科別設定（data.redflag.tempHigh／tempMedium；null 代表不判）
    if (R.tempHigh != null && best >= R.tempHigh) return ['high', '體溫' + fmtTemp(best) + '°C'];
    if (R.tempMedium != null && best >= R.tempMedium) return ['medium', '體溫' + fmtTemp(best) + '°C'];
    return null;
  }
  function uniq(a) { return a.filter(function (x, i) { return a.indexOf(x) === i; }); }

  function screenRedFlag(text, data) {
    var R = data.redflag, norm = normalizeText(text), cl = splitClauses(norm, data);
    var self = R.selfHarm.filter(function (k) { return norm.indexOf(k) !== -1; });
    if (self.length) return { severity: 'high', keywords: self, kind: 'self_harm', category: 'self_harm' };
    var temp = temperatureScreen(cl, data);
    var high = matchRules(cl, R.high, R);
    if (temp && temp[0] === 'high') high.unshift(['fever', temp[1]]);
    if (high.length) return { severity: 'high', keywords: uniq(high.map(function (h) { return h[1]; })), kind: 'physical', category: high[0][0] };
    var med = matchRules(cl, R.medium, R);
    if (temp && temp[0] === 'medium') med.unshift(['fever', temp[1]]);
    if (med.length) return { severity: 'medium', keywords: uniq(med.map(function (h) { return h[1]; })), kind: 'medium', category: med[0][0] };
    return { severity: 'none', keywords: [], kind: 'none', category: 'none' };
  }

  // ── 症狀評估（assessment.py）─────────────────────────────────
  var ESAS_SEVERE = 7;
  // 症狀評估協定來自 data.protocols（scripts/build_pages.py 從預設科別的 knowledge/<科別>/manifest.json 匯出），
  // 不在引擎裡另外手抄。
  function protocolsOf(data) { return (data && data.protocols) || []; }
  function protocolByKey(data, key) {
    var list = protocolsOf(data);
    for (var i = 0; i < list.length; i++) if (list[i].key === key) return list[i];
    return null;
  }
  var ABORT = ['先不用', '不用了', '算了', '沒事了', '不想說', '跳過', '不用問'];
  var CN = { '零': 0, '一': 1, '兩': 2, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9 };

  // 資訊型提問（預防、假設、原因）且沒有描述當下狀況 → 不進評估。線索來自 data.assessmentCues（assessment.py 匯出）
  function isInfoQuestion(text, data) {
    var C = data && data.assessmentCues;
    if (!C) return false;
    var has = function (list) { return list.some(function (k) { return text.indexOf(k) !== -1; }); };
    return has(C.info) && !has(C.present);
  }
  function detectSymptom(text, data) {
    if (isInfoQuestion(text, data)) return null;
    var list = protocolsOf(data);
    for (var i = 0; i < list.length; i++) {
      if (list[i].keywords.some(function (k) { return text.indexOf(k) !== -1; })) return list[i];
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

  // 病患端固定文字一律來自 data.safetyText（由 backend/safety_text.py 匯出）

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
  function highReply(data, category) {
    var t = data.safetyText.emergency;
    return t[category] || t.other;
  }

  function createSession(patient) {
    return { patient: patient, messages: [], assessment: null, scores: [], alerts: [], quiz: [], events: [] };
  }
  function now() { return new Date().toISOString(); }
  function log(s, type, payload) { s.events.push({ ts: now(), type: type, payload: payload || {} }); }

  function chat(session, text, data) {
    var s = session, trace = [], resp;
    s.messages.push({ role: 'patient', text: text, ts: now() });

    // ① 紅旗
    var flag = screenRedFlag(text, data);
    trace.push({ step: '① 紅旗篩檢', result: flag.severity === 'none' ? '未命中' :
      (flag.severity.toUpperCase() + '（' + flag.keywords.join('、') + (flag.kind === 'self_harm' ? '；自傷類不套否定規則' : '') + '）') });
    if (flag.severity === 'high') {
      s.alerts.push({ ts: now(), level: 'high', trigger: flag.keywords.join('；'), type: 'EMERGENCY_ALERT', message: text });
      log(s, 'redflag_high', { terms: flag.keywords });
      trace.push({ step: '短路', result: '立刻推播護理站 + 固定安全回覆，不進 RAG／LLM' });
      resp = { reply: highReply(data, flag.category), sources: [], emergency: true, level: 'high', keywords: flag.keywords, quality: 'redflag_shortcut', trace: trace };
      s.messages.push({ role: 'bot', text: resp.reply, ts: now(), resp: resp });
      return resp;
    }
    var mediumAlert = function () {
      if (flag.severity === 'medium') s.alerts.push({ ts: now(), level: 'medium', trigger: flag.keywords.join('；'), type: 'MEDIUM_FLAG', message: text });   // v2：MEDIUM 也推播、進未確認清單
    };

    // ② 症狀評估
    if (s.assessment) {
      var st = s.assessment, proto = protocolByKey(data, st.symptom);
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
      var p = detectSymptom(text, data);
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
    var overHigh = proto.highCount != null && extra[proto.field] != null && extra[proto.field] >= proto.highCount;
    var level = escalate && overHigh ? 'high' : 'medium';
    log(s, 'assessment_complete', { symptom: proto.key, score: score, extra: extra, escalate: escalate });
    var fieldTxt = extra[proto.field] != null ? ('，' + (proto.field === 'count' ? '次數約 ' : '天數約 ') + extra[proto.field] + (proto.field === 'count' ? ' 次' : ' 天')) : '';
    if (escalate) {
      s.alerts.push({ ts: now(), level: level, trigger: proto.key + ':' + score, type: 'SYMPTOM_ALERT', message: proto.name + ' 嚴重度 ' + score + '/10' + fieldTxt });
      trace.push({ step: '② 症狀評估', result: '達門檻（≥' + ESAS_SEVERE + ' 分' + (proto.escalateCount ? '或 ≥' + proto.escalateCount + ' ' + (proto.field === 'count' ? '次' : '天') : '') + '）→ 升級' + (level === 'high' ? ' HIGH（CTCAE grade 3）' : '') + '、推播護理站' });
      var reply = '您的「' + proto.name + '」聽起來比較嚴重（嚴重度 ' + score + '/10' + fieldTxt + '）。' + (level === 'high' ? data.safetyText.escalation_high_note : '') + data.safetyText.escalation_suffix_web;
      var resp = { reply: reply, sources: [], emergency: true, level: level, keywords: [proto.key], quality: 'assessment_escalated', trace: trace };
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
      return finish(s, data.safetyText.deflect, [], 'deflected_no_source', trace, flag, mediumAlert);
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
    chat: chat, quizQuestions: quizQuestions, quizScore: quizScore, protocolsOf: protocolsOf,
    MIN_SCORE: MIN_SCORE, highReply: highReply, normalizeText: normalizeText
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = Engine;
  else root.DemoEngine = Engine;
})(typeof window !== 'undefined' ? window : this);
