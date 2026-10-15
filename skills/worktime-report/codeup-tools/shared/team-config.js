/**
 * 团队人员配置
 *
 * 维护各角色与研发组的人员映射关系。
 * 功能设计负责人由需求动态决定（取子需求的 assignedTo），其他角色固定。
 */

// 角色 → 固定负责人
const ROLE_OWNERS = {
  uiDesign: {
    name: '王硕',
    userId: '69cb318ba407dfca29a78ed6',
  },
  test: {
    name: '李月',
    userId: '6a41d93fbdf2b6df65624a1b',
  },
};

// 研发分组
const DEV_GROUPS = {
  group1: {
    label: '1组',
    backend: {
      owner: { name: '李潇', userId: '696856310ef220c2c0cc992d' },
      members: [
        { name: '崔海达', userId: '69685520d2a2c377c52d6fe9' },
      ],
    },
    frontend: {
      owner: { name: '郝立雨', userId: '69685c0286cfbe09072e0f1c' },
    },
  },
  group2: {
    label: '2组',
    backend: {
      owner: { name: '郭庆富', userId: '6a45d459140ca633d33f6a74' },
      members: [],
    },
    frontend: {
      owner: { name: '贾超卫', userId: '628f489f2c14288da09508f8' },
    },
  },
};

/**
 * 任务类型定义：每个子需求需创建的 5 类子任务
 * 返回 [{ subject, category, assignedTo, role }]
 *
 * @param {object} parentReq - 子需求对象（含 subject, assignedTo）
 * @param {string} groupKey - 研发组 key: 'group1' | 'group2'
 */
function buildTaskTemplates(parentReq, groupKey) {
  const group = DEV_GROUPS[groupKey];
  if (!group) throw new Error(`未知研发组: ${groupKey}，可选: group1, group2`);

  const designOwner = typeof parentReq.assignedTo === 'object'
    ? parentReq.assignedTo.id
    : parentReq.assignedTo || '';

  const prefix = parentReq.subject || '';

  return [
    {
      subject: `【功能设计】${prefix}`,
      category: 'Task',
      assignedTo: designOwner,
      roleLabel: '功能设计',
    },
    {
      subject: `【UI设计】${prefix}`,
      category: 'Task',
      assignedTo: ROLE_OWNERS.uiDesign.userId,
      roleLabel: 'UI设计',
    },
    {
      subject: `【后端开发】${prefix}`,
      category: 'Task',
      assignedTo: group.backend.owner.userId,
      roleLabel: '后端开发',
    },
    {
      subject: `【前端开发】${prefix}`,
      category: 'Task',
      assignedTo: group.frontend.owner.userId,
      roleLabel: '前端开发',
    },
    {
      subject: `【测试回归】${prefix}`,
      category: 'Task',
      assignedTo: ROLE_OWNERS.test.userId,
      roleLabel: '测试回归',
    },
  ];
}

module.exports = { ROLE_OWNERS, DEV_GROUPS, buildTaskTemplates };
