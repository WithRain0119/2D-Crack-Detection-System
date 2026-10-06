// app.js —— 业务流程与初始化：文件选择、上传分析、轮询、设置面板、事件绑定
// （依赖顺序：第 4 个最后加载，需先加载 config/api/render；唯一 DOMContentLoaded 在此）
(function () {
  var state = App.state;
  var seenDone = new Set(); // 本批次已出现过的 done（用于"新完成一张自动选中"）

  /* ==================== 分析按钮可用性 ==================== */
  function refreshAnalyzeBtn() {
    var ok = state.backend !== 'offline' && state.files.length > 0;
    App.el('btnAnalyze').disabled = !ok;
  }
  App.refreshAnalyzeBtn = refreshAnalyzeBtn;

  /* ==================== 文件选择与本地过滤 ==================== */
  // mode：folder（选文件夹/拖拽）| single（选图片）
  App.acceptFiles = function (fileList, mode) {
    var arr = Array.prototype.slice.call(fileList || []);
    var valid = [], skipped = [];
    arr.forEach(function (f) {
      var name = f.name || '';
      var dot = name.lastIndexOf('.');
      var ext = dot > -1 ? name.slice(dot).toLowerCase() : '';
      // 扩展名 OR MIME 双条件
      var ok = App.IMG_EXT.has(ext) || (f.type && f.type.indexOf('image/') === 0);
      if (ok) valid.push(f); else skipped.push(name);
    });

    // 接受新文件即停止旧轮询并完整重置（缺陷#5 / "分析中重选"规避）
    stopPolling();
    resetAll();

    state.files = valid;
    state.skipped = skipped;
    state.mode = (mode === 'folder') ? 'folder' : 'single';

    // 文件摘要
    App.setText(App.el('fileSummary'),
      valid.length ? '已选择 ' + valid.length + ' 张图片' : '未选择文件');
    // 跳过提示（不阻塞）
    var hint = App.el('skipHint');
    if (skipped.length) {
      var names = skipped.slice(0, 10).join('、');
      if (skipped.length > 10) names += ' 等';
      App.setText(hint, '跳过 ' + skipped.length + ' 个非图片：' + names);
      hint.hidden = false;
    } else {
      App.setText(hint, '');
      hint.hidden = true;
    }
    refreshAnalyzeBtn();
  };

  /* ==================== 唯一重置入口 ==================== */
  // 清 thumbMap / 缩略图 DOM / 大图 / 表格 / 进度 / 汇总 / batchId / alert
  function resetAll() {
    state.batchId = '';
    state.inputDir = '';
    state.outputDir = '';
    state.selectedIdx = null;
    state.thumbMap.clear();
    seenDone.clear();
    App.setText(App.el('thumbList'), '');
    App.setText(App.el('boxBody'), '');
    App.el('origImg').hidden = true;
    App.el('resultImg').hidden = true;
    App.el('fallbackCanvas').hidden = true;
    App.render.renderViewerHint('', 'info');
    App.render.renderViewerTitle('');
    App.el('progressCard').hidden = true;
    App.el('resultCard').hidden = true;
    App.setText(App.el('progressText'), '已处理 0/0');
    App.setText(App.el('currentFile'), '');
    App.el('currentFile').hidden = true;
    App.el('progressBar').style.width = '0%';
    App.render.renderSummary('');
    App.render.hideAlert();
    App.setText(App.el('batchBadge'), '未创建批次');
    App.setText(App.el('pathBadge'), '');
    App.el('pathBadge').hidden = true;
  }
  App.resetAll = resetAll;

  /* ==================== 开始分析：init → 展示目录 → run → 轮询 ==================== */
  App.startAnalysis = async function () {
    if (state.files.length === 0) {
      App.render.showAlert('请先选择要分析的图片', 'warn');
      return; // 不发请求
    }
    stopPolling(); // 再次分析前作废旧轮询
    resetAll();

    var btn = App.el('btnAnalyze');
    btn.disabled = true; // 上传期间防重复点击
    try {
      // 1) 创建批次并上传（多 files + conf + mode）
      var fd = new FormData();
      state.files.forEach(function (f) { fd.append('files', f, f.name); });
      fd.append('conf', String(state.conf));
      fd.append('mode', state.mode);
      var init = await App.api.apiPostForm('/api/api/batch/init', fd,
        { timeoutMs: App.TIMEOUT.init });

      // 2) 展示批次信息（batch_id 原文 + 两个相对目录）
      state.batchId = init.batch_id;
      state.inputDir = init.input_dir;
      state.outputDir = init.output_dir;
      App.setText(App.el('batchBadge'), init.batch_id);
      App.setText(App.el('pathBadge'),
        'input：' + init.input_dir + '　output：' + init.output_dir);
      App.el('pathBadge').hidden = false;
      App.el('progressCard').hidden = false;
      App.el('resultCard').hidden = false;
      App.render.renderProgress(init);

      // 3) 预建全部缩略图条目
      App.render.buildThumbs(init.files);

      // 4) 启动后台识别（立即返回）
      var fdRun = new FormData();
      fdRun.append('batch_id', state.batchId);
      var run = await App.api.apiPostForm('/api/api/batch/run', fdRun,
        { timeoutMs: App.TIMEOUT.def });
      App.render.renderProgress(run);

      // 5) 开始轮询
      startPolling();
    } catch (e) {
      // 后端业务失败文案逐字原样展示（没有收到任何文件 / 超 500 张 / 无有效图片 …）
      App.render.showAlert((e && e.message) || '连接后端失败', 'error');
    } finally {
      refreshAnalyzeBtn();
    }
  };

  /* ==================== 轮询：token 自增 + AbortController（缺陷#6 规避） ==================== */
  function stopPolling() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    if (state.pollAbort) {
      state.pollAbort.abort(); // 中断在途请求
      state.pollAbort = null;
    }
    state.pollToken++; // 令牌作废：在途/排队的 pollOnce 一律弃权
  }

  function schedulePoll(token, delay) {
    state.pollTimer = setTimeout(function () { pollOnce(token); }, delay);
  }

  function startPolling() {
    stopPolling();
    var token = state.pollToken; // stopPolling 已自增，当前值即新批次令牌
    state.pollAbort = new AbortController();
    state.pollDeadline = Date.now() + App.POLL.maxMs;
    state.pollFailCount = 0;
    App.render.setStatus('busy', '处理中');
    schedulePoll(token, 0);
  }

  async function pollOnce(token) {
    if (token !== state.pollToken) return; // 首行比对：旧轮询直接弃权
    if (Date.now() > state.pollDeadline) {
      stopPolling();
      App.render.setStatus('ok', '后端在线');
      App.render.showAlert('处理超时（已超过 30 分钟），已停止轮询', 'error');
      return;
    }
    try {
      var resp = await App.api.apiGet(
        '/api/api/batch/status/' + encodeURIComponent(state.batchId), // 全角冒号必须编码
        { timeoutMs: App.TIMEOUT.def, signal: state.pollAbort.signal });
      if (token !== state.pollToken) return; // 响应返回后再次比对，防止旧批次混入
      state.pollFailCount = 0;

      // 每轮：进度 + 按 stored_name 更新全部缩略图
      App.render.renderProgress(resp);
      (resp.files || []).forEach(function (f) {
        App.render.updateThumb(f.stored_name, f);
      });

      // 新出现的 done 自动选中（取本轮最后一张新完成的）
      var newly = [];
      (resp.files || []).forEach(function (f) {
        if (f.status === 'done' && !seenDone.has(f.stored_name)) {
          seenDone.add(f.stored_name);
          newly.push(f);
        }
      });
      if (newly.length) App.render.selectImage(newly[newly.length - 1].stored_name);

      if (resp.status === 'done') {
        stopPolling();
        App.render.setStatus('ok', '后端在线');
        // 汇总：成功/失败张数 + output 路径
        var okN = 0, failN = 0;
        (resp.files || []).forEach(function (f) {
          if (f.status === 'done') okN++;
          else if (f.status === 'failed') failN++;
        });
        App.render.renderSummary('分析完成：成功 ' + okN + ' 张，失败 ' + failN +
          ' 张；输出目录：' + resp.output_dir);
        App.loadRecords(); // 每批次完成刷新历史记录
      } else if (resp.status === 'failed') {
        stopPolling();
        App.render.setStatus('ok', '后端在线');
        App.render.showAlert(resp.error || '批次处理失败', 'error');
      } else {
        schedulePoll(token, App.POLL.interval);
      }
    } catch (e) {
      if (token !== state.pollToken) return; // 已被作废（stopPolling 触发的中止不算失败）
      state.pollFailCount++;
      if (state.pollFailCount >= App.POLL.maxFails) {
        stopPolling();
        state.backend = 'offline';
        App.render.setStatus('danger', '后端离线');
        App.render.showAlert('连接后端失败', 'error'); // 三级提示之顶部条，不用裸 alert()
        refreshAnalyzeBtn();
        return;
      }
      schedulePoll(token, App.POLL.interval);
    }
  }

  /* ==================== 历史检测记录 ==================== */
  App.loadRecords = async function () {
    try {
      var resp = await App.api.apiGet('/api/api/records', { timeoutMs: App.TIMEOUT.def });
      // 必取 resp.records（对象不是数组 —— 缺陷#1 规避）
      App.render.renderRecords(resp && resp.records);
    } catch (e) {
      // 后端离线时探测徽章已给出更具体的原因，不覆盖它
      if (state.backend !== 'offline') {
        App.render.showAlert((e && e.message) || '连接后端失败', 'error');
      }
    }
  };

  /* ==================== 设置面板（居中模态窗） ==================== */
  function closeSettings() {
    App.el('settingsOverlay').hidden = true;
  }

  function bindSettings() {
    // 右上角 ⚙ → 打开居中模态窗（打开时清掉上次提示）
    App.el('btnSettings').addEventListener('click', function () {
      var ov = App.el('settingsOverlay');
      ov.hidden = false;
      App.el('settingsMsg').hidden = true;
      App.setText(App.el('settingsMsg'), '');
    });
    // 点遮罩空白处关闭（点窗口内部不关闭）
    App.el('settingsOverlay').addEventListener('click', function (e) {
      if (e.target === App.el('settingsOverlay')) closeSettings();
    });
    // 关闭按钮 / Esc
    App.el('btnSettingsClose').addEventListener('click', closeSettings);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !App.el('settingsOverlay').hidden) closeSettings();
    });
    App.el('btnClearLogs').addEventListener('click', async function () {
      if (!confirm('确认清空历史日志文件？当前运行中的日志将保留。')) return;
      try {
        var resp = await App.api.apiPost('/api/api/logs/clear', { timeoutMs: App.TIMEOUT.def });
        App.setText(App.el('settingsMsg'), '已清除 ' + (Number(resp.deleted) || 0) + ' 个日志文件');
        App.el('settingsMsg').hidden = false;
      } catch (e) {
        App.render.showAlert((e && e.message) || '连接后端失败', 'error');
      }
    });

    // 清空历史检测记录：清 SQLite 全表（磁盘图片不受影响），记录区立即回空态
    App.el('btnClearRecords').addEventListener('click', async function () {
      if (!confirm('确认清空全部历史检测记录？此操作不可恢复，磁盘上的图片不受影响。')) return;
      try {
        var resp = await App.api.apiPost('/api/api/records/clear', { timeoutMs: App.TIMEOUT.def });
        App.setText(App.el('settingsMsg'), '已清除 ' + (Number(resp.deleted) || 0) + ' 条检测记录');
        App.el('settingsMsg').hidden = false;
        App.render.renderRecords([]); // 记录区立即回空态（内部同步 state.records）
      } catch (e) {
        App.render.showAlert((e && e.message) || '连接后端失败', 'error');
      }
    });
  }

  /* ==================== 事件一次性绑定（唯一 DOMContentLoaded —— 缺陷#9 规避） ==================== */
  function bindEvents() {
    // 两个按钮 → 触发对应隐藏 input
    App.el('btnFolder').addEventListener('click', function () { App.el('folderInput').click(); });
    App.el('btnImages').addEventListener('click', function () { App.el('imageInput').click(); });

    // 两个 input → 过滤并接受文件
    App.el('folderInput').addEventListener('change', function (e) {
      App.acceptFiles(e.target.files, 'folder');
      e.target.value = ''; // 允许再次选择同一批文件
    });
    App.el('imageInput').addEventListener('change', function (e) {
      App.acceptFiles(e.target.files, 'single');
      e.target.value = '';
    });

    // 拖拽上传
    var dz = App.el('dropZone');
    dz.addEventListener('dragover', function (e) {
      e.preventDefault();
      dz.classList.add('is-drag');
    });
    dz.addEventListener('dragleave', function () { dz.classList.remove('is-drag'); });
    dz.addEventListener('drop', function (e) {
      e.preventDefault();
      dz.classList.remove('is-drag');
      App.acceptFiles(e.dataTransfer && e.dataTransfer.files, 'folder');
    });

    // 置信度滑块
    App.el('confRange').addEventListener('input', function (e) {
      state.conf = parseFloat(e.target.value);
      App.setText(App.el('confValue'), e.target.value);
    });

    // 开始分析
    App.el('btnAnalyze').addEventListener('click', function () { App.startAnalysis(); });

    // 缩略图列表：事件委托（节点永不重建）
    App.el('thumbList').addEventListener('click', function (e) {
      var li = e.target.closest('li');
      if (!li || !li.dataset.stored) return;
      App.render.selectImage(li.dataset.stored);
    });

    // 结果图/原图加载事件：各绑一次，驱动 canvas 兜底
    App.el('resultImg').addEventListener('error', function () { App.render.onResultError(); });
    App.el('origImg').addEventListener('load', function () { App.render.onOrigLoaded(); });

    // 设置面板
    bindSettings();
  }

  /* ==================== 启动 ==================== */
  document.addEventListener('DOMContentLoaded', async function () {
    bindEvents();
    await App.api.probeBackend(); // 探测后端（8s 超时，三态徽章）
    App.loadRecords();            // 页面加载即拉取历史记录
  });
})();
