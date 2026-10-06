// api.js —— 网络层：fetch 封装（超时/业务错误解析）与后端探测（依赖顺序：第 2 个，需先加载 config.js）
// 红线：不带 credentials；不手动设 Content-Type（multipart 边界交给浏览器）；只读响应体 error
(function () {
  var R = App.api = {};

  /* ==================== 统一请求 ====================
   * opts.timeoutMs 超时（默认 15s）；opts.signal 外部 AbortSignal（轮询作废用）
   * 业务失败：HTTP 200 + {"error":"..."} → throw Error(error 文案)
   * 网络失败 / 超时 / 被中止 → throw Error('连接后端失败')
   */
  async function request(method, path, body, opts) {
    opts = opts || {};
    var timeoutMs = opts.timeoutMs || App.TIMEOUT.def;
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, timeoutMs);
    var external = opts.signal || null;
    var onAbort = function () { ctrl.abort(); };
    if (external) {
      if (external.aborted) ctrl.abort();
      else external.addEventListener('abort', onAbort);
    }

    var text, res;
    try {
      res = await fetch(App.API + path, {
        method: method,
        body: body || undefined, // FormData 或 undefined；不设 headers
        signal: ctrl.signal      // 不带 credentials
      });
      text = await res.text();
    } catch (e) {
      throw new Error('连接后端失败'); // 网络失败 / 超时 / 被作废
    } finally {
      clearTimeout(timer);
      if (external) external.removeEventListener('abort', onAbort);
    }

    var data = null;
    try { data = JSON.parse(text); } catch (e) { data = null; }
    if (data && data.error) throw new Error(String(data.error)); // 只读响应体 error
    if (!res.ok) throw new Error('请求失败（HTTP ' + res.status + '）');
    if (data === null) throw new Error('后端返回了无法解析的数据');
    return data;
  }

  /* ==================== 三个便捷入口 ==================== */
  R.apiGet = function (path, opts) { return request('GET', path, undefined, opts); };
  R.apiPostForm = function (path, fd, opts) { return request('POST', path, fd, opts); };
  R.apiPost = function (path, opts) { return request('POST', path, undefined, opts); };

  /* ==================== 后端探测：三态徽章 ====================
   * 在线（绿）/ 模型未加载（黄 + 提示）/ 离线（红 + 统一提示 + 禁用分析按钮）
   */
  R.probeBackend = async function () {
    try {
      var data = await request('GET', '/', undefined, { timeoutMs: App.TIMEOUT.probe });
      if (data && data.model_loaded) {
        App.state.backend = 'online';
        App.render.setStatus('ok', '后端在线');
      } else {
        App.state.backend = 'model-missing';
        App.render.setStatus('warn', '模型未加载');
        App.render.showAlert('模型未加载，请查看日志', 'warn');
      }
    } catch (e) {
      App.state.backend = 'offline';
      App.render.setStatus('danger', '后端离线');
      App.render.showAlert('后端未启动，请先运行 python backend/main.py', 'error');
    }
    App.refreshAnalyzeBtn();
  };
})();
