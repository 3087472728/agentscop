/**
 * 云效工时统计 — 全部员工版
 *
 * 与 index.js 的区别：不按 DAILY_REPORT_NAMES 名单过滤，
 * 统计云效组织下所有项目成员（30 人）的每日工时填报情况。
 *
 * 用法:
 *   node worktime-report/index-all.js              # 统计今天
 *   node worktime-report/index-all.js 2026-08-29   # 统计指定日期
 */

const { getDateRange, isInDateRange, formatTimestamp, pLimit } = require('../shared/utils');
const { searchProjects, getProjectMembers, listAllWorkitems, getEffortRecords } = require('../shared/yunxiao-client');
const { generateReport, generateDailyText } = require('./report-generator');

const CONCURRENCY = 5;

async function main() {
  const dateArg = process.argv[2] || null;
  const { startDate, endDate, dateLabel } = getDateRange(dateArg);

  console.log('===== 云效工时统计（全部员工）=====');
  console.log(`统计日期: ${dateLabel}`);
  console.log(`时间范围: ${startDate.toISOString()} ~ ${endDate.toISOString()}\n`);

  // 1. 获取所有项目
  console.log('[1/4] 获取所有项目...');
  const projects = await searchProjects();
  if (projects.length === 0) {
    console.log('未找到任何项目。');
    return;
  }

  // 2. 收集所有项目的全部成员和工作项（不做名单过滤）
  console.log(`\n[2/4] 遍历 ${projects.length} 个项目，获取成员和工作项...`);
  const allMembers = new Map();
  const allWorkitems = [];

  for (const project of projects) {
    console.log(`  项目: ${project.name}`);

    const members = await getProjectMembers(project.id);
    for (const m of members) {
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

  // 4. 按目标日期过滤并聚合
  console.log(`\n[4/4] 聚合数据并生成报表...`);
  const effortDate = (r) => {
    const raw = r.gmtStart ?? r.gmtCreate;
    if (raw === undefined || raw === null) return null;
    const ts = new Date(typeof raw === 'number' ? raw : parseInt(raw, 10));
    return isNaN(ts.getTime()) ? null : ts;
  };
  const filteredRecords = allRecords.filter((r) => {
    const ts = effortDate(r);
    if (!ts) return false;
    return isInDateRange(ts, startDate, endDate);
  });
  console.log(`共 ${allRecords.length} 条工时记录，日期范围内 ${filteredRecords.length} 条`);

  const summary = new Map();
  const details = [];

  const memberIds = new Set(Array.from(allMembers.values()).map((m) => m.userId));

  for (const record of filteredRecords) {
    const userId = record.creator?.id || record.owner?.id || '';
    const userName = record.creator?.name || record.owner?.name || userId;
    const hours = record.actualTime || 0;

    // 只统计成员，但保留所有填报记录到明细
    if (memberIds.has(userId)) {
      if (!summary.has(userId)) {
        summary.set(userId, { totalHours: 0, recordCount: 0, memberName: userName });
      }
      const entry = summary.get(userId);
      entry.totalHours += hours;
      entry.recordCount += 1;
    }

    details.push({
      userId,
      memberName: userName,
      projectName: record.projectName,
      subject: record.workitemSubject,
      serialNumber: record.workitemSerialNumber,
      workType: record.workType || '',
      hours: Math.round(hours * 100) / 100,
      description: record.description || '',
      gmtCreate: record.gmtCreate,
      gmtCreateFormatted: formatTimestamp(
        typeof record.gmtCreate === 'number' ? record.gmtCreate : parseInt(record.gmtCreate, 10)
      ),
    });
  }

  // 添加未填报的成员到汇总
  for (const [, member] of allMembers) {
    if (!summary.has(member.userId)) {
      summary.set(member.userId, {
        totalHours: 0,
        recordCount: 0,
        memberName: member.userName,
      });
    }
  }

  const membersList = [...allMembers.values()];
  await generateReport({ members: membersList, summary, details, dateLabel });
  generateDailyText({ details, summary, dateLabel, targetNames: membersList.map((m) => m.userName) });

  const filedCount = [...summary.values()].filter((s) => s.recordCount > 0).length;
  const totalCount = allMembers.size;
  const totalHours = Math.round([...summary.values()].reduce((sum, s) => sum + s.totalHours, 0) * 100) / 100;
  console.log(`\n===== 统计完成 =====`);
  console.log(`已填报: ${filedCount}/${totalCount} 人`);
  console.log(`总工时: ${totalHours} 小时`);
}

main().catch((err) => {
  console.error('执行失败:', err.message || err);
  process.exit(1);
});
