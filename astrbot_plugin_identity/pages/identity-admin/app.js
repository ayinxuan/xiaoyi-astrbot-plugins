// AstrBot Plugin Page - Identity Manager
const bridge = window.AstrBotPluginPage;

function toast(msg, ok) { if (ok===void 0) ok=true;
  var el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast ' + (ok ? 'toast-success' : 'toast-error') + ' show';
  setTimeout(function(){ el.classList.remove('show'); }, 2500);
}

// ==== 数据加载 ====
var _tableData = [], mode = '', editUserId = '';

async function loadData() {
  try {
    var data = await bridge.apiGet('/list');
    _tableData = Array.isArray(data) ? data : [];
    renderTable(_tableData);
    await loadStats();
  } catch(e) { toast('加载失败: ' + e.message, false); }
}

async function loadStats() {
  try {
    var s = await bridge.apiGet('/stats');
    document.getElementById('stats').textContent = '共 ' + (s.total || 0) + ' 条记录';
  } catch(e) {}
}

// ==== 表格渲染 ====
function renderTable(data) {
  var tbody = document.getElementById('tableBody');
  var empty = document.getElementById('empty');
  if (!data.length) { tbody.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  var rows = [];
  for (var i = 0; i < data.length; i++) {
    var r = data[i];
    rows.push(
      '<tr>' +
      '<td>' + esc(r.user_id) + '</td>' +
      '<td>' + esc(r.nickname) + '</td>' +
      '<td><strong>' + esc(r.identity) + '</strong></td>' +
      '<td>' + esc(r.prompt || '—') + '</td>' +
      '<td>' +
        '<button class="btn btn-secondary btn-sm" data-action="edit" data-idx="' + i + '">编辑</button> ' +
        '<button class="btn btn-danger btn-sm" data-action="delete" data-uid="' + esc(r.user_id) + '">删除</button>' +
      '</td>' +
      '</tr>'
    );
  }
  tbody.innerHTML = rows.join('');
}

function esc(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ===== 搜索 =====
function filterTable(query) {
  var q = (query || '').trim().toLowerCase();
  if (!q) { renderTable(_tableData); return; }
  var filtered = _tableData.filter(function(r) {
    return (r.user_id + '').toLowerCase().indexOf(q) > -1 ||
           (r.nickname || '').toLowerCase().indexOf(q) > -1 ||
           (r.identity || '').toLowerCase().indexOf(q) > -1;
  });
  renderTable(filtered);
}

// ===== 主题切换 =====
function toggleTheme() {
  var isWhite = document.body.classList.toggle('theme-white');
  document.getElementById('themeBtn').textContent = isWhite ? '🌙 深色' : '☀ 白色';
}

// ===== 事件委托（按钮点击）=====
document.getElementById('tableBody').addEventListener('click', function(e) {
  var btn = e.target;
  while (btn && btn !== this) {
    if (btn.getAttribute && btn.getAttribute('data-action')) break;
    btn = btn.parentElement;
  }
  if (!btn || !btn.getAttribute) return;
  var action = btn.getAttribute('data-action');
  if (action === 'delete') {
    var uid = btn.getAttribute('data-uid');
    if (uid) deleteRow(uid);
  } else if (action === 'edit') {
    var idx = parseInt(btn.getAttribute('data-idx'), 10);
    var r = _tableData[idx];
    if (r) editRow(r.user_id, r.nickname, r.identity, r.prompt || '');
  }
});

// ===== 弹窗 =====
function openModal(m, uid, nick, ident, prompt) {
  mode = m;
  document.getElementById('modalOverlay').classList.add('active');
  document.getElementById('modalTitle').textContent = (m === 'add' ? '添加身份' : '编辑身份');
  document.getElementById('fldUserId').value = uid || '';
  document.getElementById('fldNick').value = nick || '';
  document.getElementById('fldIdentity').value = ident || '';
  document.getElementById('fldPrompt').value = prompt || '';
  document.getElementById('fldUserId').disabled = (m === 'edit');
  if (m === 'add') editUserId = '';
  else editUserId = uid;
}

function closeModal() {
  document.getElementById('modalOverlay').classList.remove('active');
}

function editRow(uid, nick, ident, prompt) {
  openModal('edit', uid, nick, ident, prompt);
}

// ===== 提交 =====
async function submitForm() {
  var uid = document.getElementById('fldUserId').value.trim();
  var nick = document.getElementById('fldNick').value.trim();
  var ident = document.getElementById('fldIdentity').value.trim();
  var prompt = document.getElementById('fldPrompt').value.trim();
  if (!uid || !ident) { toast('QQ号和身份标签不能为空', false); return; }
  try {
    if (mode === 'add') {
      await bridge.apiPost('/add', { user_id: uid, nickname: nick, identity: ident, prompt: prompt });
    } else {
      await bridge.apiPost('/update', { user_id: editUserId, nickname: nick, identity: ident, prompt: prompt });
    }
    closeModal();
    toast(mode === 'add' ? '添加成功' : '更新成功');
    loadData();
  } catch(e) { toast('操作失败: ' + e.message, false); }
}

// ===== 删除 =====
async function deleteRow(uid) {
  try {
    await bridge.apiPost('/delete', { user_id: uid });
    toast('已删除');
    loadData();
  } catch(e) { toast('删除失败: ' + e.message, false); }
}

// ===== 启动 =====
(async function() {
  try {
    await bridge.ready();
    await loadData();
  } catch(e) {
    toast('Bridge 连接失败: ' + e.message, false);
  }
})();