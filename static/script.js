// 智能问数 - 前端交互 (v2)

const input = document.getElementById('questionInput');
const sendBtn = document.getElementById('sendBtn');
const chatArea = document.getElementById('chatArea');
const chatMessages = document.getElementById('chatMessages');
const welcomeScreen = document.getElementById('welcomeScreen');
const qaCount = document.getElementById('qaCount');
const statusDot = document.querySelector('.status-dot');
const statusText = document.querySelector('.status-text');

let isLoading = false;
let questionCount = 0;
let isFirstQuestion = true;

let tabDataStore = {};

input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendQuestion();
    }
});

input.addEventListener('input', function() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
});

function ask(q) {
    input.value = q;
    sendQuestion();
}

async function sendQuestion() {
    if (isLoading) return;
    var question = input.value.trim();
    if (!question) return;

    if (isFirstQuestion) {
        welcomeScreen.style.display = 'none';
        chatArea.style.display = 'flex';
        isFirstQuestion = false;
    }

    addMessage('user', question);
    input.value = '';
    input.style.height = 'auto';

    var loadId = addMessage('bot', '<div class="typing"><span></span><span></span><span></span></div>', true);
    setLoading(true);
    setStatus(false);

    try {
        var res = await fetch('/api/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: question }),
        });
        var data = await res.json();

        removeMessage(loadId);
        setStatus(true);

        var html = buildAnswerHtml(data);
        addMessage('bot', html);
        questionCount++;
        qaCount.textContent = questionCount;

    } catch (err) {
        removeMessage(loadId);
        setStatus(false);
        addMessage('bot', '请求失败，请检查后端是否运行。');
    }

    setLoading(false);
    input.focus();
}

// 简单Markdown渲染器
function renderMarkdown(text) {
    if (!text) return '';
    var html = text;
    // 标题
    html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');
    // 加粗
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // 表格
    var lines = html.split('\n');
    var result = [];
    var tableRows = [];
    var inTable = false;
    for (var i = 0; i < lines.length; i++) {
        var line = lines[i].trim();
        if (line.indexOf('|') !== -1 && line.indexOf('---') === -1) {
            if (!inTable) { inTable = true; tableRows = []; }
            var cells = line.split('|').map(function(c) { return c.trim(); }).filter(function(c) { return c; });
            tableRows.push(cells);
        } else {
            if (inTable) {
                result.push(buildHtmlTable(tableRows));
                inTable = false;
                tableRows = [];
            }
            result.push(line);
        }
    }
    if (inTable) result.push(buildHtmlTable(tableRows));
    html = result.join('\n');
    // 列表
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
    // 换行
    html = html.replace(/\n/g, '<br>');
    return html;
}

function buildHtmlTable(rows) {
    if (rows.length < 2) return rows.map(function(r) { return r.join(' '); }).join('<br>');
    var html = '<table class="md-table"><thead><tr>';
    rows[0].forEach(function(c) { html += '<th>' + c + '</th>'; });
    html += '</tr></thead><tbody>';
    for (var i = 1; i < rows.length; i++) {
        html += '<tr>';
        rows[i].forEach(function(c) { html += '<td>' + c + '</td>'; });
        html += '</tr>';
    }
    html += '</tbody></table>';
    return html;
}

function buildAnswerHtml(data) {
    var html = '';
    var intent = data.intent || data.task_type || 'basic_query';
    var tags = { basic_query:'财务报表', query:'财务报表', stat_query:'统计分析', comparison:'企业对比', time_trend:'趋势分析', fuzzy_intent:'模糊查询', open_question:'研报解读', analysis_query:'归因分析', analysis:'归因分析', risk:'风险预警', report:'财务报告', trend:'趋势分析', compare:'企业对比' };
    html += '<span class="msg-badge">' + (tags[intent] || '查询') + '</span><br>';

    var rc = data.result_count || 0;
    var rp = data.result_period || '';
    if (rc > 0 || rp) {
        html += '<div class="result-meta">';
        html += '<span class="meta-count">' + rc + '条结果</span>';
        if (rp) html += '<span class="meta-period">' + rp + '</span>';
        if (data.company) html += '<span style="color:var(--text-dim);font-size:10px">' + data.company + '</span>';
        html += '</div>';
    }

    // 统一用Markdown渲染答案（处理**加粗**等标记）
    var answer = data.answer || data.display_html || (data.has_rag ? '' : '无结果');

    if (data.chart) {
        html += '<div class="chart-img"><img src="data:image/png;base64,' + data.chart + '" onclick="this.classList.toggle(\'expanded\')"></div>';
    }

    // 答案文字（图表之后展示）
    if (answer && answer !== '无结果') {
        html += '<div class="answer-text">' + renderMarkdown(answer) + '</div>';
    }

    var conf = data.confidence || 0;
    var route = data.route || '';
    html += '<div class="confidence-tag">置信度 ' + (conf * 100).toFixed(0) + '% (' + route + ')</div>';

    var hasRag = data.has_rag && data.rag_html_raw;
    var hasSql = data.sql && data.sql.length > 0;

    if (hasRag || hasSql) {
        var msgId = 'm' + Date.now() + '_' + Math.random().toString(36).slice(2,6);
        html += '<div class="foot-tabs" id="' + msgId + '_tabs">';
        html += '<div class="foot-tab-bar">';
        if (hasRag) html += '<button class="foot-tab-btn active" onclick="switchTab(\'' + msgId + '\',\'rag\')">研报参考</button>';
        if (hasSql) html += '<button class="foot-tab-btn' + (!hasRag ? ' active' : '') + '" onclick="switchTab(\'' + msgId + '\',\'sql\')">SQL溯源</button>';
        html += '</div>';
        html += '<div class="foot-tab-content" id="' + msgId + '_rag"' + (!hasRag ? ' style="display:none"' : '') + '>';
        html += (hasRag ? data.rag_html_raw : '<div class="empty-tab">无研报参考</div>');
        html += '</div>';
        html += '<div class="foot-tab-content" id="' + msgId + '_sql" style="display:' + (hasRag ? 'none' : '') + '">';
        html += (hasSql ? '<pre class="sql-trace">' + escHtml(data.sql) + '</pre>' : '<div class="empty-tab">无SQL记录</div>');
        html += '</div>';
        html += '</div>';
        tabDataStore[msgId] = { current: hasRag ? 'rag' : 'sql' };
    }

    return html;
}

function switchTab(msgId, tab) {
    var rag = document.getElementById(msgId + '_rag');
    var sql = document.getElementById(msgId + '_sql');
    var btns = document.querySelectorAll('#' + msgId + '_tabs .foot-tab-btn');
    if (rag) rag.style.display = tab === 'rag' ? '' : 'none';
    if (sql) sql.style.display = tab === 'sql' ? '' : 'none';
    btns.forEach(function(b) { b.classList.remove('active'); });
    var idx = tab === 'rag' ? 0 : (rag ? 1 : 0);
    if (btns[idx]) btns[idx].classList.add('active');
}

function addMessage(role, html, isTemp) {
    var div = document.createElement('div');
    div.className = 'msg ' + role;
    if (isTemp) div.dataset.temp = '1';
    var bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.innerHTML = html;
    div.appendChild(bubble);
    chatMessages.appendChild(div);
    scrollDown();
    return div;
}

function removeMessage(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
}

function scrollDown() {
    var ca = document.getElementById('contentArea');
    ca.scrollTop = ca.scrollHeight;
}

function setLoading(loading) {
    isLoading = loading;
    sendBtn.disabled = loading;
    input.disabled = loading;
}

function setStatus(connected) {
    if (connected) {
        statusDot.classList.add('connected');
        statusText.textContent = '已连接';
    } else {
        statusDot.classList.remove('connected');
        statusText.textContent = '未连接';
    }
}

function escHtml(text) {
    var d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

document.querySelector('.nav-right').addEventListener('click', function(e) {
    if (e.target.classList.contains('nav-btn') && e.target.textContent === '清空') {
        chatMessages.innerHTML = '';
        welcomeScreen.style.display = 'flex';
        chatArea.style.display = 'none';
        isFirstQuestion = true;
        questionCount = 0;
        qaCount.textContent = '0';
        tabDataStore = {};
    }
});

input.focus();

