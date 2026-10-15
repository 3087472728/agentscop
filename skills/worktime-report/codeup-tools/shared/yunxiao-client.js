const https = require('https');
const zlib = require('zlib');
const config = require('./config');

const BASE_URL = "https://" + config.domain;
const COMMON_HEADERS = {
  'Content-Type': 'application/json',
  'x-yunxiao-token': config.token,
  'Accept': '*/*',
  'Accept-Encoding': 'gzip, deflate',
  'User-Agent': 'codeup-tools/2.0',
};

function httpRequest(resolve, reject, method, path, body) {
  const url = new URL(BASE_URL + path);
  const options = {
    hostname: url.hostname,
    port: 443,
    path: url.pathname + url.search,
    method,
    headers: { ...COMMON_HEADERS },
    timeout: 30000,
  };

  const req = https.request(options, (res) => {
    const chunks = [];
    res.on('data', (chunk) => chunks.push(chunk));
    res.on('end', () => {
      const raw = Buffer.concat(chunks);
      const decode = (buf) => {
        if (res.statusCode >= 400) {
          reject(new Error("HTTP " + res.statusCode + ": " + buf.toString().substring(0, 200)));
          return;
        }
        try {
          resolve({ data: JSON.parse(buf.toString()), headers: res.headers });
        } catch {
          resolve({ data: buf.toString(), headers: res.headers });
        }
      };
      if (res.headers['content-encoding'] === 'gzip') {
        zlib.gunzip(raw, (err, decoded) => decode(err ? raw : decoded));
      } else if (res.headers['content-encoding'] === 'deflate') {
        zlib.inflate(raw, (err, decoded) => decode(err ? raw : decoded));
      } else {
        decode(raw);
      }
    });
  });

  req.on('error', reject);
  req.on('timeout', () => {
    req.destroy();
    reject(new Error("请求超时: " + method + " " + path));
  });

  if (body) {
    req.write(JSON.stringify(body));
  }
  req.end();
}

function request(method, path, body = null) {
  return new Promise((resolve, reject) => {
    httpRequest(
      (result) => resolve(result.data),
      reject,
      method, path, body
    );
  });
}

function requestWithHeaders(method, path, body = null) {
  return new Promise((resolve, reject) => {
    httpRequest(resolve, reject, method, path, body);
  });
}

// ====== 项目相关 ======

async function searchProjects() {
  const path = "/oapi/v1/projex/organizations/" + config.organizationId + "/projects:search";
  const allProjects = [];
  let page = 1;
  const perPage = 200;

  while (true) {
    const body = { page, perPage, orderBy: 'gmtCreate', sort: 'desc' };
    const data = await request('POST', path, body);
    const items = Array.isArray(data) ? data : [];
    if (!items.length) break;

    allProjects.push(...items);
    if (items.length < perPage) break;
    page++;
  }

  console.log("获取到 " + allProjects.length + " 个项目");
  return allProjects.map((p) => ({ id: p.id, name: p.name }));
}

async function getProjectMembers(projectId) {
  const path = "/oapi/v1/projex/organizations/" + config.organizationId + "/projects/" + projectId + "/members";
  const data = await request('GET', path);
  return (Array.isArray(data) ? data : []).map((m) => ({
    userId: m.userId,
    userName: m.userName,
    roleName: m.roleName,
  }));
}

// ====== 迭代相关 ======

async function listIterations(projectId) {
  const path = "/oapi/v1/projex/organizations/" + config.organizationId + "/projects/" + projectId + "/sprints";
  const data = await request('GET', path);
  return (Array.isArray(data) ? data : []).map((it) => ({
    id: it.id,
    name: it.name,
    gmtStart: it.startDate,
    gmtEnd: it.endDate,
    status: it.status,
  }));
}

// ====== 工作项相关 ======

async function searchWorkitems(projectId, categories, page = 1, perPage = 50) {
  const path = "/oapi/v1/projex/organizations/" + config.organizationId + "/workitems:search";
  const body = {
    spaceId: projectId,
    category: categories.join(','),
    page,
    perPage,
    orderBy: 'gmtModified',
    sort: 'desc',
  };
  const data = await request('POST', path, body);
  return Array.isArray(data) ? data : [];
}

async function searchWorkitemsPaginated(projectId, categories, page = 1, perPage = 50, conditions = null) {
  const path = "/oapi/v1/projex/organizations/" + config.organizationId + "/workitems:search";
  const body = {
    spaceId: projectId,
    category: categories.join(','),
    page,
    perPage,
    orderBy: 'gmtModified',
    sort: 'desc',
  };
  if (conditions) {
    body.conditions = typeof conditions === 'string' ? conditions : JSON.stringify(conditions);
  }

  const result = await requestWithHeaders('POST', path, body);
  const items = Array.isArray(result.data) ? result.data : [];
  const total = parseInt(result.headers['x-total'] || '0', 10);
  const totalPages = parseInt(result.headers['x-total-pages'] || '1', 10);

  return { items, total, totalPages, page: parseInt(result.headers['x-page'] || '1', 10) };
}

async function listAllWorkitems(projectId, categories = ['Req', 'Bug', 'Task', 'Risk']) {
  const allItems = [];
  let page = 1;
  const perPage = 200;

  while (true) {
    const result = await searchWorkitemsPaginated(projectId, categories, page, perPage);
    if (!result.items.length) break;

    allItems.push(...result.items);
    console.log("  已获取 " + allItems.length + " 个工作项（第 " + page + " 页）");
    if (page >= result.totalPages) break;
    page++;
  }

  console.log("共获取 " + allItems.length + " 个工作项");
  return allItems;
}

async function getWorkitem(workitemId) {
  const path = "/oapi/v1/projex/organizations/" + config.organizationId + "/workitems/" + workitemId;
  const data = await request('GET', path);
  return data;
}

// ====== customFieldValues 解析 ======

function parseCustomFieldValues(item) {
  const cvs = item.customFieldValues || [];
  const result = {};

  for (const cv of cvs) {
    const val = cv.values && cv.values.length > 0 ? cv.values[0] : null;
    if (!val) continue;

    switch (cv.fieldId) {
      case '79':
        result.planStart = val.identifier || val.displayValue || null;
        break;
      case '80':
        result.planEnd = val.identifier || val.displayValue || null;
        break;
      case '101586':
        result.estimatedHours = parseFloat(val.identifier || '0') || 0;
        break;
      case '101587':
        result.actualHoursSummary = parseFloat(val.identifier || '0') || 0;
        break;
      case 'sumPlanedLaborHour':
        result.estimatedHoursSummary = parseFloat(val.identifier || '0') || 0;
        break;
      case 'sumActualLaborHour':
        result.actualHoursSummaryAuto = parseFloat(val.identifier || '0') || 0;
        break;
      case 'priority':
        result.priority = val.displayValue || val.identifier || null;
        break;
    }
  }

  return result;
}

function extractWorkitemMeta(item) {
  const fields = parseCustomFieldValues(item);
  const parentId = item.parentId === 'EMPTY_VALUE' ? null : (item.parentId || null);

  // 生命周期阶段检测
  const fieldMapping = require('../data/field-mapping.json');
  const lifecyclePhases = fieldMapping.lifecyclePhases || {};
  const completedPhases = fieldMapping.completedLifecyclePhases || ['已完成'];
  const devDoneStatuses = ['开发完成', '测试中', '测试完成', '验收中', '已完成'];
  const testDoneStatuses = ['测试完成', '验收中', '已完成'];

  const statusName = item.status ? item.status.name : '';
  const statusId = item.status ? item.status.id : '';
  const lifecyclePhase = lifecyclePhases[statusId] || statusName || '未知';
  const isCompleted = completedPhases.includes(lifecyclePhase);

  // 提取开发完成时间
  let devCompletedAt = null;
  if (statusName === '开发完成') {
    devCompletedAt = item.updateStatusAt || null;
  } else if (devDoneStatuses.includes(statusName)) {
    devCompletedAt = '已通过';
  }

  // 提取测试完成时间
  let testCompletedAt = null;
  if (statusName === '测试完成') {
    testCompletedAt = item.updateStatusAt || null;
  } else if (testDoneStatuses.includes(statusName)) {
    testCompletedAt = '已通过';
  }

  return {
    id: item.id,
    idPath: item.idPath || '',
    parentId: parentId,
    serialNumber: item.serialNumber || '',
    subject: item.subject || '',
    categoryId: item.categoryId || '',
    statusId: statusId,
    statusName: statusName,
    statusDisplayName: item.status ? item.status.displayName : '',
    lifecyclePhase: lifecyclePhase,
    isCompleted: isCompleted,
    devCompletedAt: devCompletedAt,
    testCompletedAt: testCompletedAt,
    assignedToId: item.assignedTo ? item.assignedTo.id : '',
    assignedToName: item.assignedTo ? item.assignedTo.name : '',
    spaceId: item.space ? item.space.id : '',
    spaceName: item.space ? item.space.name : '',
    sprintId: item.sprint ? item.sprint.id : '',
    sprintName: item.sprint ? item.sprint.name : '',
    workitemTypeId: item.workitemType ? item.workitemType.id : '',
    workitemTypeName: item.workitemType ? item.workitemType.name : '',
    gmtCreate: item.gmtCreate || null,
    gmtModified: item.gmtModified || null,
    updateStatusAt: item.updateStatusAt || null,
    logicalStatus: item.logicalStatus || '',
    planStart: fields.planStart || null,
    planEnd: fields.planEnd || null,
    estimatedHours: fields.estimatedHours || 0,
    estimatedHoursSummary: fields.estimatedHoursSummary || 0,
    actualHoursSummary: fields.actualHoursSummary || 0,
    priority: fields.priority || null,
  };
}

// ====== 创建工作项 ======

async function createWorkitem(params) {
  const path = "/oapi/v1/projex/organizations/" + config.organizationId + "/workitems";
  const body = {
    spaceId: params.projectId,
    subject: params.subject,
    description: params.description || '',
    category: params.category || 'Task',
    workitemTypeId: params.workitemTypeId || '',
    assignedTo: params.assignedTo || '',
    iterationId: params.iterationId || '',
    parentId: params.parentId || '',
  };
  return request('POST', path, body);
}

async function batchCreateWorkitems(projectId, items) {
  const results = { success: [], failed: [] };
  const limit = require('./utils').pLimit(3);

  const tasks = items.map((item, idx) =>
    limit(async () => {
      try {
        const result = await createWorkitem({ ...item, projectId });
        console.log("  [" + (idx + 1) + "/" + items.length + "] 创建成功: " + item.subject);
        results.success.push({ index: idx, subject: item.subject, result });
      } catch (err) {
        console.error("  [" + (idx + 1) + "/" + items.length + "] 创建失败: " + item.subject + " - " + err.message);
        results.failed.push({ index: idx, subject: item.subject, error: err.message });
      }
    })
  );

  await Promise.all(tasks);
  return results;
}

// ====== 更新工作项 ======

async function updateWorkitem(workitemId, fields) {
  const path = "/oapi/v1/projex/organizations/" + config.organizationId + "/workitems/" + workitemId;
  return request('PUT', path, fields);
}

// ====== 工时相关 ======

async function getEffortRecords(workitemId) {
  const path = "/oapi/v1/projex/organizations/" + config.organizationId + "/workitems/" + workitemId + "/effortRecords";
  try {
    const data = await request('GET', path);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    console.warn("  查询工作项 " + workitemId + " 的工时失败: " + err.message);
    return [];
  }
}

/**
 * 查询工时记录（安全版本）
 * 与 getEffortRecords 的区别：不吞异常，返回 { ok, records, error }，
 * 对 429/5xx/超时 自动重试一次，供审计类工具区分"无记录"与"查询失败"。
 */
async function getEffortRecordsSafe(workitemId) {
  const path = "/oapi/v1/projex/organizations/" + config.organizationId + "/workitems/" + workitemId + "/effortRecords";

  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const data = await request('GET', path);
      return { ok: true, records: Array.isArray(data) ? data : [], error: null };
    } catch (err) {
      const msg = String(err.message || err);
      const retryable = /HTTP 429|HTTP 5\d\d|请求超时/.test(msg);
      if (attempt >= 2 || !retryable) {
        return { ok: false, records: [], error: msg };
      }
    }
  }
}

async function getAllEffortRecords(projectId, categories) {
  const workitems = await listAllWorkitems(projectId, categories);
  const limit = require('./utils').pLimit(5);
  const allRecords = [];
  let completed = 0;

  const tasks = workitems.map((wi) =>
    limit(async () => {
      const records = await getEffortRecords(wi.id);
      completed++;
      if (completed % 50 === 0 || completed === workitems.length) {
        process.stdout.write("  工时查询进度: " + completed + "/" + workitems.length + "\n");
      }
      return records.map((r) => ({
        ...r,
        workitemSubject: wi.subject || '',
        workitemCategory: wi.category || '',
        workitemSerialNumber: wi.serialNumber || '',
      }));
    })
  );

  const results = await Promise.all(tasks);
  for (const records of results) {
    allRecords.push(...records);
  }
  return allRecords;
}

module.exports = {
  searchProjects,
  getProjectMembers,
  listIterations,
  searchWorkitems,
  searchWorkitemsPaginated,
  listAllWorkitems,
  getWorkitem,
  parseCustomFieldValues,
  extractWorkitemMeta,
  createWorkitem,
  updateWorkitem,
  batchCreateWorkitems,
  getEffortRecords,
  getEffortRecordsSafe,
  getAllEffortRecords,
};
