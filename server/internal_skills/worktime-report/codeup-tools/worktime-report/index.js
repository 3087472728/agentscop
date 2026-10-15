/**
 * 云效每日工时自动统计 — 主入口
 *
 * 用法:
 *   node worktime-report/index.js                        # 统计今天
 *   node worktime-report/index.js 2025-04-15             # 统计指定日期
 *   node worktime-report/index.js 2025-04-01 2025-04-30  # 统计日期区间（生成多 Sheet 报表）
 *
 * 工时归属日期取记录的 gmtStart（北京时间自然日），补填历史工时不影响当天统计。
 */

const { getDateRange, isInDateRange, formatTimestamp, pLimit } = require('../shared/utils');
const { searchProjects, getProjectMembers, listAllWorkitems, getEffortRecords } = require('../shared/yunxiao-client');
const { generateReport, generateDailyText, generateRangeReport } = require('./report-generator');

const CONCURRENCY = 5;
const BEIJING_OFFSET = 8 * 3600 * 1000;

// 统计名单：自动取全部项目成员并集（按 userId 去重），项目里增减成员自动生效
// 可选排除名单：codeup-tools/data/exclude-names.json（用户名数组），留空/缺省则不排除任何人
const EXCLUDE_NAMES = (() => {
  try {
    const v = require('../data/exclude-names.json');
    return Array.isArray(v) ? v : [];
  } catch (e) {
    return [];
  }
})();

// 工时归属日期（gmtStart 优先，回退 gmtCreate）
function effortDateOf(r) {
  const raw = r.gmtStart ?? r.gmtCreate;
  if (raw === undefined || raw === null) return null;
  const ts = new Date(typeof raw === 'number' ? raw : parseInt(raw, 10));
  return isNaN(ts.getTime()) ? null : ts;
}

// 归属日期的北京时间字符串 YYYY-MM-DD
function beijingDateStr(ts) {
  return new Date(ts.getTime() + BEIJING_OFFSET).toISOString().slice(0, 10);
}

function createdTsOf(r) {
  if (!r.gmtCreate) return null;
  const ts = new Date(typeof r.gmtCreate === 'number' ? r.gmtCreate : parseInt(r.gmtCreate, 10));
  return isNaN(ts.getTime()) ? null : ts;
}

async function main() {
  const args = process.argv.slice(2);
  let startDate, endDate, dateLabel;
  let isRange = false;

  if (args.length >= 2) {
    const s = new Date(args[0] + 'T00:00:00+08:00');
    const e = new Date(args[1] + 'T23:59:59.999+08:00');
    if (isNaN(s.getTime()) || isNaN(e.getTime()) || s > e) {
      console.error('无效的日期区间，请使用: node worktime-report/index.js <起始日期> <结束日期>（YYYY-MM-DD）');
      process.exit(1);
    }
    startDate = s;
    endDate = e;
    dateLabel = `${args[0]}_至_${args[1]}`;
    isRange = true;
  } else {
    ({ startDate, endDate, dateLabel } = getDateRange(args[0]));
  }

  console.log('===== 云效工时统计 =====');
  console.log(`统计范围: ${dateLabel}`);
  console.log(`时间范围: ${startDate.toISOString()} ~ ${endDate.toISOString()}\n`);

  // 1. 获取所有项目
  console.log('[1/4] 获取所有项目...');
  const projects = await searchProjects();
  if (projects.length === 0) {
    console.log('未找到任何项目。');
    return;
  }

  // 2. 收集所有项目的成员和工作项
  console.log(`\n[2/4] 遍历 ${projects.length} 个项目，获取成员和工作项...`);
  const allMembers = new Map();
  const allWorkitems = [];

  for (const project of projects) {
    console.log(`  项目: ${project.name}`);

    const members = await getProjectMembers(project.id);
    for (const m of members) {
      if (EXCLUDE_NAMES.includes(m.userName)) continue;
      if (!allMembers.has(m.userId)) {
        allMembers.set(m.userId, m);
      }
    }

    const workitems = await listAllWorkitems(project.id);
    for (const wi of workitems) {
      allWorkitems.push({ ...wi, projectName: project.name });
    }
  }

  console.log(`共 ${allMembers.size} 名成员（去重），${allWorkitems.length} 个工作项`);

  if (allWorkitems.length === 0) {
    console.log('\n未找到工作项，无需统计工时。');
    return;
  }

  // 3. 并发查询每个工作项的工时记录
  console.log(`\n[3/4] 查询 ${allWorkitems.length} 个工作项的实际工时（并发数 ${CONCURRENCY}）...`);
  const limit = pLimit(CONCURRENCY);
  const allRecords = [];
  let completed = 0;

  const tasks = allWorkitems.map((wi) =>
    limit(async () => {
      const records = await getEffortRecords(wi.id);
      completed++;
      if (completed % 20 === 0 || completed === allWorkitems.length) {
        process.stdout.write(`  进度: ${completed}/${allWorkitems.length}\n`);
      }
      return records.map((r) => ({
        ...r,
        workitemSubject: wi.subject || '',
        workitemSerialNumber: wi.serialNumber || '',
        projectName: wi.projectName || '',
      }));
    })
  );

  const results = await Promise.all(tasks);
  for (const records of results) {
    allRecords.push(...records);
  }

  // 4. 按工时归属日期过滤并聚合
  console.log(`\n[4/4] 聚合数据并生成报表...`);
  const filteredRecords = allRecords.filter((r) => {
    const ts = effortDateOf(r);
    if (!ts) return false;
    return isInDateRange(ts, startDate, endDate);
  });
  console.log(`共 ${allRecords.length} 条工时记录，日期范围内 ${filteredRecords.length} 条`);

  // 名单补充：云效组织级用户接口（PAT）未开放，
  // 团队名单 = 项目成员并集 ∪ 实际填报过工时的人（自动去重）
  for (const r of allRecords) {
    const u = r.creator || r.owner;
    if (u && u.id && !allMembers.has(u.id) && !EXCLUDE_NAMES.includes(u.name || '')) {
      allMembers.set(u.id, { userId: u.id, userName: u.name || u.id });
    }
  }

  const listedIds = new Set(Array.from(allMembers.values()).map((m) => m.userId));
  const outsideFilers = new Map(); // 名单外填报人员（不计入汇总，单独列示）
  const summary = new Map();
  const details = [];
  const perDay = new Map(); // 'YYYY-MM-DD' -> Map(name -> hours)（整周记录按天分摊）

  for (const record of filteredRecords) {
    const userId = record.creator?.id || record.owner?.id || '';
    const userName = record.creator?.name || record.owner?.name || userId;
    const hours = record.actualTime || 0;
    const ts = effortDateOf(record);
    const dayStr = ts ? beijingDateStr(ts) : '';

    if (!listedIds.has(userId)) {
      outsideFilers.set(userName, (outsideFilers.get(userName) || 0) + hours);
      continue;
    }

    if (!summary.has(userId)) {
      summary.set(userId, { totalHours: 0, recordCount: 0, memberName: userName });
    }
    const entry = summary.get(userId);
    entry.totalHours += hours;
    entry.recordCount += 1;

    details.push({
      userId,
      memberName: userName,
      projectName: record.projectName,
      subject: record.workitemSubject,
      serialNumber: record.workitemSerialNumber,
      workType: record.workType || '',
      hours: Math.round(hours * 100) / 100,
      dayStr,
      description: record.description || '',
      gmtCreate: record.gmtCreate,
      gmtCreateFormatted: formatTimestamp(
        typeof record.gmtCreate === 'number' ? record.gmtCreate : parseInt(record.gmtCreate, 10)
      ),
    });

    // 每日分摊：gmtStart~gmtEnd 跨多天的整周记录按天均摊
    if (dayStr) {
      const rawEnd = record.gmtEnd;
      let endTs = ts;
      if (rawEnd !== undefined && rawEnd !== null) {
        const e2 = new Date(typeof rawEnd === 'number' ? rawEnd : parseInt(rawEnd, 10));
        if (!isNaN(e2.getTime()) && e2 > ts) endTs = e2;
      }
      const spanDays = Math.min(62, Math.round((endTs.getTime() - ts.getTime()) / 86400000) + 1);
      const share = hours / spanDays;
      const cur = new Date(ts.getTime());
      for (let i = 0; i < spanDays; i++) {
        const d = beijingDateStr(cur);
        if (!perDay.has(d)) perDay.set(d, new Map());
        const dayMap = perDay.get(d);
        dayMap.set(userName, (dayMap.get(userName) || 0) + share);
        cur.setTime(cur.getTime() + 86400000);
      }
    }
  }

  // 添加未填报的名单内成员到汇总
  for (const [, member] of allMembers) {
    if (!summary.has(member.userId)) {
      summary.set(member.userId, {
        totalHours: 0,
        recordCount: 0,
        memberName: member.userName,
      });
    }
  }

  if (outsideFilers.size > 0) {
    console.log('\n名单外填报（不在统计名单，未计入汇总）:');
    for (const [n, h] of outsideFilers) {
      console.log(`  ${n}: ${Math.round(h * 100) / 100} 小时`);
    }
  }

  const membersList = [...allMembers.values()];
  const filedCount = [...summary.values()].filter((s) => s.recordCount > 0).length;
  const totalCount = allMembers.size;
  const totalHours = Math.round([...summary.values()].reduce((sum, s) => sum + s.totalHours, 0) * 100) / 100;

  // 按项目聚合（供对话回复使用）
  const projStats = new Map();
  for (const d of details) {
    const p = d.projectName || '未知项目';
    const cur = projStats.get(p) || { hours: 0, count: 0 };
    cur.hours += d.hours || 0;
    cur.count += 1;
    projStats.set(p, cur);
  }

  console.log(`\n===== 统计完成 =====`);
  console.log(`统计范围: ${dateLabel}`);
  console.log(`总工时: ${totalHours} 小时 | 工时条目: ${details.length} 条`);
  console.log(`已填报: ${filedCount}/${totalCount} 人`);
  const unfilledNames = membersList
    .filter((m) => !((summary.get(m.userId) || {}).recordCount > 0))
    .map((m) => m.userName);
  console.log(`未填报: ${unfilledNames.length ? unfilledNames.join('、') : '无'}`);

  console.log(`\n按项目汇总（工时降序）:`);
  for (const [p, st] of [...projStats.entries()].sort((a, b) => b[1].hours - a[1].hours)) {
    const share = totalHours > 0 ? Math.round((st.hours / totalHours) * 1000) / 10 : 0;
    console.log(`  ${p}: ${Math.round(st.hours * 100) / 100} 小时（占 ${share}%，${st.count} 条）`);
  }

  console.log(`\n按人员汇总（工时降序）:`);
  for (const s of [...summary.values()].sort((a, b) => b.totalHours - a.totalHours)) {
    const share = totalHours > 0 ? Math.round((s.totalHours / totalHours) * 1000) / 10 : 0;
    console.log(`  ${s.memberName}: ${Math.round(s.totalHours * 100) / 100} 小时（${s.recordCount} 条，占 ${share}%）${s.recordCount > 0 ? '' : ' [未填报]'}`);
  }

  // 统计区间内的全部北京自然日（用于每日矩阵标注工作日/周末/未填报）
  const allDays = (() => {
    const out = [];
    const cur = new Date(startDate.getTime() + BEIJING_OFFSET);
    cur.setUTCHours(0, 0, 0, 0);
    const endMs = endDate.getTime() + BEIJING_OFFSET;
    while (cur.getTime() <= endMs && out.length < 366) {
      out.push(cur.toISOString().slice(0, 10));
      cur.setUTCDate(cur.getUTCDate() + 1);
    }
    return out;
  })();

  if (isRange) {
    await generateRangeReport({ members: membersList, summary, details, perDay, allDays, dateLabel });
  } else {
    await generateReport({ members: membersList, summary, details, perDay, allDays, dateLabel });
    generateDailyText({ details, summary, dateLabel });
  }
}

main().catch((err) => {
  console.error('执行失败:', err.message || err);
  process.exit(1);
});
