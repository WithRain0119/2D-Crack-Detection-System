// config.js —— 全局常量、共享状态与通用工具（依赖顺序：第 1 个加载，无前置依赖）
// 通过 window.App 命名空间向 api/render/app 模块共享数据
window.App = window.App || {};

/* ==================== 后端与超时常量 ==================== */
App.API = 'http://127.0.0.1:8000'; // 后端地址（硬编码；除它以外页面零外链）
App.TIMEOUT = {
  probe: 8000, // 健康探测
  init: 60000, // 批次创建与上传（大文件多）
  def: 15000   // 其余请求（run/status/records/logs）
};

/* ==================== 轮询常量 ==================== */
App.POLL = {
  interval: 1000,            // 每 1 秒查一次进度
  maxMs: 30 * 60 * 1000,     // 轮询总时长上限 30 分钟
  maxFails: 5                // 连续失败 5 次即停止并提示
};

/* ==================== 图片扩展名集合（小写、含点） ==================== */
App.IMG_EXT = new Set(['.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff']);

/* ==================== 共享状态（唯一状态源） ==================== */
App.state = {
  backend: 'unknown',  // online | model-missing | offline | unknown
  files: [],           // 本地筛选后的有效图片 File 数组
  skipped: [],         // 被跳过的非图片文件名
  conf: 0.25,          // 置信度阈值（随 init 上传）
  mode: 'single',      // folder（选文件夹/拖拽）| single（选图片）
  batchId: '',         // 当前批次 ID（含全角冒号，进 URL 必须 encodeURIComponent）
  inputDir: '',        // 后端返回的相对路径 static/<id>/input
  outputDir: '',       // 后端返回的相对路径 static/<id>/output
  pollToken: 0,        // 轮询令牌：每次启停自增，作废旧轮询
  pollAbort: null,     // AbortController：切换批次时中断在途请求
  pollTimer: null,     // 下一次轮询的 setTimeout 句柄
  pollDeadline: 0,     // 轮询截止时间戳
  pollFailCount: 0,    // 连续失败计数
  thumbMap: new Map(), // stored_name -> { li, img, badge, data }（每条自带数据，无全局 currentImage）
  records: [],         // GET /api/api/records 的 resp.records（数组，原始顺序旧->新）
  selectedIdx: null    // 缩略图当前选中下标
};

/* ==================== 通用工具 ==================== */
// 按 id 取节点（全站取 DOM 的唯一入口）
App.el = function (id) { return document.getElementById(id); };

// 文本写入统一入口：一律 textContent，用户数据永不进 innerHTML
App.setText = function (node, text) {
  if (!node) return;
  node.textContent = (text === null || text === undefined) ? '' : String(text);
};

// 原图 URL：API + input_dir 逐段编码 + 存储文件名编码
// （result_url / input_url 等后端给的完整 URL 一律原样使用，不用本函数拼）
App.urlForInput = function (storedName) {
  if (!App.state.inputDir) return '';
  var dir = App.state.inputDir.split('/').map(encodeURIComponent).join('/');
  return App.API + '/' + dir + '/' + encodeURIComponent(storedName);
};
