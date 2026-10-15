const path = require('path');
require('dotenv').config({ path: path.resolve(__dirname, '..', '.env') });

const required = [
  'YUNXIAO_TOKEN',
  'YUNXIAO_DOMAIN',
  'YUNXIAO_ORGANIZATION_ID',
];

const missing = required.filter((key) => !process.env[key]);
if (missing.length) {
  console.error('缺少必要的环境变量:', missing.join(', '));
  console.error('请复制 .env.example 为 .env 并填写配置');
  process.exit(1);
}

module.exports = {
  token: process.env.YUNXIAO_TOKEN,
  domain: process.env.YUNXIAO_DOMAIN,
  organizationId: process.env.YUNXIAO_ORGANIZATION_ID,
  projectId: process.env.YUNXIAO_PROJECT_ID || '',
  projectIdCctef: process.env.YUNXIAO_PROJECT_ID_CCTEF || '',
  qwenApiKey: process.env.QWEN_API_KEY || '',
};
