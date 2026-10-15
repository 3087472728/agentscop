/**
 * 文件：src/api/role.ts
 * 用途：API 客户端：团队角色管理（仅管理员）
 *       可见性规则由后端「归属即规则」策略自动判定，此处只管理
 *       用户角色/团队 与 可用智能体白名单
 * 密码：GET /roles 返回的 password 恒为空串（哈希不出网）；
 *       POST 时 password 传明文（后端自动哈希化），
 *       传空串/缺省 = 保持该用户原有密码不变
 * 迁移注：源自 agent-new/src/api/role.ts，扩展 password 字段；
 *       新增 GET /me（admin 判定统一数据源）、rename（P3.5）、
 *       resolution（P3 删除用户 409 决策流的处置回传）
 */
import { client } from './client';

export type TeamRole = 'admin' | 'leader' | 'member';

export interface TeamUser {
	role: TeamRole;
	team?: string;
	/** GET 返回空串；POST 传明文（后端哈希化）或空串（保持不变） */
	password?: string;
}

export interface RolesConfig {
	admin_edit?: boolean;
	users: Record<string, TeamUser>;
	/** 每用户可用智能体白名单：{ "<user_id>": ["<agent_id>", ...] }（严格模式，勾选才可见） */
	user_agents?: Record<string, string[]>;
	/** 保留后端/其他版本的扩展键，避免保存时丢失 */
	[key: string]: unknown;
}

/** 被删用户的资源清单（后端 409 决策流的 detail.resources） */
export interface ResourceInventory {
	agents: { id: string; name: string }[];
	knowledge_bases: { id: string; name: string }[];
	credentials: { id: string; name: string }[];
}

/** 被删用户资源处置方式：transfer=转移给 admin / delete=连带删除 */
export type RemovalResolution = 'transfer' | 'delete';

export interface SaveRolesResult {
	ok: boolean;
	/** 各用户转移给 admin 的资源计数（P3 生命周期） */
	transferred?: Record<string, Record<string, number>>;
	/** 被删用户的磁盘工作目录清单（程序不自动删，提示管理员归档） */
	archived_dirs?: string[];
}

export interface RenameUserResult {
	ok: boolean;
	/** Redis owner 迁移计数：{agents, knowledge_bases, credentials} */
	transferred?: Record<string, number>;
	/** 已改名的磁盘工作目录 */
	disk_renamed?: string[];
	/** 磁盘改名失败项（身份切换不受影响，可重试/人工归档） */
	disk_errors?: string[];
}

export const roleApi = {
	get: () => client.get<RolesConfig>('/roles'),
	save: (body: RolesConfig) => client.post<SaveRolesResult>('/roles', body),
	/** 当前登录者身份与角色——前端 admin 判定的唯一数据源（P1 统一） */
	me: () => client.get<{ user_id: string; role: TeamRole }>('/me'),
	/** 用户改名（P3.5）：Redis owner → roles.json → 磁盘目录 三步迁移 */
	rename: (from: string, to: string) =>
		client.post<RenameUserResult>('/roles/users/rename', { from, to }),
};
