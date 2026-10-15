/**
 * 项目元数据配置
 *
 * 集中维护各项目的 ID、工作项类型、迭代等元信息，避免每次查询 API。
 */

module.exports = {
  // 项目列表
  projects: {
    cctef: {
      name: '沈阳交通执法项目',
      id: process.env.YUNXIAO_PROJECT_ID_CCTEF || '8c294e75540d4d4c115b8883de',
      workitemTypes: {
        Req: { id: '9uy29901re573f561d69jn40', name: '产品类需求' },
        Task: { id: 'ba102e46bc6a8483d9b7f25c', name: '任务' },
        Bug: { id: '37da3a07df4d08aef2e3b393', name: '缺陷' },
      },
      iterations: {
        initial: { id: 'd59197c67cfd090c1bc7e8cd0d', name: '初始研发迭代' },
      },
      knownParentReqs: {
        '勤务调度': '8f7fafda685e746eacffeddabc',
        '执法监督与评议考核': 'abde22ca817c7eb7438720bcbf',
        '行政执法协同办案': '8bf5cce73ae1649fe91af6bbc2',
      },
    },
    ccull: {
      name: '本溪燃气监测管理平台',
      id: process.env.YUNXIAO_PROJECT_ID_CCULL || '99d39d7071bbd917cab2d52e59',
      workitemTypes: {},  // 待按需补充
      iterations: {},
      knownParentReqs: {},
    },
    ccull_ln: {
      name: '辽宁省燃气平台',
      id: process.env.YUNXIAO_PROJECT_ID_CCULL_LN || 'f9fe27f37fc55bbefe836b7a0f',
      workitemTypes: {},
      iterations: {},
      knownParentReqs: {},
    },
  },

  // 计划时间字段 ID（更新用）
  fieldIds: {
    planStart: '79',   // 计划开始时间
    planEnd: '80',     // 计划完成时间
  },
};
