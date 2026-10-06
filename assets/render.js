// render.js —— 全部渲染逻辑：状态条/进度/缩略图/大图对比/明细表/历史记录/提示/canvas兜底
// （依赖顺序：第 3 个，需先加载 config.js；仅调用 App.el/setText/urlForInput，不发网络请求）
(function () {
  var R = App.render = {};

  // 模块内部：当前大图区展示的数据。仅供结果图加载失败时 canvas 画框取 boxes，
  // 归一化明细表绝不依赖它（每个条目解析自带 image_size —— 缺陷#15 规避）
  var viewerData = null;
  var fallbackPending = false; // 原图未就绪时先挂起，load 后补画

  /* ==================== 顶部状态条与统一提示 ==================== */
  // 徽章类型：ok 在线绿 / warn 黄 / danger 红 / busy 处理中
  R.setStatus = function (type, text) {
    var b = App.el('backendBadge');
    b.className = 'badge badge-' + type;
    App.setText(b, text);
  };

  R.showAlert = function (text, type) {
    var n = App.el('globalAlert');
    n.className = 'alert alert-' + (type || 'error');
    App.setText(n, text);
    n.hidden = false;
  };

  R.hideAlert = function () {
    App.el('globalAlert').hidden = true;
  };

  /* ==================== 进度区 ==================== */
  // 进度只读后端 processed/total，无任何计时模拟（缺陷#11 规避）
  R.renderProgress = function (s) {
    var total = Number(s.total) || 0;
    var processed = Number(s.processed) || 0;
    var pct = total > 0 ? Math.min(100, processed / total * 100) : 0;
    App.setText(App.el('progressText'), '已处理 ' + processed + '/' + total);
    var cur = App.el('currentFile');
    if (s.current) {
      App.setText(cur, '当前：' + s.current);
      cur.hidden = false;
    } else {
      App.setText(cur, '');
      cur.hidden = true;
    }
    App.el('progressBar').style.width = pct + '%';
  };

  R.renderSummary = function (text) {
    var n = App.el('batchSummary');
    if (!text) { n.hidden = true; App.setText(n, ''); return; }
    App.setText(n, text);
    n.hidden = false;
  };

  /* ==================== 缩略图列表 ==================== */
  var BADGE = {
    pending:    ['待处理', 'is-gray'],
    processing: ['处理中', 'is-blue'],
    done:       ['完成',   'is-green'],
    failed:     ['失败',   'is-red']
  };

  // init 成功后一次性预建全部条目（DOM 只建一次，之后只更新徽章/图源）
  R.buildThumbs = function (files) {
    var list = App.el('thumbList');
    App.setText(list, '');
    App.state.thumbMap.clear();
    (files || []).forEach(function (f) {
      var li = document.createElement('li');
      li.dataset.stored = f.stored_name;
      var img = document.createElement('img');
      img.className = 'thumb-img';
      img.alt = '';
      img.src = App.urlForInput(f.stored_name); // 未出结果前显示原图
      var meta = document.createElement('div');
      meta.className = 'thumb-meta';
      var name = document.createElement('span');
      name.className = 'thumb-name';
      App.setText(name, f.name);
      var badge = document.createElement('span');
      badge.className = 'thumb-badge';
      meta.appendChild(name);
      meta.appendChild(badge);
      li.appendChild(img);
      li.appendChild(meta);
      list.appendChild(li);
      App.state.thumbMap.set(f.stored_name, { li: li, img: img, badge: badge, data: f });
      R.updateThumb(f.stored_name, f);
    });
  };

  // 轮询每轮按 stored_name 更新：四态徽章 + 缩略图（done/failed 优先结果图）
  R.updateThumb = function (stored, s) {
    var entry = App.state.thumbMap.get(stored);
    if (!entry || !s) return;
    entry.data = s; // 每条缩略图自带最新数据
    var st = BADGE[s.status] || BADGE.pending;
    App.setText(entry.badge, st[0]);
    entry.badge.className = 'thumb-badge ' + st[1];
    // done/failed 用 result_url；failed 结果图为空时回退原图（空 src 会加载失败）
    var src = (s.status === 'done' || s.status === 'failed') ? (s.result_url || '') : '';
    src = src || App.urlForInput(stored);
    if (entry.img.getAttribute('src') !== src) entry.img.src = src;
  };

  /* ==================== 大图对比区 ==================== */
  function indexOfStored(stored) {
    var children = App.el('thumbList').children;
    for (var i = 0; i < children.length; i++) {
      if (children[i].dataset.stored === stored) return i;
    }
    return -1;
  }

  function clearThumbActive() {
    var children = App.el('thumbList').children;
    for (var i = 0; i < children.length; i++) children[i].classList.remove('is-active');
  }

  // 切换大图对比 + 明细表；data 全部取自 thumbMap 每条自带的数据
  R.selectImage = function (storedOrIdx) {
    var stored = storedOrIdx;
    if (typeof storedOrIdx === 'number') {
      var li0 = App.el('thumbList').children[storedOrIdx];
      stored = li0 ? li0.dataset.stored : null;
    }
    if (!stored) return;
    var entry = App.state.thumbMap.get(stored);
    if (!entry) return;

    clearThumbActive();
    entry.li.classList.add('is-active');
    App.state.selectedIdx = indexOfStored(stored);

    var data = entry.data || {};
    viewerData = data;
    fallbackPending = false;

    // 左：原图（前端按 input_dir 拼 URL）
    var orig = App.el('origImg');
    orig.src = App.urlForInput(stored);
    orig.hidden = false;
    App.el('fallbackCanvas').hidden = true;

    // 右：识别结果 —— 正常情况只用后端 result_url，绝不前端画框（缺陷#12 规避）
    var resImg = App.el('resultImg');
    var hint = null;
    if (data.status === 'done') {
      if (data.result_url) {
        resImg.src = data.result_url;
        resImg.hidden = false;
      } else {
        resImg.hidden = true; // 恢复批次等异常：无结果图
      }
      // 裂缝数量展示：null（恢复批次缺数据）不显示
      if (data.crack_count !== null && data.crack_count !== undefined) {
        hint = { text: '共检出 ' + data.crack_count + ' 处裂缝', type: 'info' };
      }
    } else if (data.status === 'failed') {
      resImg.hidden = true;
      hint = { text: data.error || '识别失败', type: 'error' };
    } else if (data.status === 'processing') {
      resImg.hidden = true;
      hint = { text: '正在识别…', type: 'info' };
    } else {
      resImg.hidden = true;
      hint = { text: '尚未生成结果', type: 'info' }; // pending
    }
    R.renderViewerHint(hint ? hint.text : '', hint ? hint.type : 'info');
    R.renderBoxTable({ boxes: data.boxes, image_size: data.image_size, crack_count: data.crack_count });
  };

  R.renderViewerHint = function (text, type) {
    var n = App.el('viewerHint');
    if (!text) { n.hidden = true; App.setText(n, ''); return; }
    n.className = 'viewer-hint hint-' + (type || 'info');
    App.setText(n, text);
    n.hidden = false;
  };

  /* ==================== 裂缝明细表（严格 11 列） ==================== */
  // 表头 11 列写死在 HTML；每行恰好 11 个 <td>（缺陷#13 规避）
  // 归一化 = 像素 ÷ 该条目自带 image_size 的宽/高（严禁 img.width、严禁跨条目全局 —— 缺陷#15 规避）
  R.renderBoxTable = function (data) {
    var body = App.el('boxBody');
    App.setText(body, '');
    var boxes = (data && Array.isArray(data.boxes)) ? data.boxes : [];

    // 解析该条目自带的 image_size 字符串："1600x1200" -> 宽 1600 / 高 1200
    var w = 0, h = 0;
    var size = (data && data.image_size) ? String(data.image_size).split('x') : [];
    if (size.length === 2) {
      w = parseFloat(size[0]);
      h = parseFloat(size[1]);
    }
    var hasCount = data && data.crack_count !== null && data.crack_count !== undefined;
    var sizeOk = w > 0 && h > 0 && hasCount;

    function addRow(cells, cls) {
      var tr = document.createElement('tr');
      if (cls) tr.className = cls;
      cells.forEach(function (txt) {
        var td = document.createElement('td');
        App.setText(td, txt);
        tr.appendChild(td);
      });
      body.appendChild(tr);
      return tr;
    }
    function norm(v, d) {
      var n = Number(v) / d;
      if (!isFinite(n)) return '—';
      n = Math.min(1, Math.max(0, n)); // 归一化必须落在 0~1
      return n.toFixed(4);
    }

    // 数据不完整（image_size 为空 或 crack_count 为 null）→ 数值列显示 —
    if (!sizeOk) {
      if (boxes.length === 0) {
        var tr = document.createElement('tr');
        var td = document.createElement('td');
        td.setAttribute('colspan', '11');
        App.setText(td, '—');
        td.className = 'empty-cell';
        tr.appendChild(td);
        body.appendChild(tr);
      } else {
        boxes.forEach(function (b, i) {
          addRow([
            String(i + 1),
            (b && b.class) ? String(b.class) : '—',
            (b && typeof b.conf === 'number') ? b.conf.toFixed(4) : '—',
            '—', '—', '—', '—', '—', '—', '—', '—'
          ]);
        });
      }
      return;
    }

    // 数据完整但无框 → 空态行"未检出裂缝"
    if (boxes.length === 0) {
      var trE = document.createElement('tr');
      var tdE = document.createElement('td');
      tdE.setAttribute('colspan', '11');
      App.setText(tdE, '未检出裂缝');
      tdE.className = 'empty-cell';
      trE.appendChild(tdE);
      body.appendChild(trE);
      return;
    }

    // 正常：每行 11 列 —— 序号|类别|置信度|x1|y1|x2|y2|归一化x1|归一化y1|归一化x2|归一化y2
    boxes.forEach(function (b, i) {
      addRow([
        String(i + 1),
        (b && b.class) ? String(b.class) : '—',
        (b && typeof b.conf === 'number') ? b.conf.toFixed(4) : '—',
        String(b.x1), String(b.y1), String(b.x2), String(b.y2),
        norm(b.x1, w), norm(b.y1, h), norm(b.x2, w), norm(b.y2, h)
      ]);
    });
  };

  /* ==================== canvas 兜底（仅 resultImg.onerror 触发） ==================== */
  R.onResultError = function () {
    R.drawFallback();
  };

  // 原图加载完成后补画（结果图先失败、原图后就绪的时序）
  R.onOrigLoaded = function () {
    if (fallbackPending) R.drawFallback();
  };

  R.drawFallback = function () {
    var canvas = App.el('fallbackCanvas');
    var img = App.el('origImg');
    App.el('resultImg').hidden = true;
    R.renderViewerHint('结果图加载失败，已用画框兜底', 'warn');
    if (!img.complete) { fallbackPending = true; canvas.hidden = true; return; }
    if (!img.naturalWidth) { fallbackPending = false; canvas.hidden = true; return; }
    fallbackPending = false;
    var w = img.naturalWidth, h = img.naturalHeight;
    canvas.width = w;
    canvas.height = h;
    var ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(img, 0, 0); // 底图用原图
    var boxes = (viewerData && Array.isArray(viewerData.boxes)) ? viewerData.boxes : [];
    ctx.lineWidth = Math.max(2, Math.round(w / 400));
    ctx.strokeStyle = '#f87171';
    boxes.forEach(function (b) { // 按原图像素坐标 strokeRect
      ctx.strokeRect(b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);
    });
    canvas.hidden = false;
  };

  /* ==================== 历史记录区 ==================== */
  function textLine(cls, txt) {
    var p = document.createElement('p');
    p.className = cls;
    App.setText(p, txt);
    return p;
  }

  // list = state.records（数组，旧->新）；渲染时最新在前；查看按钮用 addEventListener + dataset.idx
  R.renderRecords = function (list) {
    App.state.records = Array.isArray(list) ? list : [];
    var box = App.el('recordsList');
    App.setText(box, '');

    if (App.state.records.length === 0) {
      box.appendChild(textLine('record-empty', '暂无检测记录'));
      return;
    }

    App.state.records
      .map(function (rec, i) { return { rec: rec, idx: i }; })
      .reverse() // 最新在前
      .forEach(function (pair) {
        var rec = pair.rec;
        var item = document.createElement('div');
        item.className = 'record-item' + (rec.status === 'failed' ? ' is-failed' : '');

        // 结果缩略图：result_url；加载失败 / 无结果图 → 退化为文件名文字
        if (rec.result_url) {
          var img = document.createElement('img');
          img.className = 'record-thumb';
          img.alt = '';
          img.src = rec.result_url;
          img.addEventListener('error', function () {
            var fb = document.createElement('p');
            fb.className = 'record-thumb-fallback';
            App.setText(fb, rec.filename);
            if (img.parentNode) img.parentNode.replaceChild(fb, img);
          });
          item.appendChild(img);
        } else {
          var fb0 = document.createElement('p');
          fb0.className = 'record-thumb-fallback';
          App.setText(fb0, rec.filename);
          item.appendChild(fb0);
        }

        var info = document.createElement('div');
        info.className = 'record-info';
        info.appendChild(textLine('record-name', rec.filename));
        info.appendChild(textLine('record-time mono', rec.detection_time || '—'));
        var cnt = (rec.crack_count === null || rec.crack_count === undefined) ? '—' : rec.crack_count;
        info.appendChild(textLine('record-count mono', '裂缝数：' + cnt));
        // 批次 / 存储位置：/detect 来源的记录没有这两个键 → 不显示
        if (Object.prototype.hasOwnProperty.call(rec, 'batch_id') && rec.batch_id) {
          info.appendChild(textLine('record-batch mono', '批次：' + rec.batch_id));
        }
        if (Object.prototype.hasOwnProperty.call(rec, 'input_url') && rec.input_url) {
          info.appendChild(textLine('record-path mono', '原图：' + rec.input_url));
        }

        // 状态徽章：failed 红色"失败"+error / done 绿色"完成"
        var status = document.createElement('div');
        status.className = 'record-status';
        var badge = document.createElement('span');
        if (rec.status === 'failed') {
          badge.className = 'badge badge-danger';
          App.setText(badge, '失败');
          status.appendChild(badge);
          if (rec.error) status.appendChild(textLine('record-error', rec.error));
        } else {
          badge.className = 'badge badge-ok';
          App.setText(badge, '完成');
          status.appendChild(badge);
        }
        info.appendChild(status);

        // 「查看」：addEventListener + dataset.idx 存索引，严禁把 JSON 塞 onclick（缺陷#3 规避）
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn-view';
        App.setText(btn, '查看');
        btn.dataset.idx = String(pair.idx);
        btn.addEventListener('click', function (e) {
          R.showRecord(Number(e.currentTarget.dataset.idx));
        });
        info.appendChild(btn);

        item.appendChild(info);
        box.appendChild(item);
      });
  };

  // 点「查看」→ 在大图对比区展示该记录（原图 input_url、结果 result_url、明细用记录数据）
  R.showRecord = function (idx) {
    var rec = App.state.records[idx];
    if (!rec) return;
    clearThumbActive();
    App.state.selectedIdx = null;
    viewerData = { boxes: rec.boxes, image_size: rec.image_size, crack_count: rec.crack_count };
    fallbackPending = false;

    App.el('resultCard').hidden = false; // 本会话可能还没建过批次

    var orig = App.el('origImg');
    if (rec.input_url) {
      orig.src = rec.input_url;
      orig.hidden = false;
    } else {
      orig.hidden = true; // /detect 来源的记录没有 input_url
    }
    App.el('fallbackCanvas').hidden = true;

    var resImg = App.el('resultImg');
    if (rec.status === 'failed') {
      resImg.hidden = true;
      R.renderViewerHint(rec.error || '识别失败', 'error');
    } else if (rec.result_url) {
      resImg.src = rec.result_url;
      resImg.hidden = false;
      if (rec.crack_count !== null && rec.crack_count !== undefined) {
        R.renderViewerHint('共检出 ' + rec.crack_count + ' 处裂缝', 'info');
      } else {
        R.renderViewerHint('', 'info');
      }
    } else {
      resImg.hidden = true;
      R.renderViewerHint('结果图不存在', 'error');
    }
    R.renderBoxTable({ boxes: rec.boxes, image_size: rec.image_size, crack_count: rec.crack_count });
    App.el('resultCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
})();
