// 天声小爱 前端逻辑
const $ = (id) => document.getElementById(id);
let running = false;
let busy = false;
let configLoaded = false;

// ---------- 状态条 ----------
function setStat(id, state, text) {
  const el = $(id);
  if (!el) return;
  el.classList.toggle('on', state === 'on');
  el.classList.toggle('warn', state === 'warn');
  const val = el.querySelector('.stat-val');
  if (val && text !== undefined) val.textContent = text;
}

async function refreshStatus() {
  try {
    const r = await fetch('/api/status');
    const st = await r.json();
    running = st.running;

    const svc = Object.fromEntries((st.services || []).map(s => [s.name, s]));
    const box = st.box || {};

    // AI 后端
    const cb = svc.codebuddy;
    if (st.codebuddy_api) setStat('st-codebuddy', 'on', '已就绪');
    else if (cb?.alive) setStat('st-codebuddy', 'warn', '启动中…');
    else setStat('st-codebuddy', '', '未启动');

    // 网关
    const gw = svc.gateway;
    if (st.gateway_api) setStat('st-gateway', 'on', '8008 在线');
    else if (gw?.alive) setStat('st-gateway', 'warn', '启动中…');
    else setStat('st-gateway', '', '未启动');

    // 音箱监听
    const n = st.listener_count ?? 0;
    if (svc.xiaogpt?.alive && n === 1) setStat('st-xiaogpt', 'on', '监听中');
    else if (n > 1) setStat('st-xiaogpt', 'warn', `${n} 个实例冲突`);
    else if (svc.xiaogpt?.alive) setStat('st-xiaogpt', 'warn', '运行中');
    else setStat('st-xiaogpt', '', '未启动');

    // 小爱音箱：用后端实测的 token 有效性 + 设备 presence，而不是只看进程存活
    const sp = st.speaker || {};
    if (sp.token_valid === false) {
      setStat('st-speaker', 'warn', '登录已失效');
    } else if (sp.online === true) {
      setStat('st-speaker', 'on', sp.name || '已连接');
    } else if (sp.online === false) {
      setStat('st-speaker', 'warn', '音箱离线');
    } else if (sp.state === 'net') {
      setStat('st-speaker', 'warn', '网络异常');
    } else {
      // 尚未探测（刚启动）
      setStat('st-speaker', svc.xiaogpt?.alive ? 'warn' : '', svc.xiaogpt?.alive ? '检测中…' : '未连接');
    }

    // 小米盒子
    if (box.alive && box.adb_connected) setStat('st-box', 'on', box.serial || '已连接');
    else if (box.alive) setStat('st-box', 'warn', 'ADB 未连接');
    else setStat('st-box', '', '未启动');

    const btn = $('power');
    btn.classList.toggle('on', running);
    btn.classList.toggle('off', !running);
    $('power-text').textContent = running ? '停止' : '启动';
    btn.classList.toggle('busy', busy);

    if (st.config) applyConfigToForm(st.config, false);
  } catch (e) {
    ['st-codebuddy', 'st-gateway', 'st-xiaogpt', 'st-speaker', 'st-box'].forEach(id => setStat(id, '', '—'));
  }
}

// ---------- 表单 ----------
function applyConfigToForm(cfg, force) {
  if (configLoaded && !force) return;
  configLoaded = true;
  $('keyword').value = cfg.keyword || '天声小爱';
  const firstKw = (cfg.keyword || '天声小爱').split(',')[0].trim() || '天声小爱';
  if (cfg.no_keyword) {
    $('chat-hint').textContent = '已开启免唤醒 · 直接说话即可';
  } else {
    $('chat-hint').innerHTML = '说“<b id="kw-hint">' + firstKw + '</b>”开头即可唤醒';
  }
  $('box-ip').value = cfg.box_ip || '';
  $('no-keyword').checked = !!cfg.no_keyword;
  syncKeywordState();
  $('prompt').value = cfg.prompt || '';
  $('mute').checked = cfg.mute_xiaoai !== false;
  $('stream').checked = cfg.stream !== false;
  $('transition').value = cfg.transition ?? '正在问天声小爱，请稍等';
  if (cfg.tts === 'mi') $('voice').value = 'mi';
  else if (cfg.voice) $('voice').value = cfg.voice;
  const r = parseInt((cfg.rate || '+10%').replace(/[^0-9-]/g, ''), 10) || 10;
  $('rate').value = r;
  $('rate-val').textContent = `+${r}%`;
}

async function loadVoices() {
  const r = await fetch('/api/voices');
  const list = await r.json();
  const groups = {};
  list.forEach(v => { const g = v.group || ''; (groups[g] = groups[g] || []).push(v); });
  $('voice').innerHTML = Object.entries(groups).map(([g, items]) => {
    const opts = items.map(v => `<option value="${v.id}">${v.name} — ${v.desc}</option>`).join('');
    return g ? `<optgroup label="${g}">${opts}</optgroup>` : opts;
  }).join('');
}

// ---------- 对话 ----------
function fmtTime(ms) {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function esc(s) {
  return String(s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function bubbleHTML(it) {
  const srcTag = it.source === 'box' ? '<span class="src">米盒</span>' : '';
  return `
    <div class="msg">
      <div class="time">${fmtTime(it.time)}</div>
      <div class="bubble user">${srcTag}${esc(it.query)}</div>
      ${it.answer ? `<div class="bubble assistant"><div class="who ai">天声小爱</div>${esc(it.answer)}</div>` : ''}
    </div>`;
}

function renderDialogue(items) {
  const chat = $('chat');
  if (!items.length) {
    chat.innerHTML = '<div class="empty">还没有对话<br><span>对音箱或盒子说“问天声小爱，你能做什么”试试</span></div>';
    return;
  }
  chat.innerHTML = items.map(bubbleHTML).join('');
  chat.scrollTop = chat.scrollHeight;
}

async function loadDialogue() {
  const r = await fetch('/api/dialogue?limit=200');
  renderDialogue(await r.json());
}

function startStream() {
  const es = new EventSource('/api/dialogue/stream');
  es.onmessage = (ev) => {
    try {
      const item = JSON.parse(ev.data);
      const chat = $('chat');
      if (chat.querySelector('.empty')) chat.innerHTML = '';
      chat.insertAdjacentHTML('beforeend', bubbleHTML(item));
      chat.scrollTop = chat.scrollHeight;
    } catch (e) {}
  };
  es.onerror = () => { es.close(); setTimeout(startStream, 4000); };
}

// ---------- AI 后端 ----------
let agentState = { logged_in: false, ready: false };

async function refreshAgentStatus() {
  try {
    const r = await fetch('/api/agent/status');
    const d = await r.json();
    const nameEl = $('agent-name');
    const stateEl = $('agent-state');
    const btn = $('agent-login');
    const logoutBtn = $('agent-logout');
    const cur = (d.agents || []).find(a => a.id === d.agent);
    const label = cur ? cur.name : 'WorkBuddy';

    // 主状态只显示「已登录 / 未登录」，细节放副行；按钮 hover 才滑出
    let logged = false, detail = label, color = 'var(--muted-2)', busy = false;
    if (!d.backend_ready) {
      detail = `${label} · 后端未启动`; color = 'var(--warn)'; busy = true;
    } else if (d.logged_in) {
      logged = true; detail = `${label} · 运行中`; color = 'var(--brand)';
    } else if (d.login_state === 'pending') {
      detail = `${label} · 等待授权中…`; color = 'var(--warn)'; busy = true;
    } else {
      detail = `${label} · 需要授权`; color = 'var(--danger)';
    }

    nameEl.textContent = logged ? '已登录' : '未登录';
    nameEl.classList.toggle('ok', logged);
    stateEl.textContent = detail;
    stateEl.style.color = color;

    btn.textContent = '登录';
    btn.disabled = busy;
    btn.hidden = logged;              // 已登录只留「退出登录」，不再显示登录按钮
    logoutBtn.hidden = !logged;
    agentState = { logged_in: logged, ready: !!d.backend_ready };
    return d;
  } catch (e) { return null; }
}

// ---------- 二次确认弹窗（退出登录防误触）----------
function askConfirm(title, desc, okText) {
  return new Promise(resolve => {
    const ov = $('confirm-overlay');
    const okBtn = $('confirm-ok');
    const cancelBtn = $('confirm-cancel');
    $('confirm-title').textContent = title;
    $('confirm-desc').textContent = desc;
    okBtn.textContent = okText || '确认退出';
    ov.hidden = false;

    function onKey(e) { if (e.key === 'Escape') done(false); }
    function done(v) {
      ov.hidden = true;
      okBtn.onclick = null;
      cancelBtn.onclick = null;
      ov.onclick = null;
      document.removeEventListener('keydown', onKey);
      resolve(v);
    }

    okBtn.onclick = () => done(true);
    cancelBtn.onclick = () => done(false);
    ov.onclick = e => { if (e.target === ov) done(false); };
    document.addEventListener('keydown', onKey);
  });
}

$('agent-logout').addEventListener('click', async () => {
  const yes = await askConfirm(
    '退出 AI 后端登录？',
    '退出后需要重新授权才能继续使用 AI 对话功能。确定要退出吗？'
  );
  if (!yes) return;
  const tip = $('agent-tip');
  tip.textContent = '正在退出登录 …';
  try {
    const d = await (await fetch('/api/agent/logout', { method: 'POST' })).json();
    tip.textContent = d.ok ? '已退出登录' : (d.msg || '退出失败');
    await refreshAgentStatus();
    setTimeout(() => { tip.textContent = ''; }, 4000);
  } catch (e) { tip.textContent = `退出失败：${e.message}`; }
});

$('agent-login').addEventListener('click', async () => {
  const tip = $('agent-tip');
  tip.textContent = '正在发起登录 …';
  try {
    const r = await fetch('/api/agent/login', { method: 'POST' });
    const d = await r.json();
    if (!d.ok) { tip.textContent = d.msg || '发起登录失败'; await refreshAgentStatus(); return; }
    if (d.already) { tip.textContent = d.msg; await refreshAgentStatus(); return; }

    const link = `<br><a href="${d.auth_url}" target="_blank" style="color:var(--brand)">手动打开授权页</a>`;
    tip.innerHTML = (d.msg || '请完成授权。') + link;

    let n = 0;
    const timer = setInterval(async () => {
      n++;
      const st = await refreshAgentStatus();
      if (st && st.logged_in) {
        clearInterval(timer);
        tip.textContent = '登录成功';
        setTimeout(() => { tip.textContent = ''; }, 4000);
        return;
      }
      // 弹窗被手动关闭 / 超时：停止轮询并如实提示
      try {
        const ls = await (await fetch('/api/agent/login/status')).json();
        if (ls.status === 'cancelled') {
          clearInterval(timer); tip.textContent = '已取消登录'; return;
        }
        if (ls.status === 'timeout' || ls.status === 'fail') {
          clearInterval(timer); tip.textContent = ls.message || '登录未完成，请重试'; return;
        }
      } catch (e) { /* 状态接口抖动不影响主流程 */ }
      if (n > 100) clearInterval(timer);   // 2s × 100 兜底
    }, 2000);
  } catch (e) { tip.textContent = `登录失败：${e.message}`; }
});

// ---------- 小米账号 ----------
let miState = { logged_in: false, account: '' };

async function refreshMiStatus() {
  try {
    const r = await fetch('/api/mijia/status');
    const d = await r.json();
    const nameEl = $('mi-account');
    const atEl = $('mi-account-at');
    const st = d.state || {};
    const loginBtn = $('mi-login');
    const logoutBtn = $('mi-logout');
    const who = d.account ? maskPhone(d.account) : (d.user_id ? `小米ID ${d.user_id}` : '');

    // 凭据失效按「未登录」处理，才能显示「登录」按钮重新授权
    const logged = !!(d.logged_in && d.token_valid !== false);
    let detail = '', color = 'var(--muted-2)';
    if (d.logged_in && d.token_valid === false) {
      detail = '登录已失效，请重新授权'; color = 'var(--danger)';
    } else if (d.logged_in && d.probe_state === 'net') {
      detail = '凭据校验失败（' + (d.probe_error || '网络异常') + '）'; color = 'var(--warn)';
    } else if (logged) {
      detail = [who, d.login_at ? `上次登录 ${d.login_at}` : ''].filter(Boolean).join(' · ');
    }

    nameEl.textContent = logged ? '已登录' : '未登录';
    nameEl.classList.toggle('ok', logged);
    atEl.textContent = detail;
    atEl.style.color = color;

    loginBtn.textContent = '登录';      // 按钮文案统一为「登录」
    loginBtn.hidden = logged;
    logoutBtn.hidden = !logged;
    miState = { logged_in: logged, account: d.account || '' };

    if (st.status === 'pending') $('mi-login-tip').textContent = st.message || '等待登录 …（在弹出的窗口中完成）';
    else if (st.status === 'ok') { $('mi-login-tip').textContent = '登录成功，凭据已保存'; setTimeout(() => { $('mi-login-tip').textContent = ''; }, 4000); }
    else if (st.status === 'fail' || st.status === 'timeout') $('mi-login-tip').textContent = st.message || '登录失败';

    return d;
  } catch (e) { return null; }
}

function maskPhone(s) {
  return s && s.length >= 7 ? s.slice(0, 3) + '****' + s.slice(-4) : s;
}

$('mi-logout').addEventListener('click', async () => {
  const yes = await askConfirm(
    '退出小米账号登录？',
    '将清除本机保存的小米凭据与备份，设备控制和语音播报会停止，之后需要重新扫码登录。确定要退出吗？'
  );
  if (!yes) return;
  const tip = $('mi-login-tip');
  tip.textContent = '正在退出登录 …';
  try {
    const d = await (await fetch('/api/mijia/logout', { method: 'POST' })).json();
    tip.textContent = d.ok ? '已退出登录' : (d.msg || '退出失败');
    await refreshMiStatus();
    if (d.ok) loadDevices(true);          // 清掉上一个账号残留的设备卡片
    setTimeout(() => { tip.textContent = ''; }, 4000);
  } catch (e) { tip.textContent = `退出失败：${e.message}`; }
});

$('mi-login').addEventListener('click', async () => {
  const tip = $('mi-login-tip');
  tip.textContent = '正在打开登录窗口 …';
  try {
    const r = await fetch('/api/mijia/login', { method: 'POST' });
    const d = await r.json();
    tip.textContent = d.msg || '';
    if (d.ok) {
      const timer = setInterval(async () => {
        const st = await refreshMiStatus();
        const s = (st?.state || {}).status;
        if (['ok', 'fail', 'timeout', 'cancelled'].includes(s)) {
          clearInterval(timer);
          if (s === 'ok') $('mi-login-tip').textContent = '登录成功！点「启动」重新连接服务';
        }
      }, 2500);
    }
  } catch (e) { tip.textContent = `打开登录窗口失败：${e.message}`; }
});

// ---------- 试听 ----------
let previewAudio = null;

$('preview').addEventListener('click', async () => {
  const voice = $('voice').value;
  const tip = $('preview-tip');
  const btn = $('preview');
  if (voice === 'mi') { tip.textContent = '小爱原声由音箱合成，无法在此试听'; return; }

  btn.disabled = true;
  tip.textContent = '正在生成试听 …';
  try {
    if (previewAudio) { previewAudio.pause(); previewAudio = null; }
    const rate = `+${$('rate').value}%`;
    const r = await fetch(`/api/tts/preview?voice=${encodeURIComponent(voice)}&rate=${encodeURIComponent(rate)}`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
    const blob = await r.blob();
    previewAudio = new Audio(URL.createObjectURL(blob));
    await previewAudio.play();
    tip.textContent = '';
    previewAudio.onended = () => { tip.textContent = ''; };
  } catch (e) {
    tip.textContent = `试听失败：${e.message}`;
  } finally {
    btn.disabled = false;
  }
});

$('rate').addEventListener('input', () => { $('rate-val').textContent = `+${$('rate').value}%`; });

// ---------- 开关电源 ----------
$('power').addEventListener('click', async () => {
  if (busy) return;
  busy = true;
  $('power').classList.add('busy');
  $('power-text').textContent = running ? '停止中…' : '启动中…';
  try {
    await fetch('/api/power', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ on: !running }),
    });
  } catch (e) {}
  busy = false;
  $('power').classList.remove('busy');
  setTimeout(refreshStatus, 600);
});

// ---------- 保存 ----------
$('save').addEventListener('click', async () => {
  const body = {
    keyword: $('keyword').value.trim(),
    tts: $('voice').value,
    rate: `+${$('rate').value}%`,
    prompt: $('prompt').value,
    transition: $('transition').value,
    mute_xiaoai: $('mute').checked,
    stream: $('stream').checked,
    box_ip: $('box-ip').value.trim(),
    no_keyword: $('no-keyword').checked,
  };
  const r = await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  $('save-tip').textContent = d.ok
    ? (d.restarted
        ? '已保存 · 监听服务已重启生效'
        : (d.pending ? '已保存 · 启动服务后生效' : '已保存 · 已生效'))
    : (d.msg || '保存失败');
  $('kw-hint').textContent = (body.keyword || '天声小爱').split(',')[0].trim();
  setTimeout(() => { $('save-tip').textContent = ''; }, 3000);
});

$('refresh').addEventListener('click', loadDialogue);

// ---------- 日志抽屉 ----------
let logSvc = 'xiaogpt';
let logOpen = false;

$('log-toggle').addEventListener('click', (e) => {
  if (e.target.classList.contains('log-tab')) return;
  logOpen = !logOpen;
  $('logbar').classList.toggle('open', logOpen);
  $('log').hidden = !logOpen;
  if (logOpen) loadLog();
});

document.querySelectorAll('.log-tab').forEach(btn => {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    document.querySelectorAll('.log-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    logSvc = btn.dataset.svc;
    if (!logOpen) { logOpen = true; $('logbar').classList.add('open'); $('log').hidden = false; }
    loadLog();
  });
});

async function loadLog() {
  if (!logOpen) return;
  try {
    const r = await fetch(`/api/log?name=${logSvc}`);
    const d = await r.json();
    const el = $('log');
    el.textContent = d.text || '暂无日志';
    el.scrollTop = el.scrollHeight;
  } catch (e) {}
}

// ---------- 盒子连接帮助弹窗 ----------
(function initBoxHelp() {
  const btn = $('box-help');
  const pop = $('box-help-pop');
  const mask = $('help-mask');
  const close = $('box-help-close');
  if (!btn || !pop) return;

  const show = (on) => {
    pop.hidden = !on;
    if (mask) mask.hidden = !on;
  };

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    show(pop.hidden);
  });
  if (close) close.addEventListener('click', () => show(false));
  if (mask) mask.addEventListener('click', () => show(false));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !pop.hidden) show(false);
  });
})();

// ---------- 免唤醒：唤醒词置灰 ----------
const KW_TIP_DEFAULT = '逗号分隔可写多个。建议把常见误识别也加进来（例如“天声小爱”常被听成的同音词）';

function syncKeywordState() {
  const off = $('no-keyword').checked;
  const input = $('keyword');
  input.disabled = off;
  input.classList.toggle('is-disabled', off);
  $('kw-hint') && ($('kw-hint').style.opacity = off ? '.5' : '1');
  const tip = $('kw-tip');
  if (tip) {
    tip.textContent = off
      ? '已开启免唤醒 · 唤醒词暂不生效'
      : KW_TIP_DEFAULT;
    tip.classList.toggle('warn-tip', off);
  }
}

// ---------- 设备看板 ----------
const DEV_ICONS = {
  light: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 1 3.6 10.8c-.5.4-.6.9-.6 1.2H9c0-.3-.1-.8-.6-1.2A6 6 0 0 1 12 3z"/></svg>',
  fan: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="2.2"/><path d="M12 9.8c0-3 1-5.8 3.4-5.8 2 0 2.6 2.6 1.4 4.2M14.2 12c3 0 5.8 1 5.8 3.4 0 2-2.6 2.6-4.2 1.4M12 14.2c0 3-1 5.8-3.4 5.8-2 0-2.6-2.6-1.4-4.2M9.8 12c-3 0-5.8-1-5.8-3.4 0-2 2.6-2.6 4.2-1.4"/></svg>',
  airconditioner: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="5" width="18" height="8" rx="2.5"/><path d="M6.5 16c.8-1.2.8-2.4 0-3.6M12 16c.8-1.2.8-2.4 0-3.6M17.5 16c.8-1.2.8-2.4 0-3.6"/></svg>',
  purifier: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="4" y="3.5" width="16" height="17" rx="3"/><path d="M8 8.5h8M8 12h8M8 15.5h5"/></svg>',
  waterheater: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="4" y="4" width="16" height="13" rx="3"/><path d="M12 20.5v-3M8.5 9c0-2 1.6-3.5 3.5-3.5S15.5 7 15.5 9"/><path d="M12 16.8c1.4-1.6 2.4-2.8 2.4-4a2.4 2.4 0 0 0-4.8 0c0 1.2 1 2.4 2.4 4z"/></svg>',
  waterpurifier: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 3.5s5.5 6.2 5.5 10a5.5 5.5 0 0 1-11 0C6.5 9.7 12 3.5 12 3.5z"/><path d="M9.5 13.5a2.5 2.5 0 0 0 2.5 2.5"/></svg>',
  router: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 16.5h.01M10.5 16.5h.01M7 3.5c2.8-1 7.2-1 10 0M9 7c1.7-.6 4.3-.6 6 0"/></svg>',
  speaker: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="6" y="2.5" width="12" height="19" rx="5"/><path d="M9.5 7.5h5M9.5 11h5"/></svg>',
  other: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="4" y="4" width="16" height="16" rx="4"/><circle cx="12" cy="12" r="2.5"/></svg>',
};

let devList = [];
let devTimer = null;
const devOpen = new Set();     // 记录哪些卡片是展开的

function devIcon(type) { return DEV_ICONS[type] || DEV_ICONS.other; }

function timeStr(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

async function loadDevices(force) {
  try {
    const r = await fetch('/api/devices' + (force ? '?force=1' : ''));
    const d = await r.json();
    devList = d.devices || [];
    renderDevices(d);
  } catch (e) {
    $('device-grid').innerHTML = '<div class="dev-loading">读取设备失败，请检查网络或小米登录状态</div>';
  }
}

function ctrlHtml(dev, c) {
  const v = c.value;
  if (v === null || v === undefined) return '';
  const dis = c.rw ? '' : ' disabled';

  if (c.kind === 'bool') {
    return `<div class="dev-toggle-line ${v ? 'on' : ''}" data-act="bool" data-siid="${c.siid}" data-piid="${c.piid}" data-value="${v ? 0 : 1}">
      <i class="dot-ind"></i>${c.label}</div>`;
  }
  if (c.kind === 'range') {
    const unit = c.unit || '';
    return `<div class="dev-row">
      <span class="dev-label">${c.label}</span>
      <input class="dev-range" type="range" min="${c.min}" max="${c.max}" step="${c.step || 1}"
             value="${v}" data-siid="${c.siid}" data-piid="${c.piid}" data-unit="${unit}"${dis}>
      <span class="dev-num">${v}${unit}</span>
    </div>`;
  }
  if (c.kind === 'enum') {
    const opts = (c.options || []).slice(0, 8);
    if (!opts.length) return '';
    return `<div class="dev-row">
      <span class="dev-label">${c.label}</span>
      <div class="dev-seg">${opts.map(o =>
        `<button data-act="enum" data-siid="${c.siid}" data-piid="${c.piid}" data-value="${o.v}"
          class="${o.v === v ? 'active' : ''}"${dis}>${o.t}</button>`).join('')}</div>
    </div>`;
  }
  if (c.kind === 'number') {
    return `<div class="dev-row"><span class="dev-label">${c.label}</span>
      <span class="dev-num" style="width:auto;color:var(--muted)">${v}</span></div>`;
  }
  return '';
}

function cardHtml(dev) {
  const open = devOpen.has(dev.did);
  const p = dev.primary;
  const powerBtn = p
    ? `<button class="dev-power ${p.value ? 'on' : ''}" data-act="power"
         data-siid="${p.siid}" data-piid="${p.piid}" data-value="${p.value ? 0 : 1}"
         ${p.rw ? '' : 'disabled'} title="${p.value ? '点击关闭' : '点击开启'}"></button>`
    : '';
  const rows = (dev.controls || [])
    .filter(c => !(p && c.siid === p.siid && c.piid === p.piid))
    .map(c => ctrlHtml(dev, c)).filter(Boolean);
  const body = rows.length
    ? `<div class="dev-body" ${open ? '' : 'hidden'}>${rows.join('')}</div>`
    : '<div class="dev-body" hidden><div class="dev-empty-note">该设备暂无可调项</div></div>';

  return `<div class="dev-card ${dev.online ? '' : 'offline'}" data-did="${dev.did}" data-open="${open ? 1 : 0}">
    <div class="dev-head" data-act="toggle">
      <span class="dev-icon">${devIcon(dev.type)}</span>
      <div class="dev-title">
        <b title="${dev.name}">${dev.name}</b>
        <em>${dev.online ? (dev.summary || dev.type_cn || dev.type) : '离线'}</em>
      </div>
      ${powerBtn}
    </div>
    ${body}
  </div>`;
}

function renderDevices(meta) {
  const grid = $('device-grid');
  if (!devList.length) {
    grid.innerHTML = '<div class="dev-loading">没有读取到设备。请确认小米账号已登录，且账号下有米家设备。</div>';
    return;
  }
  $('dev-updated').textContent = '更新于 ' + (timeStr(meta.updated_at) || '—');
  grid.innerHTML = devList.map(cardHtml).join('');

  // 滑块：拖动时实时显示数值，松手提交
  grid.querySelectorAll('.dev-range').forEach(el => {
    el.addEventListener('input', () => {
      const num = el.parentElement.querySelector('.dev-num');
      if (num) num.textContent = el.value + (el.dataset.unit || '');
    });
    el.addEventListener('change', () => {
      const card = el.closest('.dev-card');
      devSet(card.dataset.did, el.dataset.siid, el.dataset.piid, Number(el.value));
    });
  });
}

async function devSet(did, siid, piid, value) {
  try {
    const r = await fetch('/api/devices/set', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ did, siid: Number(siid), piid: Number(piid), value }),
    });
    const d = await r.json();
    if (!d.ok) {
      $('dev-updated').textContent = '操作失败：' + (d.msg || ('错误码 ' + d.code));
    } else {
      $('dev-updated').textContent = '已下发指令，正在同步 …';
    }
    setTimeout(() => loadDevices(true), 700);
  } catch (e) {
    $('dev-updated').textContent = '操作失败：网络异常';
  }
}

// 卡片内的点击代理
$('device-grid').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const card = e.target.closest('.dev-card');
  if (!card) return;
  const did = card.dataset.did;
  const act = btn.dataset.act;

  if (act === 'toggle') {
    // 点标题区展开/收起
    const body = card.querySelector('.dev-body');
    const open = card.dataset.open === '1';
    card.dataset.open = open ? '0' : '1';
    if (open) { body.hidden = true; devOpen.delete(did); }
    else { body.hidden = false; devOpen.add(did); }
    return;
  }
  if (act === 'power' || act === 'bool' || act === 'enum') {
    e.stopPropagation();
    devSet(did, btn.dataset.siid, btn.dataset.piid, Number(btn.dataset.value));
  }
});

// tab 切换
document.querySelectorAll('.pane-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const pane = btn.dataset.pane;
    document.querySelectorAll('.pane-tab').forEach(b => b.classList.toggle('active', b === btn));
    $('pane-chat').hidden = pane !== 'chat';
    $('pane-devices').hidden = pane !== 'devices';
    $('refresh').hidden = pane !== 'chat';
    $('dev-refresh').hidden = pane === 'chat';
    $('chat-hint').hidden = pane !== 'chat';
    if (pane === 'devices') {
      loadDevices(false);
      if (!devTimer) devTimer = setInterval(() => loadDevices(false), 15000);
    } else if (devTimer) {
      clearInterval(devTimer);
      devTimer = null;
    }
  });
});

$('dev-refresh').addEventListener('click', () => loadDevices(true));

$('no-keyword').addEventListener('change', syncKeywordState);

// ---------- 启动 ----------
(async function init() {
  await loadVoices();
  await refreshAgentStatus();
  await refreshMiStatus();
  await refreshStatus();
  await loadDialogue();
  startStream();
  setInterval(refreshStatus, 3000);
  setInterval(refreshAgentStatus, 3000);   // 登录态实时刷新（配合弹窗自动关闭）
  setInterval(refreshMiStatus, 10000);
  setInterval(loadLog, 5000);
})();
