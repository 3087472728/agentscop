/**
 * Excel 报表生成器
 *
 * 输出结构（先汇总、后明细，明细支持筛选）：
 *   Sheet1 汇总    —— 总览 + 按项目汇总 + 按人员汇总（按工时降序）
 *   Sheet2 每日统计 —— 日期为列（标星期）、人员为行、格内为当日工时；
 *                      红=工作日未填报、橙=当日>8h加班、蓝=周末填报；
 *                      另有 每人合计/加班/工作日未填 统计列
 *   Sheet3 工时明细 —— 全部明细记录（表头带筛选按钮，可按人/项目/日期筛选）
 */
const ExcelJS = require('exceljs');
const path = require('path');
const fs = require('fs');

const HEADER_FILL = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF4472C4' } };
const SECTION_FILL = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFD9E1F2' } };
const FILL_MISS = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFC7CE' } };   // 工作日未填报
const FILL_OVERTIME = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFE699' } }; // 当日>8h
const FILL_WEEKEND_WORK = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFBDD7EE' } }; // 周末填报
const FILL_WEEKEND_HEADER = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFE699' } };
const WEEKDAY_CN = ['日', '一', '二', '三', '四', '五', '六'];

function styleHeader(row) {
  row.font = { bold: true, color: { argb: 'FFFFFFFF' } };
  row.fill = HEADER_FILL;
  row.alignment = { horizontal: 'center', vertical: 'middle' };
  row.height = 24;
}

function styleSection(row) {
  row.font = { bold: true, color: { argb: 'FF1F4E79' }, size: 12 };
  row.fill = SECTION_FILL;
  row.height = 20;
}

function ensureReportsDir() {
  const reportsDir = path.resolve(__dirname, '..', 'reports');
  if (!fs.existsSync(reportsDir)) {
    fs.mkdirSync(reportsDir, { recursive: true });
  }
  return reportsDir;
}

function r2(x) {
  return Math.round(x * 100) / 100;
}

function pct(part, total) {
  return total > 0 ? Math.round((part / total) * 1000) / 10 + '%' : '0%';
}

// 'YYYY-MM-DD'（北京日期）→ 0=周日 ... 6=周六（不受服务器时区影响）
function beijingWeekday(dayStr) {
  const ts = Date.parse(dayStr + 'T00:00:00+08:00');
  return new Date(ts + 8 * 3600 * 1000).getUTCDay();
}

function isWeekend(dayStr) {
  const w = beijingWeekday(dayStr);
  return w === 0 || w === 6;
}

/**
 * 聚合统计：项目维度 / 人员维度
 * summary 含名单内所有人（未填报的工时为 0）
 */
function buildStats({ summary, details }) {
  const all = [...summary.values()];
  const totalHours = all.reduce((s, x) => s + x.totalHours, 0);
  const filed = all.filter((x) => x.recordCount > 0);
  const unfilled = all.filter((x) => x.recordCount === 0).map((x) => x.memberName);

  const projMap = new Map();
  for (const d of details) {
    const p = d.projectName || '未知项目';
    const cur = projMap.get(p) || { hours: 0, count: 0 };
    cur.hours += d.hours || 0;
    cur.count += 1;
    projMap.set(p, cur);
  }

  return {
    totalHours: r2(totalHours),
    filedCount: filed.length,
    totalCount: all.length,
    unfilled,
    recordCount: details.length,
    byProject: [...projMap.entries()]
      .map(([name, v]) => ({ name, hours: r2(v.hours), count: v.count, share: pct(v.hours, totalHours) }))
      .sort((a, b) => b.hours - a.hours),
    byPerson: all
      .map((x) => ({
        name: x.memberName,
        hours: r2(x.totalHours),
        count: x.recordCount,
        share: pct(x.totalHours, totalHours),
        status: x.recordCount > 0 ? '已填报' : '未填报',
      }))
      .sort((a, b) => b.hours - a.hours),
  };
}

// ===== Sheet: 汇总（总览 + 按项目 + 按人员） =====
function addSummarySheet(workbook, stats, dateLabel) {
  const sheet = workbook.addWorksheet('汇总');

  // ---- 总览 ----
  const overviewTitle = sheet.addRow(['总览']);
  styleSection(overviewTitle);
  sheet.addRow(['统计范围', dateLabel]);
  sheet.addRow(['总工时（小时）', stats.totalHours]);
  sheet.addRow(['填报情况', `${stats.filedCount}/${stats.totalCount} 人`]);
  sheet.addRow(['未填报', stats.unfilled.length ? stats.unfilled.join('、') : '无']);
  sheet.addRow(['工时条目', `${stats.recordCount} 条`]);
  sheet.addRow([]);

  // ---- 按项目汇总 ----
  const projTitle = sheet.addRow(['按项目汇总']);
  styleSection(projTitle);
  styleHeader(sheet.addRow(['项目', '工时（小时）', '占比', '条目数']));
  for (const p of stats.byProject) {
    sheet.addRow([p.name, p.hours, p.share, p.count]);
  }
  sheet.addRow([]);

  // ---- 按人员汇总 ----
  const personTitle = sheet.addRow(['按人员汇总（按工时降序）']);
  styleSection(personTitle);
  styleHeader(sheet.addRow(['姓名', '工时（小时）', '占比', '条目数', '状态']));
  for (const p of stats.byPerson) {
    const row = sheet.addRow([p.name, p.hours, p.share, p.count, p.status]);
    row.getCell(5).font = { color: { argb: p.status === '已填报' ? 'FF008000' : 'FFFF0000' } };
  }

  sheet.columns = [{ width: 26 }, { width: 16 }, { width: 10 }, { width: 10 }, { width: 10 }];
  return sheet;
}

// ===== Sheet: 每日统计（日期×人员矩阵 + 未填/加班标注） =====
function addDailyMatrixSheet(workbook, { summary, perDay, allDays }) {
  const days = allDays && allDays.length ? allDays : [...perDay.keys()].sort();
  const sheet = workbook.addWorksheet('每日统计', {
    views: [{ state: 'frozen', xSplit: 1, ySplit: 1 }],
  });

  const colTotal = 2 + days.length;      // 合计列
  const colOt = colTotal + 1;            // 加班列
  const colMiss = colOt + 1;             // 工作日未填列

  sheet.columns = [
    { header: '姓名', key: 'name', width: 14 },
    ...days.map((d) => ({ header: `${d.slice(5)} 周${WEEKDAY_CN[beijingWeekday(d)]}`, key: d, width: 11 })),
    { header: '合计', key: 'total', width: 10 },
    { header: '加班', key: 'ot', width: 10 },
    { header: '工作日未填', key: 'miss', width: 12 },
  ];
  styleHeader(sheet.getRow(1));

  // 周末列表头换底色，便于区分
  days.forEach((d, i) => {
    if (isWeekend(d)) {
      sheet.getRow(1).getCell(2 + i).fill = FILL_WEEKEND_HEADER;
    }
  });

  // 人员行按总工时降序，与汇总 Sheet 一致
  const ordered = [...summary.values()].sort((a, b) => b.totalHours - a.totalHours);
  const dayTotals = new Map(days.map((d) => [d, 0]));

  for (const s of ordered) {
    const name = s.memberName;
    const row = sheet.getRow(sheet.rowCount + 1);
    row.getCell(1).value = name;

    let rowTotal = 0;
    let overtime = 0;
    let missed = 0;

    days.forEach((d, i) => {
      const h = Math.round((perDay.get(d)?.get(name) || 0) * 100) / 100;
      const cell = row.getCell(2 + i);
      const weekend = isWeekend(d);

      if (h > 0) {
        cell.value = h;
        rowTotal += h;
        dayTotals.set(d, (dayTotals.get(d) || 0) + h);
        if (weekend) {
          cell.fill = FILL_WEEKEND_WORK; // 周末填报
          overtime += h;
        } else if (h > 8) {
          cell.fill = FILL_OVERTIME; // 工作日超 8 小时
          overtime += h - 8;
        }
      } else if (!weekend) {
        cell.value = '未填';
        cell.font = { color: { argb: 'FFFF0000' } };
        cell.fill = FILL_MISS;
        missed += 1;
      } else {
        cell.value = '—';
        cell.font = { color: { argb: 'FF999999' } };
      }
    });

    row.getCell(colTotal).value = Math.round(rowTotal * 100) / 100;
    row.getCell(colTotal).font = { bold: true };
    row.getCell(colOt).value = Math.round(overtime * 100) / 100;
    row.getCell(colMiss).value = missed;
    if (missed > 0) {
      row.getCell(colMiss).font = { color: { argb: 'FFFF0000' }, bold: true };
    }
  }

  // 按日合计行
  const totalRowObj = { name: '按日合计' };
  days.forEach((d, i) => {
    totalRowObj[d] = Math.round((dayTotals.get(d) || 0) * 100) / 100;
  });
  const totalRow = sheet.addRow(totalRowObj);
  totalRow.font = { bold: true };

  // 图例
  const legend = sheet.addRow([
    '说明：红=工作日未填报；橙=当日>8小时（加班）；蓝=周末填报；—=周末未填报（不统计）。"加班"列=工作日超出8小时部分+周末全部填报。',
  ]);
  legend.font = { italic: true, color: { argb: 'FF808080' }, size: 9 };
}

// ===== Sheet: 工时明细（按日期排序 + 表头筛选按钮） =====
function addDetailsSheet(workbook, details) {
  const detailSheet = workbook.addWorksheet('工时明细', {
    views: [{ state: 'frozen', ySplit: 1 }],
  });

  detailSheet.columns = [
    { header: '序号', key: 'index', width: 6 },
    { header: '姓名', key: 'name', width: 14 },
    { header: '归属日期', key: 'dayStr', width: 14 },
    { header: '项目', key: 'project', width: 24 },
    { header: '工作项标题', key: 'subject', width: 40 },
    { header: '工时类型', key: 'type', width: 12 },
    { header: '时长（小时）', key: 'hours', width: 14 },
    { header: '描述', key: 'description', width: 40 },
    { header: '录入时间', key: 'gmtCreate', width: 22 },
  ];

  styleHeader(detailSheet.getRow(1));

  const sortedDetails = [...details].sort((a, b) =>
    (a.dayStr || '').localeCompare(b.dayStr || '') ||
    (a.memberName || '').localeCompare(b.memberName || '', 'zh-CN')
  );

  sortedDetails.forEach((d, i) => {
    const row = detailSheet.addRow({
      index: i + 1,
      name: d.memberName || '未知',
      dayStr: d.dayStr || '',
      project: d.projectName || '',
      subject: d.subject || '',
      type: d.workType || '',
      hours: d.hours || 0,
      description: d.description || '',
      gmtCreate: d.gmtCreateFormatted || '',
    });
    row.alignment = { vertical: 'middle' };
  });

  // 表头筛选按钮：可按姓名/日期/项目直接筛选
  detailSheet.autoFilter = {
    from: { row: 1, column: 1 },
    to: { row: 1, column: detailSheet.columns.length },
  };
}

/**
 * 单日报表：汇总 + 每日统计 + 工时明细
 */
async function generateReport({ members, summary, details, perDay, allDays, dateLabel }) {
  const reportsDir = ensureReportsDir();

  const filePath = path.join(reportsDir, `工时报表_${dateLabel}.xlsx`);
  const workbook = new ExcelJS.Workbook();
  workbook.creator = '云效工时统计工具';
  workbook.created = new Date();

  const stats = buildStats({ summary, details });
  addSummarySheet(workbook, stats, dateLabel);
  if (perDay) {
    addDailyMatrixSheet(workbook, { summary, perDay, allDays });
  }
  addDetailsSheet(workbook, details);

  await workbook.xlsx.writeFile(filePath);
  console.log(`\n报表已生成: ${filePath}`);
  return filePath;
}

/**
 * 日期区间报表：一个文件多个 Sheet
 *   Sheet1 汇总（总览 + 项目维度 + 人员维度）
 *   Sheet2 每日统计（日期×人员矩阵，红=未填/橙=加班/蓝=周末）
 *   Sheet3 工时明细（带筛选按钮）
 */
async function generateRangeReport({ members, summary, details, perDay, allDays, dateLabel }) {
  const reportsDir = ensureReportsDir();

  const filePath = path.join(reportsDir, `工时报表_${dateLabel}.xlsx`);
  const workbook = new ExcelJS.Workbook();
  workbook.creator = '云效工时统计工具';
  workbook.created = new Date();

  const stats = buildStats({ summary, details });
  addSummarySheet(workbook, stats, dateLabel);
  addDailyMatrixSheet(workbook, { summary, perDay, allDays });
  addDetailsSheet(workbook, details);

  await workbook.xlsx.writeFile(filePath);
  console.log(`\n报表已生成: ${filePath}`);
  return filePath;
}

/**
 * 单日文本日报：总览 → 项目汇总 → 人员汇总 → 明细简列
 */
function generateDailyText({ details, summary, dateLabel, targetNames }) {
  const reportsDir = ensureReportsDir();
  const stats = buildStats({ summary, details });

  const byName = new Map();
  for (const d of details) {
    const name = d.memberName || '未知';
    if (!byName.has(name)) byName.set(name, []);
    byName.get(name).push(d);
  }

  const lines = [];
  lines.push(`团队日报 ${dateLabel}`);
  lines.push('====================');
  lines.push('【总览】');
  lines.push(`总工时: ${stats.totalHours} 小时 | 已填报: ${stats.filedCount}/${stats.totalCount} 人 | 条目: ${stats.recordCount} 条`);
  lines.push(`未填报: ${stats.unfilled.length ? stats.unfilled.join('、') : '无'}`);
  lines.push('');

  lines.push('【按项目汇总】');
  for (const p of stats.byProject) {
    lines.push(`  ${p.name}: ${p.hours} 小时（占 ${p.share}，${p.count} 条）`);
  }
  lines.push('');

  lines.push('【按人员汇总】（按工时降序）');
  for (const p of stats.byPerson) {
    lines.push(`  ${p.name}: ${p.hours} 小时（${p.count} 条）`);
  }
  lines.push('');

  lines.push('【明细】');
  for (const p of stats.byPerson) {
    const items = byName.get(p.name) || [];
    if (!items.length) {
      lines.push(`  ${p.name}：未填报`);
      continue;
    }
    const brief = items
      .map((d) => `${d.projectName || ''} ${d.hours}h ${d.subject || ''}${d.description ? `（${d.description}）` : ''}`)
      .join('；');
    lines.push(`  ${p.name}：${brief}`);
  }

  const filePath = path.join(reportsDir, `日报_${dateLabel}.txt`);
  fs.writeFileSync(filePath, lines.join('\n'), 'utf-8');
  console.log(`日报已生成: ${filePath}`);
  return filePath;
}

module.exports = { generateReport, generateDailyText, generateRangeReport };
