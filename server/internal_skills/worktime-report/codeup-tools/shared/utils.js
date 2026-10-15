/**
 * 日期与并发控制工具函数
 */

// 团队位于北京时间（UTC+8），而服务器时区是 UTC。
// 日期区间一律按北京时间的自然日计算，否则跨时区会导致
// 「今天」的工时被整个跳过或重复统计。
const BEIJING_TZ = 'Asia/Shanghai';

function beijingToday() {
  return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

function getDateRange(dateStr) {
  const label = dateStr || beijingToday();
  const startDate = new Date(`${label}T00:00:00+08:00`);
  const endDate = new Date(`${label}T23:59:59.999+08:00`);
  if (isNaN(startDate.getTime())) {
    throw new Error(`无效的日期格式: ${dateStr}，请使用 YYYY-MM-DD`);
  }
  return { startDate, endDate, dateLabel: label };
}

function formatDate(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function formatTimestamp(ts) {
  if (!ts) return '';
  return new Date(ts).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
}

function isInDateRange(timestamp, startDate, endDate) {
  const d = new Date(timestamp);
  return d >= startDate && d <= endDate;
}

function pLimit(concurrency) {
  let activeCount = 0;
  const queue = [];

  function next() {
    if (queue.length > 0 && activeCount < concurrency) {
      activeCount++;
      const { fn, resolve, reject } = queue.shift();
      fn()
        .then(resolve)
        .catch(reject)
        .finally(() => {
          activeCount--;
          next();
        });
    }
  }

  return function limit(fn) {
    return new Promise((resolve, reject) => {
      queue.push({ fn, resolve, reject });
      next();
    });
  };
}

module.exports = {
  getDateRange,
  formatDate,
  formatTimestamp,
  isInDateRange,
  pLimit,
};
