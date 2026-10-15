/**
 * 文件：src/pages/team/index.tsx
 * 用途：团队角色管理页（仅 admin）：配置每个用户的角色、团队归属、
 *       登录密码与可用智能体白名单
 *       可见性规则由后端「归属即规则」策略自动判定：
 *       - 管理员创建的知识库/凭证 → 全员只读可见
 *       - 用户自建资源 → 仅自己与管理员可见、可修改
 *       - 智能体全部归属管理员 → 勾选「可用智能体」后该用户才可见
 * 密码：列值留空 = 保持该用户原密码；输入新值 = 重置（保存时后端
 *       自动哈希化，GET 返回恒为空串）
 * 管理判定：经 GET /me 的 role 字段（roles.json 唯一事实源），
 *       不再依赖「用户名 === 'admin'」的名字硬编码（P1 统一）
 * 删除生命周期：保存时后端 diff 出被删用户并清点资源；名下有资源
 *       时返回 409 + 清单，弹窗让管理员三选一（转移/连带删/取消）
 * 改名（P3.5）：用户名旁铅笔按钮 → 后端三步迁移（Redis → 配置 → 磁盘）
 * 未保存保护：行内编辑仅改本地，离开/刷新前弹拦截提示（P0.3）
 * 迁移注：源自 agent-new/src/pages/team/index.tsx，新增密码列与
 *       添加用户的初始密码输入
 */
import {
	Building2,
	ChevronDown,
	Crown,
	KeyRound,
	Pencil,
	Plus,
	Search,
	ShieldCheck,
	Trash2,
	UserRound,
	UsersRound,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import { agentApi } from '@/api/agent';
import { ApiError } from '@/api/client';
import {
	roleApi,
	type RemovalResolution,
	type ResourceInventory,
	type RolesConfig,
	type TeamRole,
} from '@/api/role';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';

type Row = { name: string; role: TeamRole; team: string; agents: string[]; password: string };

/** 用户名白名单：与后端 validate_username / 磁盘净化规则对齐（入口即合法 ⇒ 无路径碰撞） */
const USERNAME_RE = /^[A-Za-z0-9_-]{1,64}$/;

const ROLE_OPTIONS: { value: TeamRole; label: string }[] = [
	{ value: 'member', label: '成员（仅自己）' },
	{ value: 'leader', label: '队长（团队权限暂未启用）' },
	{ value: 'admin', label: '管理员（可见所有）' },
];

/** 角色对应的视觉色（仅用于界面指示） */
const ROLE_META: Record<TeamRole, { label: string; className: string }> = {
	member: { label: '成员', className: 'bg-muted text-muted-foreground' },
	leader: { label: '队长', className: 'bg-amber-500/15 text-amber-600' },
	admin: { label: '管理员', className: 'bg-primary/10 text-primary' },
};

const ROLE_ICON: Record<TeamRole, typeof UserRound> = {
	member: UserRound,
	leader: Crown,
	admin: ShieldCheck,
};

/** 依据用户名稳定取一种底色，用于首字头像 */
const AVATAR_TONES = [
	'bg-blue-500/15 text-blue-600',
	'bg-violet-500/15 text-violet-600',
	'bg-emerald-500/15 text-emerald-600',
	'bg-amber-500/15 text-amber-600',
	'bg-rose-500/15 text-rose-600',
	'bg-cyan-500/15 text-cyan-600',
];

function avatarTone(name: string) {
	let h = 0;
	for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
	return AVATAR_TONES[h % AVATAR_TONES.length];
}

/** 统一提取可读错误信息（409 的结构化 detail 由调用方自行解析） */
function errMsg(e: unknown, fallback: string): string {
	return e instanceof ApiError && e.detail ? e.detail : fallback;
}

/** 多选下拉框（带复选框），用于勾选用户可用的智能体 */
function AgentMultiSelect({
	options,
	value,
	onChange,
	disabled,
	emptyLabel = '未勾选（仅自己创建的可见）',
	emptyOptionsText = '暂无智能体',
}: {
	options: { id: string; name: string }[];
	value: string[];
	onChange: (next: string[]) => void;
	disabled?: boolean;
	emptyLabel?: string;
	emptyOptionsText?: string;
}) {
	const [open, setOpen] = useState(false);
	const ref = useRef<HTMLDivElement>(null);

	useEffect(() => {
		if (!open) return;
		const onDoc = (e: MouseEvent) => {
			if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
		};
		document.addEventListener('mousedown', onDoc);
		return () => document.removeEventListener('mousedown', onDoc);
	}, [open]);

	const label =
		value.length === 0
			? emptyLabel
			: value.length <= 2
				? options
						.filter((o) => value.includes(o.id))
						.map((o) => o.name)
						.join('、') || `${value.length} 个`
				: `已选 ${value.length} 个`;

	const toggle = (id: string) =>
		onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id]);

	return (
		<div className="relative" ref={ref}>
			<button
				type="button"
				disabled={disabled}
				onClick={() => setOpen((o) => !o)}
				title={disabled ? '管理员默认可见所有智能体' : undefined}
				className={cn(
					'flex h-8 w-64 items-center justify-between gap-1 rounded-lg border border-input bg-transparent px-2.5 text-left text-sm whitespace-nowrap transition-colors outline-none select-none',
					'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
					'disabled:cursor-not-allowed disabled:opacity-40',
					'data-open:border-ring data-open:ring-3 data-open:ring-ring/50',
				)}
				data-open={open || undefined}
			>
				<span
					className={cn(
						'line-clamp-1 flex-1',
						value.length === 0 && !disabled && 'text-muted-foreground',
					)}
				>
					{disabled ? '全部可见' : label}
				</span>
				<ChevronDown
					className={cn(
						'size-3.5 shrink-0 text-muted-foreground transition-transform',
						open && 'rotate-180',
					)}
				/>
			</button>
			{open && (
				<div className="absolute z-20 mt-1 max-h-72 w-80 overflow-auto rounded-lg border bg-popover p-1.5 shadow-md ring-1 ring-foreground/10">
					{options.length === 0 && (
						<div className="p-3 text-sm text-muted-foreground">{emptyOptionsText}</div>
					)}
					{options.map((o) => (
						<label
							key={o.id}
							className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-sm hover:bg-accent"
						>
							<input
								type="checkbox"
								checked={value.includes(o.id)}
								onChange={() => toggle(o.id)}
								className="size-4 accent-foreground"
							/>
							<span className="truncate">{o.name}</span>
						</label>
					))}
					<div className="mt-1 flex items-center justify-between border-t pt-1.5">
						<button
							type="button"
							className="rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
							onClick={() => onChange([])}
						>
							清空
						</button>
						<Button size="xs" variant="secondary" onClick={() => setOpen(false)}>
							完成
						</Button>
					</div>
				</div>
			)}
		</div>
	);
}

function StatChip({
	label,
	value,
	icon: Icon,
	className,
}: {
	label: string;
	value: number;
	icon: typeof UserRound;
	className?: string;
}) {
	return (
		<div className="flex items-center gap-2.5 rounded-lg bg-muted px-3 py-2">
			<div className={cn('grid size-7 place-items-center rounded-md', className)}>
				<Icon className="size-4" />
			</div>
			<div className="flex flex-col leading-tight">
				<span className="font-mono text-base text-foreground">{value}</span>
				<span className="text-[10px] text-muted-foreground">{label}</span>
			</div>
		</div>
	);
}

export function TeamPage() {
	// admin 判定统一走 GET /me 的 role 字段（roles.json 唯一事实源）；
	// null = 查询中。第二个 admin 账号同样放行（P1 修复的双标准问题）。
	const [myRole, setMyRole] = useState<TeamRole | null>(null);
	const [rows, setRows] = useState<Row[]>([]);
	const [agentOptions, setAgentOptions] = useState<{ id: string; name: string }[]>([]);
	const [newName, setNewName] = useState('');
	const [newPassword, setNewPassword] = useState('');
	const [filter, setFilter] = useState('');
	const [loading, setLoading] = useState(true);
	const [saving, setSaving] = useState(false);
	// 未保存基线：加载/保存成功后重建；行内编辑只改 rows
	const [baselineRows, setBaselineRows] = useState<Row[]>([]);
	// P3 删除决策流：后端 409 返回的被删用户资源清单
	const [pendingConflict, setPendingConflict] = useState<{
		resources: Record<string, ResourceInventory>;
		workspaceDirs: string[];
	} | null>(null);
	const [pendingResolution, setPendingResolution] = useState<Record<string, RemovalResolution>>({});
	// P3.5 改名对话框
	const [renameTarget, setRenameTarget] = useState<string | null>(null);
	const [renameValue, setRenameValue] = useState('');
	const [renaming, setRenaming] = useState(false);

	const isAdmin = myRole === 'admin';

	useEffect(() => {
		roleApi
			.me()
			.then((m) => setMyRole(m.role))
			.catch(() => setMyRole('member'));
	}, []);

	const applyConfig = (cfg: RolesConfig) => {
		const ua = cfg.user_agents ?? {};
		const mapped: Row[] = Object.entries(cfg.users ?? {}).map(([name, u]) => ({
			name,
			role: u.role,
			team: u.team ?? '',
			agents: ua[name] ?? [],
			// GET 恒返回空串（后端已脱敏），页面以「留空 = 不改」处理
			password: '',
		}));
		setRows(mapped);
		setBaselineRows(mapped);
	};

	const loadAll = () =>
		Promise.all([roleApi.get(), agentApi.list().catch(() => null)])
			.then(([cfg, agentResp]) => {
				applyConfig(cfg);
				setAgentOptions(
					(agentResp?.agents ?? []).map((a) => ({
						id: a.id,
						name: a.data?.name ?? a.id,
					})),
				);
			})
			.catch((e: unknown) => toast.error(errMsg(e, '加载失败')));

	useEffect(() => {
		if (!isAdmin) {
			setLoading(false);
			return;
		}
		loadAll().finally(() => setLoading(false));
	}, [isAdmin]);

	// 未保存变更计数：新增/修改的行 + 被删的行
	const dirtyCount = useMemo(() => {
		const base = new Map(baselineRows.map((b) => [b.name, JSON.stringify(b)]));
		let n = rows.filter((r) => base.get(r.name) !== JSON.stringify(r)).length;
		n += [...base.keys()].filter((k) => !rows.some((r) => r.name === k)).length;
		return n;
	}, [rows, baselineRows]);

	// 刷新/关闭页面前拦截：行内编辑不点「保存更改」不会生效（P0.3）
	useEffect(() => {
		if (dirtyCount === 0) return;
		const onBeforeUnload = (e: BeforeUnloadEvent) => {
			e.preventDefault();
			e.returnValue = '';
		};
		window.addEventListener('beforeunload', onBeforeUnload);
		return () => window.removeEventListener('beforeunload', onBeforeUnload);
	}, [dirtyCount]);

	if (myRole === null) {
		return (
			<div className="h-full overflow-auto p-2">
				<div className="flex h-full min-h-0 flex-col gap-3 rounded-[22px] bg-card p-6 shadow-panel">
					{Array.from({ length: 4 }).map((_, i) => (
						<Skeleton key={i} className="h-12 rounded-lg" />
					))}
				</div>
			</div>
		);
	}

	if (!isAdmin) {
		return (
			<div className="h-full overflow-auto p-2">
				<div className="flex h-full min-h-0 items-center justify-center rounded-[22px] bg-card shadow-panel">
					<Empty>
						<EmptyHeader>
							<EmptyTitle>本页仅管理员可用</EmptyTitle>
							<EmptyDescription>
								请使用管理员账号登录后，即可在此管理团队成员的角色与权限。
							</EmptyDescription>
						</EmptyHeader>
					</Empty>
				</div>
			</div>
		);
	}

	const update = (name: string, patch: Partial<Row>) =>
		setRows((rs) => rs.map((r) => (r.name === name ? { ...r, ...patch } : r)));

	const remove = (name: string) => {
		if (name === 'admin') {
			toast.error('管理员账号不可删除');
			return;
		}
		setRows((rs) => rs.filter((r) => r.name !== name));
	};

	const newNameInvalid = newName.trim().length > 0 && !USERNAME_RE.test(newName.trim());

	const add = () => {
		const name = newName.trim();
		if (!name) return;
		// 前端即时白名单校验（后端保存入口还会再拦一道，422）
		if (!USERNAME_RE.test(name)) {
			toast.error('用户名仅允许字母、数字、下划线、横杠，长度 1-64');
			return;
		}
		if (!newPassword.trim()) {
			toast.error('新用户必须设置初始密码');
			return;
		}
		if (rows.some((r) => r.name === name)) {
			toast.error('该用户名已存在');
			return;
		}
		setRows((rs) => [
			...rs,
			{ name, role: 'member', team: '', agents: [], password: newPassword.trim() },
		]);
		setNewName('');
		setNewPassword('');
		setFilter('');
	};

	const buildSaveBody = (current: RolesConfig, resolution?: Record<string, RemovalResolution>) => {
		const users: Record<string, { role: TeamRole; team?: string; password?: string }> = {};
		const userAgents: Record<string, string[]> = {};
		for (const r of rows) {
			users[r.name] = { role: r.role, password: r.password };
			if (r.team) users[r.name].team = r.team;
			// admin 恒有全部权限，不参与智能体白名单
			if (r.name !== 'admin') userAgents[r.name] = [...r.agents];
		}
		if (!users.admin) users.admin = { role: 'admin' };
		return {
			...current,
			admin_edit: current.admin_edit ?? true,
			users,
			user_agents: userAgents,
			...(resolution ? { resolution } : {}),
		} as RolesConfig;
	};

	const doSave = async (resolution?: Record<string, RemovalResolution>) => {
		setSaving(true);
		try {
			// 重新拉取最新配置做合并保存，避免丢失页面未展示的扩展键
			// （旧版共享/ACL 废弃键由后端保存接口自动清洗）
			const current = await roleApi.get().catch(() => ({}) as RolesConfig);
			const result = await roleApi.save(buildSaveBody(current, resolution));
			// 保存成功后清空本地密码输入（下次进入亦为空串）并重建未保存基线
			setRows((rs) => {
				const next = rs.map((r) => ({ ...r, password: '' }));
				setBaselineRows(next);
				return next;
			});
			const moved = result?.transferred ?? {};
			const movedSummary = Object.entries(moved)
				.map(([u, c]) => `${u}(agent ${c.agents ?? 0}/KB ${c.knowledge_bases ?? 0}/凭证 ${c.credentials ?? 0})`)
				.join('、');
			if (movedSummary) toast.success(`资源已转移给 admin：${movedSummary}`);
			if (result?.archived_dirs?.length) {
				toast.info(
					`以下目录不会自动删除，请管理员手动归档（可用 scripts/archive_user_workspace.py）：${result.archived_dirs.join('、')}`,
				);
			}
			toast.success('已保存，约 5 秒内生效');
			setPendingConflict(null);
		} catch (e) {
			if (e instanceof ApiError && e.status === 409) {
				// 删除用户生命周期：名下有资源 → 后端拒绝静默保存，
				// 弹窗让管理员对每个用户选择处置方式
				try {
					const detail = JSON.parse(e.detail) as {
						resources?: Record<string, ResourceInventory>;
						workspace_dirs?: string[];
					};
					if (detail.resources) {
						setPendingConflict({
							resources: detail.resources,
							workspaceDirs: detail.workspace_dirs ?? [],
						});
						const defaults: Record<string, RemovalResolution> = {};
						for (const u of Object.keys(detail.resources)) defaults[u] = 'transfer';
						setPendingResolution(defaults);
						return;
					}
				} catch {
					// detail 非结构化，按普通错误提示
				}
			}
			toast.error(errMsg(e, '保存失败'));
		} finally {
			setSaving(false);
		}
	};

	const save = () => doSave();

	const confirmRename = async () => {
		if (!renameTarget) return;
		const to = renameValue.trim();
		if (!USERNAME_RE.test(to)) {
			toast.error('新用户名仅允许字母、数字、下划线、横杠，长度 1-64');
			return;
		}
		if (to === renameTarget) {
			toast.error('新用户名与原名相同');
			return;
		}
		if (rows.some((r) => r.name === to)) {
			toast.error('该用户名已存在');
			return;
		}
		if (dirtyCount > 0) {
			toast.error('存在未保存的修改，请先保存或刷新页面后再改名');
			return;
		}
		setRenaming(true);
		try {
			const result = await roleApi.rename(renameTarget, to);
			const errs = result?.disk_errors ?? [];
			if (errs.length) {
				toast.warning(`改名已生效，但部分磁盘目录未能改名（可重试或人工归档）：${errs.join('、')}`);
			} else {
				toast.success(`已改名 ${renameTarget} → ${to}，约 5 秒内生效`);
			}
			setRenameTarget(null);
			setRenameValue('');
			await loadAll();
		} catch (e) {
			toast.error(errMsg(e, '改名失败'));
		} finally {
			setRenaming(false);
		}
	};

	const q = filter.trim().toLowerCase();
	const filtered = q
		? rows.filter(
				(r) =>
					r.name.toLowerCase().includes(q) ||
					r.team.toLowerCase().includes(q) ||
					ROLE_OPTIONS.find((o) => o.value === r.role)?.label.toLowerCase().includes(q),
			)
		: rows;

	const teamNames = [...new Set(rows.map((r) => r.team).filter(Boolean))];

	return (
		<div className="h-full min-h-0 overflow-hidden p-2">
			<div className="flex h-full min-h-0 flex-col rounded-[22px] bg-card shadow-panel">
				{/* ── 头部：标题 + 统计 + 添加用户 ─────────────────────────── */}
				<div className="flex items-start justify-between gap-4 px-6 pt-5 pb-4">
					<div className="flex min-w-0 items-start gap-3">
						<div className="mt-0.5 grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
							<UsersRound className="size-5" />
						</div>
						<div className="min-w-0">
							<div className="flex items-center gap-2">
								<h1 className="text-lg font-medium tracking-[-0.015em] text-foreground">
									团队角色管理
								</h1>
								<Badge
									variant="secondary"
									className="gap-1 rounded-md px-1.5 py-0 text-[10px]"
								>
									<ShieldCheck className="size-3" />
									仅管理员
								</Badge>
								{dirtyCount > 0 && (
									<Badge
										variant="outline"
										className="gap-1 rounded-md border-amber-500/40 px-1.5 py-0 text-[10px] text-amber-600"
									>
										{dirtyCount} 项未保存
									</Badge>
								)}
							</div>
							<p className="mt-0.5 text-xs leading-5 text-muted-foreground">
								管理员创建的知识库与凭证全员只读可见；用户自建资源仅自己与管理员可见、可修改；
								智能体全部归属管理员，勾选「可用智能体」后该用户才可见。团队权限暂未启用。保存后约 5 秒生效。
							</p>
						</div>
					</div>
					<div className="flex shrink-0 items-center gap-2">
						<div className="flex flex-col gap-0.5">
							<Input
								className={cn(
									'h-8 w-40 rounded-lg',
									newNameInvalid && 'border-destructive focus-visible:ring-destructive/30',
								)}
								placeholder="用户名"
								value={newName}
								onChange={(e) => setNewName(e.target.value)}
								onKeyDown={(e) => e.key === 'Enter' && add()}
							/>
							{newNameInvalid && (
								<span className="text-[10px] text-destructive">
									仅允许字母/数字/下划线/横杠，1-64 位
								</span>
							)}
						</div>
						<Input
							className="h-8 w-40 rounded-lg"
							type="password"
							placeholder="初始密码（必填）"
							value={newPassword}
							onChange={(e) => setNewPassword(e.target.value)}
							onKeyDown={(e) => e.key === 'Enter' && add()}
						/>
						<Button variant="secondary" onClick={add}>
							<Plus />
							添加用户
						</Button>
					</div>
				</div>

				{/* ── 概览统计 ─────────────────────────────────────────── */}
				<div className="grid grid-cols-2 gap-2 px-6 pb-4 sm:grid-cols-4">
					<StatChip
						label="用户总数"
						value={rows.length}
						icon={UsersRound}
						className="bg-primary/10 text-primary"
					/>
					<StatChip
						label="管理员"
						value={rows.filter((r) => r.role === 'admin').length}
						icon={ShieldCheck}
						className="bg-primary/10 text-primary"
					/>
					<StatChip
						label="队长"
						value={rows.filter((r) => r.role === 'leader').length}
						icon={Crown}
						className="bg-amber-500/15 text-amber-600"
					/>
					<StatChip
						label="团队分组"
						value={teamNames.length}
						icon={Building2}
						className="bg-violet-500/15 text-violet-600"
					/>
				</div>

				<Separator />

				{loading ? (
					<div className="flex flex-col gap-3 p-6">
						{Array.from({ length: 4 }).map((_, i) => (
							<Skeleton key={i} className="h-12 rounded-lg" />
						))}
					</div>
				) : (
					<>
						{/* ── 列表工具条 ─────────────────────────────── */}
						<div className="flex shrink-0 items-center justify-between gap-3 px-6 py-3">
							<div className="flex items-center gap-2">
								<span className="text-sm font-medium text-foreground">用户列表</span>
								<span className="rounded-md bg-surface-muted px-1.5 py-0.5 font-mono text-[11px] text-text-data">
									{filtered.length}/{rows.length}
								</span>
								{filter && (
									<button
										type="button"
										className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
										onClick={() => setFilter('')}
									>
										清除筛选
									</button>
								)}
							</div>
							<div className="relative">
								<Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
								<Input
									className="h-8 w-52 rounded-lg pl-8"
									placeholder="搜索用户名 / 团队 / 角色"
									value={filter}
									onChange={(e) => setFilter(e.target.value)}
								/>
							</div>
						</div>

						{/* 用户表格：占满剩余高度，内部滚动，表头吸顶 */}
						<div className="min-h-0 flex-1 overflow-y-auto px-3 pb-1">
							<div className="overflow-hidden rounded-xl border border-border">
								<Table className="min-w-[980px]">
									<TableHeader>
										<TableRow className="border-border bg-muted hover:bg-transparent">
											<TableHead className="sticky top-0 z-10 h-9 w-8 bg-muted py-0 pl-4" />
											<TableHead className="sticky top-0 z-10 h-9 bg-muted px-3 py-0 font-mono text-[10px] font-medium tracking-[0.12em] text-text-tertiary uppercase">
												用户名
											</TableHead>
											<TableHead className="sticky top-0 z-10 h-9 w-48 bg-muted px-3 py-0 font-mono text-[10px] font-medium tracking-[0.12em] text-text-tertiary uppercase">
												角色
											</TableHead>
											<TableHead className="sticky top-0 z-10 h-9 w-44 bg-muted px-3 py-0 font-mono text-[10px] font-medium tracking-[0.12em] text-text-tertiary uppercase">
												团队
											</TableHead>
											<TableHead className="sticky top-0 z-10 h-9 w-40 bg-muted px-3 py-0 font-mono text-[10px] font-medium tracking-[0.12em] text-text-tertiary uppercase">
												密码
											</TableHead>
											<TableHead className="sticky top-0 z-10 h-9 bg-muted px-3 py-0 font-mono text-[10px] font-medium tracking-[0.12em] text-text-tertiary uppercase">
												可用智能体
											</TableHead>
											<TableHead className="sticky top-0 z-10 h-9 w-10 bg-muted py-0 pr-3 text-right" />
										</TableRow>
									</TableHeader>
									<TableBody>
										{filtered.map((r) => {
											const Icon = ROLE_ICON[r.role];
											const isAdminRow = r.name === 'admin';
											return (
												<TableRow
													key={r.name}
													className="border-border group/row transition-colors hover:bg-row-hover"
												>
													<TableCell className="py-0 pl-4 pr-0">
														<div className="grid size-8 place-items-center rounded-full">
															<span
																className={cn(
																	'grid size-8 place-items-center rounded-full text-xs font-medium',
																	avatarTone(r.name),
																)}
															>
																{r.name.slice(0, 1).toUpperCase()}
															</span>
														</div>
													</TableCell>
													<TableCell className="px-3 py-2.5">
														<div className="flex items-center gap-2">
															<span
																className={cn(
																	'inline-flex size-4 shrink-0 items-center justify-center rounded',
																	'opacity-60 transition-opacity',
																)}
															>
																<Icon className="size-3.5" />
															</span>
															<span className="min-w-0 truncate text-sm text-foreground">
																{r.name}
															</span>
															{isAdminRow && (
																<Badge
																	variant="outline"
																	className="rounded-md px-1.5 py-0 text-[10px]"
																>
																	超级管理员
																</Badge>
															)}
															{!isAdminRow && (
																<button
																	type="button"
																	className="rounded p-0.5 text-muted-foreground opacity-0 transition-opacity group-hover/row:opacity-100 hover:text-foreground"
																	title={`修改用户名（资源与数据随迁）`}
																	onClick={() => {
																		setRenameTarget(r.name);
																		setRenameValue(r.name);
																	}}
																>
																	<Pencil className="size-3.5" />
																</button>
															)}
														</div>
													</TableCell>
													<TableCell className="px-3 py-2.5">
														<Select
															value={r.role}
															onValueChange={(v) =>
																update(r.name, { role: v as TeamRole })
															}
														>
															<SelectTrigger className="w-44 justify-between gap-2 rounded-lg bg-transparent">
																<SelectValue />
															</SelectTrigger>
															<SelectContent align="start">
																{ROLE_OPTIONS.map((o) => (
																	<SelectItem
																		key={o.value}
																		value={o.value}
																	>
																		{o.label}
																	</SelectItem>
																))}
															</SelectContent>
														</Select>
													</TableCell>
													<TableCell className="px-3 py-2.5">
														<Input
															className="h-8 w-40 rounded-lg"
															placeholder="—"
															value={r.team}
															disabled={r.role === 'admin'}
															onChange={(e) =>
																update(r.name, { team: e.target.value })
															}
														/>
													</TableCell>
													<TableCell className="px-3 py-2.5">
														<div className="flex items-center gap-1.5">
															<KeyRound className="size-3.5 shrink-0 text-muted-foreground" />
															<Input
																className="h-8 w-32 rounded-lg"
																type="password"
																autoComplete="new-password"
																placeholder="留空 = 不修改"
																value={r.password}
																onChange={(e) =>
																	update(r.name, {
																		password: e.target.value,
																	})
																}
															/>
														</div>
													</TableCell>
													<TableCell className="px-3 py-2.5">
														<AgentMultiSelect
															options={agentOptions}
															value={r.agents}
															disabled={r.role === 'admin'}
															onChange={(next) =>
																update(r.name, { agents: next })
															}
														/>
													</TableCell>
													<TableCell className="py-0 pr-3 text-right">
														<Button
															size="icon-sm"
															variant="ghost"
															className="text-muted-foreground opacity-60 transition-opacity hover:text-destructive hover:opacity-100 group-hover/row:opacity-100 data-[disabled]:opacity-30"
															disabled={isAdminRow}
															tooltip={
																isAdminRow
																	? '管理员账号不可删除'
																	: `删除 ${r.name}`
															}
															onClick={() => remove(r.name)}
														>
															<Trash2 />
														</Button>
													</TableCell>
												</TableRow>
											);
										})}
										{filtered.length === 0 && (
											<TableRow className="border-border hover:bg-transparent">
												<TableCell
													colSpan={7}
													className="px-4 py-10 text-center text-sm text-muted-foreground"
												>
													{rows.length === 0
														? '还没有用户，使用右上角输入框添加第一个用户。'
														: '没有匹配的用户。'}
												</TableCell>
											</TableRow>
										)}
									</TableBody>
								</Table>
							</div>
						</div>

						{/* ── 底部：角色图例 + 保存 ─────────────────────── */}
						<div className="flex shrink-0 items-center justify-between gap-3 border-t border-border px-6 py-3">
							<div className="flex items-center gap-4">
								{ROLE_OPTIONS.map((o) => {
									const meta = ROLE_META[o.value];
									const Icon = ROLE_ICON[o.value];
									return (
										<div
											key={o.value}
											className="flex items-center gap-1.5 text-xs text-muted-foreground"
										>
											<span
												className={cn(
													'inline-flex size-3.5 items-center justify-center rounded',
													meta.className,
												)}
											>
												<Icon className="size-2.5" />
											</span>
											{meta.label}
										</div>
									);
								})}
							</div>
							<Button onClick={save} disabled={saving} size="lg">
								{saving ? '保存中…' : dirtyCount > 0 ? `保存更改（${dirtyCount} 项）` : '保存更改'}
							</Button>
						</div>
					</>
				)}
			</div>

			{/* ── 删除用户决策对话框（P3：409 资源清单 → 三选一） ─────── */}
			{pendingConflict && (
				<div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4">
					<div className="w-full max-w-lg rounded-2xl bg-card p-5 shadow-panel">
						<h2 className="text-base font-medium text-foreground">删除的用户名下仍有资源</h2>
						<p className="mt-1 text-xs leading-5 text-muted-foreground">
							为避免产生谁都不可见的「幻影资源」，请为每个用户选择处置方式：
							「转移给 admin」后按归属即规则生效（智能体按白名单可见，知识库/凭证全员只读）；
							「连带删除」不可恢复。
						</p>
						<div className="mt-3 flex max-h-56 flex-col gap-2 overflow-y-auto">
							{Object.entries(pendingConflict.resources).map(([user, inv]) => (
								<div key={user} className="rounded-lg border border-border p-2.5">
									<div className="flex items-center justify-between gap-2">
										<span className="text-sm font-medium text-foreground">{user}</span>
										<Select
											value={pendingResolution[user] ?? 'transfer'}
											onValueChange={(v) =>
												setPendingResolution((rs) => ({
													...rs,
													[user]: v as RemovalResolution,
												}))
											}
										>
											<SelectTrigger className="h-7 w-44 justify-between gap-2 rounded-lg">
												<SelectValue />
											</SelectTrigger>
											<SelectContent align="end">
												<SelectItem value="transfer">转移给 admin（推荐）</SelectItem>
												<SelectItem value="delete">连带删除资源（不可恢复）</SelectItem>
											</SelectContent>
										</Select>
									</div>
									<div className="mt-1.5 text-xs text-muted-foreground">
										智能体 {inv.agents?.length ?? 0} 个
										{(inv.agents ?? []).length > 0 && `：${inv.agents.map((a) => a.name).join('、')}`}；
										知识库 {inv.knowledge_bases?.length ?? 0} 个；凭证 {inv.credentials?.length ?? 0} 个
									</div>
								</div>
							))}
						</div>
						{pendingConflict.workspaceDirs.length > 0 && (
							<p className="mt-2 text-[10px] leading-4 text-muted-foreground">
								磁盘工作目录不会自动删除，保存后请归档：{pendingConflict.workspaceDirs.join('、')}
							</p>
						)}
						<div className="mt-4 flex items-center justify-end gap-2">
							<Button
								variant="ghost"
								onClick={() => {
									setPendingConflict(null);
									setPendingResolution({});
								}}
							>
								取消本次保存
							</Button>
							<Button onClick={() => doSave(pendingResolution)} disabled={saving}>
								{saving ? '处理中…' : '确认并保存'}
							</Button>
						</div>
					</div>
				</div>
			)}

			{/* ── 改名对话框（id 换键模型：仅改登录名，资源零迁移） ──────── */}
			{renameTarget !== null && (
				<div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4">
					<div className="w-full max-w-sm rounded-2xl bg-card p-5 shadow-panel">
						<h2 className="text-base font-medium text-foreground">修改用户名</h2>
						<p className="mt-1 text-xs leading-5 text-muted-foreground">
							将「{renameTarget}」改名。其智能体授权、知识库、凭证与磁盘数据均以固定
							ID 归属，改名只影响登录名本身，数据零迁移、密码保持不变；改名后旧用户名立即失效。
						</p>
						<Input
							className="mt-3 h-9 w-full rounded-lg"
							placeholder="新用户名（字母/数字/下划线/横杠）"
							value={renameValue}
							autoFocus
							onChange={(e) => setRenameValue(e.target.value)}
							onKeyDown={(e) => e.key === 'Enter' && confirmRename()}
						/>
						{renameValue.trim().length > 0 && !USERNAME_RE.test(renameValue.trim()) && (
							<span className="mt-1 block text-[10px] text-destructive">
								仅允许字母/数字/下划线/横杠，1-64 位
							</span>
						)}
						<div className="mt-4 flex items-center justify-end gap-2">
							<Button
								variant="ghost"
								onClick={() => {
									setRenameTarget(null);
									setRenameValue('');
								}}
							>
								取消
							</Button>
							<Button onClick={confirmRename} disabled={renaming}>
								{renaming ? '改名中…' : '确认改名'}
							</Button>
						</div>
					</div>
				</div>
			)}
		</div>
	);
}
