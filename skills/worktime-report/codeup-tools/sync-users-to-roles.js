/**
 * 云效用户 → AgentScope 平台账号同步
 *
 * 用法（agentrunner 或有 apps 写权限的账户执行）:
 *   node sync-users-to-roles.js
 *
 * 行为:
 *   1. 从云效拉取全部项目成员（去重）
 *   2. 合并进 roles.json：不存在用户名的加 {role:"member", team:"gas"}
 *   3. 已存在的用户（admin/agentrunner/qtt 及历史同步过的）不改动
 *
 * roles.json 为热读取（约5秒生效），改完无需重启后端。
 * 可选: ROLES_PATH 环境变量覆盖目标文件（测试用）
 */
const fs = require('fs');
const { searchProjects, getProjectMembers } = require('./shared/yunxiao-client');

const ROLES_PATH = process.env.ROLES_PATH || '/home/agentrunner/apps/roles.json';
const DEFAULT_TEAM = process.env.DEFAULT_TEAM || 'gas';
const DEFAULT_ROLE = 'member';

// 排除名单（与工时统计共用，可选）
// 中文名 -> 拼音缩写 账号映射（roles.json 以拼音缩写为账号名）
const NAME_MAP = (() => {
  try {
    return require('./data/name-map.json');
  } catch (e) {
    return {};
  }
})();

const EXCLUDE = (() => {
  try {
    const v = require('./data/exclude-names.json');
    return Array.isArray(v) ? v : [];
  } catch (e) {
    return [];
  }
})();

(async () => {
  // 1. 拉取云效全部项目成员，按用户名去重
  const projects = await searchProjects();
  const users = new Map(); // userName -> userId
  for (const p of projects) {
    const members = await getProjectMembers(p.id).catch(() => []);
    for (const m of members) {
      if (!m.userName || EXCLUDE.includes(m.userName)) continue;
      if (!users.has(m.userName)) users.set(m.userName, m.userId);
    }
  }
  console.log(`云效项目成员共 ${users.size} 人（去重后）`);

  // 2. 合并进 roles.json（已存在的不动）
  const roles = JSON.parse(fs.readFileSync(ROLES_PATH, 'utf-8'));
  roles.users = roles.users || {};

  const added = [];
  for (const [name, userId] of users) {
    const id = NAME_MAP[name] || name;
    if (roles.users[id]) continue;
    roles.users[id] = { role: DEFAULT_ROLE, team: DEFAULT_TEAM };
    added.push(name + ' -> ' + id);
  }

  // 3. 原子写入（先备份再替换）
  fs.copyFileSync(ROLES_PATH, ROLES_PATH + '.bak');
  const tmp = ROLES_PATH + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(roles, null, 2) + '\n', 'utf-8');
  fs.renameSync(tmp, ROLES_PATH);

  console.log(`新增账号 ${added.length} 个: ${added.join('、') || '（无）'}`);
  console.log(`角色: ${DEFAULT_ROLE}，团队: ${DEFAULT_TEAM}`);
  console.log(`已写入 ${ROLES_PATH}（原文件备份为 .bak）`);
  console.log('roles.json 热加载约 5 秒生效，刷新 /team 页面即可看到全部成员。');
})().catch((e) => {
  console.error('同步失败:', e.message || e);
  process.exit(1);
});
