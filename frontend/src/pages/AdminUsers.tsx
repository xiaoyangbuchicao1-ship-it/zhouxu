import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { ArrowLeft, Check, KeyRound, LoaderCircle, RefreshCw, Search, Shield, SlidersHorizontal, Trash2, UserRound, X, Activity, ScrollText, Gauge } from "lucide-react";
import { useAuthSession } from "@/lib/auth-session";
import { AdminApiError, deleteAdminUser, listAdminAuditLogs, listAdminUsageLogs, listAdminUserActivity, listAdminUsers, resetAdminUserPassword, updateAdminUser, type AdminAccount, type AdminAuditLog, type AdminUsageLog, type AdminUserActivity } from "@/lib/admin-api";
import "./admin-users.css";

const number = new Intl.NumberFormat("zh-CN");

function formatTokens(value: number) {
  return number.format(Math.max(0, value));
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function UsageBar({ user }: { user: AdminAccount }) {
  const percentage = user.quota_tokens > 0 ? Math.min(100, (user.used_tokens / user.quota_tokens) * 100) : 0;
  return (
    <div className="admin-usage-cell">
      <div className="admin-usage-label"><span>{formatTokens(user.used_tokens)} 已使用</span><span>{formatTokens(user.remaining_tokens)} 剩余</span></div>
      <div className="admin-progress" aria-label={`已使用 ${Math.round(percentage)}%`}><span style={{ width: `${percentage}%` }} /></div>
      <small>额度 {formatTokens(user.quota_tokens)} tokens</small>
    </div>
  );
}

export default function AdminUsers() {
  const session = useAuthSession();
  const navigate = useNavigate();
  const [users, setUsers] = useState<AdminAccount[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<AdminAccount | null>(null);
  const [passwordUser, setPasswordUser] = useState<AdminAccount | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [quota, setQuota] = useState("");
  const [used, setUsed] = useState("");
  const [role, setRole] = useState<"user" | "admin">("user");
  const [activeTab, setActiveTab] = useState<"users" | "activity" | "usage" | "audit">("users");
  const [logSearch, setLogSearch] = useState("");
  const [logCategory, setLogCategory] = useState("all");
  const [logLoading, setLogLoading] = useState(false);
  const [usageLogs, setUsageLogs] = useState<AdminUsageLog[]>([]);
  const [auditLogs, setAuditLogs] = useState<AdminAuditLog[]>([]);
  const [activityLogs, setActivityLogs] = useState<AdminUserActivity[]>([]);
  const [activityUserId, setActivityUserId] = useState("");

  const load = useCallback(async () => {
    if (!session) return;
    setLoading(true);
    setError("");
    try {
      const result = await listAdminUsers(session.session.token, { search, status });
      setUsers(result.accounts);
      setTotal(result.total);
    } catch (value) {
      const apiError = value as AdminApiError;
      if (apiError.status === 401) {
        navigate("/login", { replace: true, state: { notice: "登录状态已失效，请重新登录。" } });
        return;
      }
      setError(apiError.code === "admin_required" ? "当前账户没有管理员权限。" : "用户数据暂时无法加载，请稍后重试。 ");
    } finally {
      setLoading(false);
    }
  }, [navigate, search, session, status]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!activityUserId && users[0]) setActivityUserId(users[0].id);
  }, [activityUserId, users]);
  const loadLogs = useCallback(async () => {
    if (!session || activeTab === "users") return;
    setLogLoading(true);
    try {
      if (activeTab === "usage") {
        const result = await listAdminUsageLogs(session.session.token, { search: logSearch });
        setUsageLogs(result.items);
      } else if (activeTab === "audit") {
        const result = await listAdminAuditLogs(session.session.token, { search: logSearch, category: logCategory });
        setAuditLogs(result.items);
      } else if (activityUserId) {
        const result = await listAdminUserActivity(session.session.token, activityUserId);
        setActivityLogs(result.items);
      }
    } catch (value) {
      const apiError = value as AdminApiError;
      if (apiError.status === 401) navigate("/login", { replace: true, state: { notice: "登录状态已失效，请重新登录。" } });
      else setError("日志暂时无法加载，请稍后重试。");
    } finally { setLogLoading(false); }
  }, [activeTab, activityUserId, logCategory, logSearch, navigate, session]);
  useEffect(() => { void loadLogs(); }, [loadLogs]);
  useEffect(() => { document.title = "用户管理 · 舟序 Zhouxu"; return () => { document.title = "舟序 Zhouxu"; }; }, []);

  const stats = useMemo(() => ({
    active: users.filter(user => user.status === "active").length,
    suspended: users.filter(user => user.status === "suspended").length,
    used: users.reduce((sum, user) => sum + user.used_tokens, 0),
    remaining: users.reduce((sum, user) => sum + user.remaining_tokens, 0),
  }), [users]);

  if (!session) return <Navigate to="/login" replace />;

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    setSearch(query.trim());
  }

  function openEditor(user: AdminAccount) {
    setEditing(user);
    setQuota(String(user.quota_tokens));
    setUsed(String(user.used_tokens));
    setRole(user.role === "admin" ? "admin" : "user");
    setError("");
  }

  function openPasswordEditor(user: AdminAccount) {
    setPasswordUser(user);
    setNewPassword("");
    setError("");
  }

  async function toggleStatus(user: AdminAccount) {
    setSaving(true);
    setError("");
    try {
      const updated = await updateAdminUser(session.session.token, user.id, { status: user.status === "active" ? "suspended" : "active" });
      setUsers(items => items.map(item => item.id === updated.id ? updated : item));
    } catch (value) {
      const apiError = value as AdminApiError;
      setError(apiError.code === "cannot_modify_self" ? "不能停用当前管理员账户。" : "状态更新失败，请稍后重试。 ");
    } finally { setSaving(false); }
  }

  async function saveEditor(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const nextQuota = Number(quota);
    const nextUsed = Number(used);
    if (!Number.isInteger(nextQuota) || nextQuota < 0 || !Number.isInteger(nextUsed) || nextUsed < 0 || nextUsed > nextQuota) {
      setError("请输入有效额度：已使用量不能大于总额度。 ");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const updated = await updateAdminUser(session.session.token, editing.id, { quota_tokens: nextQuota, used_tokens: nextUsed, role });
      setUsers(items => items.map(item => item.id === updated.id ? updated : item));
      setEditing(null);
    } catch (value) {
      const apiError = value as AdminApiError;
      setError(apiError.code === "quota_below_usage" ? "总额度不能低于已使用量。" : "额度更新失败，请稍后重试。 ");
    } finally { setSaving(false); }
  }

  async function savePassword(event: FormEvent) {
    event.preventDefault();
    if (!passwordUser) return;
    if (newPassword.length < 12) {
      setError("新密码至少需要 12 个字符。 ");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await resetAdminUserPassword(session.session.token, passwordUser.id, newPassword);
      setPasswordUser(null);
      setNewPassword("");
    } catch (value) {
      const apiError = value as AdminApiError;
      setError(apiError.code === "cannot_modify_self" ? "不能重置当前管理员账户的密码。" : "密码重置失败，请稍后重试。 ");
    } finally { setSaving(false); }
  }

  async function removeUser(user: AdminAccount) {
    if (!window.confirm(`确定注销“${user.display_name || user.email}”吗？该账户及其工作区数据将永久删除。`)) return;
    setSaving(true);
    setError("");
    try {
      await deleteAdminUser(session.session.token, user.id);
      setUsers(items => items.filter(item => item.id !== user.id));
      setTotal(value => Math.max(0, value - 1));
    } catch (value) {
      const apiError = value as AdminApiError;
      setError(apiError.code === "cannot_modify_self" ? "不能注销当前管理员账户。" : "注销失败，请稍后重试。 ");
    } finally { setSaving(false); }
  }

  return (
    <main className="admin-page">
      <header className="admin-header">
        <Link className="admin-brand" to="/workspace" aria-label="回到舟序工作台"><span className="admin-mark">舟</span><span><strong>Zhouxu</strong><small>舟序 · 管理控制台</small></span></Link>
        <div className="admin-header-actions"><span className="admin-admin-badge"><Shield size={14} />管理员</span><Link className="admin-back" to="/workspace"><ArrowLeft size={15} />回到工作台</Link></div>
      </header>

      <section className="admin-shell">
        <div className="admin-heading"><div><p className="admin-eyebrow">ADMINISTRATION / ACCOUNTS</p><h1>用户管理</h1><p>查看账户状态，调整 token 额度，掌握每位用户的使用情况。</p></div><button className="admin-refresh" onClick={() => void load()} disabled={loading || saving}><RefreshCw size={16} className={loading ? "admin-spin" : ""} />刷新数据</button></div>

        <div className="admin-stats" aria-label="用户概览">
          <article><span>账户总数</span><strong>{formatTokens(total)}</strong><small>当前筛选结果</small></article>
          <article><span>活跃用户</span><strong>{formatTokens(stats.active)}</strong><small>本页账户</small></article>
          <article><span>已使用额度</span><strong>{formatTokens(stats.used)}</strong><small>tokens · 本页</small></article>
          <article><span>剩余额度</span><strong>{formatTokens(stats.remaining)}</strong><small>tokens · 本页</small></article>
        </div>

        <nav className="admin-tabs" aria-label="管理数据视图">
          <button className={activeTab === "users" ? "active" : ""} onClick={() => setActiveTab("users")}><UserRound size={15} />用户账户</button>
          <button className={activeTab === "activity" ? "active" : ""} onClick={() => setActiveTab("activity")}><Activity size={15} />用户日志</button>
          <button className={activeTab === "usage" ? "active" : ""} onClick={() => setActiveTab("usage")}><Gauge size={15} />使用日志</button>
          <button className={activeTab === "audit" ? "active" : ""} onClick={() => setActiveTab("audit")}><ScrollText size={15} />审计日志</button>
        </nav>

        {error && <div className="admin-alert" role="alert">{error}</div>}
        {activeTab === "users" ? <>
          <div className="admin-toolbar">
            <form onSubmit={submitSearch} className="admin-search"><Search size={17} aria-hidden="true" /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索邮箱或称呼" aria-label="搜索邮箱或称呼" /><button type="submit">搜索</button></form>
            <label className="admin-filter"><SlidersHorizontal size={16} /><span>状态</span><select value={status} onChange={event => setStatus(event.target.value)}><option value="all">全部用户</option><option value="active">活跃</option><option value="suspended">已停用</option></select></label>
          </div>
          <section className="admin-table-card" aria-label="用户列表">
            {loading ? <div className="admin-empty"><LoaderCircle className="admin-spin" size={24} /><span>正在读取账户数据…</span></div> : users.length === 0 ? <div className="admin-empty"><UserRound size={24} /><span>没有符合条件的账户</span></div> : <div className="admin-table-wrap"><table><thead><tr><th>用户</th><th>状态</th><th>额度与使用</th><th>角色</th><th aria-label="操作" /></tr></thead><tbody>{users.map(user => <tr key={user.id}><td><div className="admin-user"><span className="admin-avatar">{(user.display_name || user.email).slice(0, 1).toUpperCase()}</span><span><strong>{user.display_name || "未设置称呼"}</strong><small>{user.email}</small></span></div></td><td><span className={`admin-status ${user.status}`}><i />{user.status === "active" ? "活跃" : "已停用"}</span></td><td><UsageBar user={user} /></td><td><span className={`admin-role ${user.role}`}><span>{user.role === "admin" ? "管理员" : "普通用户"}</span></span></td><td><div className="admin-row-actions"><button onClick={() => openEditor(user)} disabled={saving}>管理额度</button><button onClick={() => openPasswordEditor(user)} disabled={saving}><KeyRound size={13} />重置密码</button><button className="admin-status-action" onClick={() => void toggleStatus(user)} disabled={saving}>{user.status === "active" ? "停用" : "启用"}</button><button className="admin-danger-action" onClick={() => void removeUser(user)} disabled={saving}><Trash2 size={13} />注销</button></div></td></tr>)}</tbody></table></div>}
          </section>
          <p className="admin-footnote">管理员可调整额度、重置密码、停用或注销用户；注销会永久删除账户及其工作区数据。</p>
        </> : <>
          <div className="admin-toolbar">
            {activeTab === "activity" ? <label className="admin-filter admin-user-picker"><UserRound size={16} /><span>用户</span><select value={activityUserId} onChange={event => setActivityUserId(event.target.value)}>{users.map(user => <option key={user.id} value={user.id}>{user.display_name || user.email}</option>)}</select></label> : <form onSubmit={event => { event.preventDefault(); void loadLogs(); }} className="admin-search"><Search size={17} aria-hidden="true" /><input value={logSearch} onChange={event => setLogSearch(event.target.value)} placeholder="搜索用户、动作或运行 ID" aria-label="搜索日志" /><button type="submit">搜索</button></form>}
            {activeTab === "audit" && <label className="admin-filter"><SlidersHorizontal size={16} /><span>分类</span><select value={logCategory} onChange={event => setLogCategory(event.target.value)}><option value="all">全部事件</option><option value="auth">登录与账户</option><option value="admin">管理员操作</option></select></label>}
            <button className="admin-refresh" onClick={() => void loadLogs()} disabled={logLoading}><RefreshCw size={16} className={logLoading ? "admin-spin" : ""} />刷新日志</button>
          </div>
          <section className="admin-table-card" aria-label="日志列表">
            {logLoading ? <div className="admin-empty"><LoaderCircle className="admin-spin" size={24} /><span>正在读取日志…</span></div> : activeTab === "activity" ? <div className="admin-table-wrap"><table><thead><tr><th>时间</th><th>类型</th><th>动作</th><th>状态</th><th>详情</th></tr></thead><tbody>{activityLogs.map(item => <tr key={`${item.kind}-${item.id}`}><td className="admin-nowrap">{formatDate(item.created_at)}</td><td><span className={`admin-log-kind ${item.kind}`}>{item.kind === "usage" ? "使用" : "账户"}</span></td><td>{item.action}</td><td><span className={`admin-log-status ${item.status === "success" || item.status === "completed" ? "ok" : "bad"}`}>{item.status}</span></td><td><code>{JSON.stringify(item.details)}</code></td></tr>)}</tbody></table>{activityLogs.length === 0 && <div className="admin-empty"><Activity size={24} /><span>这个用户还没有活动记录</span></div>}</div> : activeTab === "usage" ? <div className="admin-table-wrap"><table><thead><tr><th>时间</th><th>用户</th><th>运行</th><th>模型 / 模式</th><th>消耗</th><th>状态</th></tr></thead><tbody>{usageLogs.map(item => <tr key={item.id}><td className="admin-nowrap">{formatDate(item.created_at)}</td><td><strong>{item.display_name || "未设置称呼"}</strong><small className="admin-cell-sub">{item.email || item.account_id}</small></td><td><code>{item.run_id.slice(0, 12)}…</code></td><td>{item.model} / {item.mode}</td><td>{formatTokens(item.total_tokens)} tokens</td><td><span className="admin-log-status ok">{item.status}</span></td></tr>)}</tbody></table>{usageLogs.length === 0 && <div className="admin-empty"><Gauge size={24} /><span>还没有使用记录</span></div>}</div> : <div className="admin-table-wrap"><table><thead><tr><th>时间</th><th>分类</th><th>操作者</th><th>动作</th><th>结果</th><th>请求</th></tr></thead><tbody>{auditLogs.map(item => <tr key={item.id}><td className="admin-nowrap">{formatDate(item.created_at)}</td><td><span className="admin-log-kind audit">{item.category}</span></td><td>{item.actor_email || "系统"}</td><td>{item.action}<small className="admin-cell-sub">{item.target_email ? `目标：${item.target_email}` : ""}</small></td><td><span className={`admin-log-status ${item.success ? "ok" : "bad"}`}>{item.success ? "成功" : "失败"} · {item.status_code}</span></td><td><code>{item.request_id || "—"}</code></td></tr>)}</tbody></table>{auditLogs.length === 0 && <div className="admin-empty"><ScrollText size={24} /><span>还没有审计记录</span></div>}</div>}
          </section>
        </>}
      </section>

      {editing && <div className="admin-modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setEditing(null); }}><section className="admin-modal" role="dialog" aria-modal="true" aria-labelledby="admin-modal-title"><div className="admin-modal-header"><div><p className="admin-eyebrow">ACCOUNT CONTROL</p><h2 id="admin-modal-title">管理账户</h2><p>{editing.display_name || editing.email}</p></div><button className="admin-close" onClick={() => setEditing(null)} aria-label="关闭"><X size={18} /></button></div><form onSubmit={saveEditor}><label>账户角色<select value={role} onChange={event => setRole(event.target.value as "user" | "admin")}><option value="user">普通用户</option><option value="admin">管理员</option></select></label><label>总额度（tokens）<input type="number" min="0" step="1" value={quota} onChange={event => setQuota(event.target.value)} /></label><label>已使用额度（tokens）<input type="number" min="0" step="1" value={used} onChange={event => setUsed(event.target.value)} /></label><div className="admin-modal-summary"><span>调整后剩余</span><strong>{formatTokens(Math.max(0, Number(quota || 0) - Number(used || 0)))} tokens</strong></div><div className="admin-modal-actions"><button type="button" onClick={() => setEditing(null)}>取消</button><button type="submit" className="admin-primary" disabled={saving}>{saving ? <LoaderCircle className="admin-spin" size={16} /> : <Check size={16} />}保存调整</button></div></form></section></div>}
      {passwordUser && <div className="admin-modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setPasswordUser(null); }}><section className="admin-modal" role="dialog" aria-modal="true" aria-labelledby="admin-password-title"><div className="admin-modal-header"><div><p className="admin-eyebrow">ACCOUNT SECURITY</p><h2 id="admin-password-title">重置密码</h2><p>{passwordUser.display_name || passwordUser.email}</p></div><button className="admin-close" onClick={() => setPasswordUser(null)} aria-label="关闭"><X size={18} /></button></div><form onSubmit={savePassword}><label>新密码<input type="password" minLength={12} autoComplete="new-password" value={newPassword} onChange={event => setNewPassword(event.target.value)} placeholder="至少 12 个字符" /></label><p className="admin-modal-hint">保存后，该用户的现有登录会话将全部失效。</p><div className="admin-modal-actions"><button type="button" onClick={() => setPasswordUser(null)}>取消</button><button type="submit" className="admin-primary" disabled={saving}>{saving ? <LoaderCircle className="admin-spin" size={16} /> : <KeyRound size={16} />}保存新密码</button></div></form></section></div>}
    </main>
  );
}
