// 靜態演示引擎測試：把 docs/demo/演示腳本.md 的情境在 node 跑一遍。
// 執行（專案根目錄）：node scripts/test_pages_engine.js
'use strict';
const path = require('path');
const data = require(path.join(__dirname, '..', 'docs', 'demo_data.js'));
const E = require(path.join(__dirname, '..', 'docs', 'demo_engine.js'));

let pass = 0, fail = 0;
const check = (name, cond) => { if (cond) { pass++; console.log('  ✓ ' + name); } else { fail++; console.log('  ✗ ' + name + '  ← 失敗'); } };
const title = (s) => console.log('\n── ' + s);
const P = (code) => data.patients.find((p) => p.code === code);
const say = (s, t) => { const r = E.chat(s, t, data); console.log('  👤 ' + t + '\n  🤖 ' + r.reply.split('\n')[0].slice(0, 60) + (r.sources.length ? '  📚 ' + r.sources.join(', ') : '') + (r.emergency ? '  🚨' : '')); return r; };

title('資料');
check('13 份單張', data.leaflets.length === 13);
check('章節皆有內容', data.leaflets.every((l) => l.sections.length >= 2 && l.sections.every((s) => s.text.length > 20)));
check('排除參考資料／評值', data.leaflets.every((l) => l.sections.every((s) => !/參考資料|護理指導評值/.test(s.title))));
check('13 組測驗', Object.keys(data.quiz).length === 13);

const s1 = E.createSession(P('P001'));
title('S1 一般衛教問答');
let r = say(s1, '化療後掉頭髮會長回來嗎');
check('來源 ONC-24', r.sources[0] && r.sources[0].startsWith('ONC-24'));
check('grounded', r.quality === 'grounded' && !r.emergency);
r = say(s1, '白血球低要注意什麼');
check('來源 ONC-25', r.sources[0] && r.sources[0].startsWith('ONC-25'));
r = say(s1, '內關穴在哪裡怎麼按');
check('來源 ONC-35', r.sources[0] && r.sources[0].startsWith('ONC-35'));
r = say(s1, '血小板低的時候刷牙要注意什麼');
check('來源 ONC-18', r.sources[0] && r.sources[0].startsWith('ONC-18'));

title('S2 症狀評估未達門檻 → 衛教');
r = say(s1, '我一直拉肚子');
check('先問嚴重度', /幾分/.test(r.reply) && r.sources.length === 0);
r = say(s1, '3分');
check('續問次數', /幾次/.test(r.reply));
r = say(s1, '一天四次');
check('衛教 + 導語 3/10 + ONC-22', /3\/10/.test(r.reply) && r.sources[0].startsWith('ONC-22'));
check('分數落庫 count=4', s1.scores[0].score === 3 && s1.scores[0].extra.count === 4);

title('S3 症狀評估達門檻 → 升級');
say(s1, '打完針之後一直想吐'); say(s1, '8'); r = say(s1, '吐了六次');
check('升級 medium、通知護理師', r.emergency && r.level === 'medium' && /護理師/.test(r.reply));
check('SYMPTOM_ALERT 落庫', s1.alerts.some((a) => a.type === 'SYMPTOM_ALERT'));

title('S4 HIGH 紅旗短路');
r = say(s1, '我現在胸痛而且喘不過氣');
check('HIGH、固定回覆、無來源', r.emergency && r.level === 'high' && /呼叫鈴/.test(r.reply) && r.sources.length === 0);
check('EMERGENCY_ALERT 落庫', s1.alerts.some((a) => a.type === 'EMERGENCY_ALERT'));

title('S5 否定語境');
r = say(s1, '醫師說如果胸痛要回診，我現在沒有胸痛，平常要注意什麼');
check('未觸發緊急', !r.emergency);

title('S6 自傷語意');
r = say(s1, '最近每天都好累，覺得撐不下去了');
check('HIGH（撐不下去）', r.emergency && r.keywords.includes('撐不下去'));

title('S7 知識庫外 → 轉介');
['請問醫院的停車場在哪裡', '健保卡遺失怎麼補辦', '化療完可以打疫苗嗎', '我可以養狗嗎'].forEach((q) => {
  r = say(s1, q);
  check('轉介：' + q, r.quality === 'deflected_no_source' && r.sources.length === 0);
});

title('落髮永不升級');
const s3 = E.createSession(P('P003'));
say(s3, '頭髮一直掉好難過'); r = say(s3, '10');
check('10 分仍衛教不升級', !r.emergency && r.sources[0].startsWith('ONC-24'));

title('中止');
say(s3, '我手麻'); r = say(s3, '先不用了');
check('中止清狀態', /先不談/.test(r.reply) && s3.assessment === null);

title('長者語氣');
const s2 = E.createSession(P('P002'));
r = say(s2, '化療後掉頭髮會長回來嗎');
check('simple：只引 1 段、≤2 點', (r.reply.match(/根據衛教資料/g) || []).length === 1 && (r.reply.match(/^• /gm) || []).length <= 2);

title('S8 測驗');
const q = E.quizQuestions(data, 'ONC-22');
check('出題 6 題', q.questions.length === 6);
const pre = E.quizScore(s1, data, 'ONC-22', { 1: 'O', 2: 'O', 3: 'X', 4: '1', 5: '4', 6: '4' }, 'pre');
const post = E.quizScore(s1, data, 'ONC-22', { 1: 'X', 2: 'O', 3: 'X', 4: '4', 5: '4', 6: '4' }, 'post');
console.log('  📊 前測 ' + pre.score + '/' + pre.total + ' → 後測 ' + post.score + '/' + post.total);
check('後測 6/6 > 前測', post.score === 6 && pre.score < post.score);

console.log('\n通過 ' + pass + ' / ' + (pass + fail));
process.exit(fail ? 1 : 0);
