export type SidebarJob = {
  state?: string; action?: string; label?: string; progress_message?: string; scope?: string;
  progress_step?: number; progress_total?: number; cancel_requested?: boolean;
};

const tasks: Record<string, string> = {
  participate_all: '活动列表，接着抽！', participate: '嘿嘿，继续抽奖！', participate_triple: '三连安排上！', participate_pool: '预选池，挨个抽！',
  maintenance_check: '开奖啦？查一查', maintenance_clean: '核查中，慢慢清理', maintenance_cycle: '开奖清理，核查中',
  maintenance_link: '核对参与记录中', maintenance_discover: '找找对应的转发', maintenance_discover_all: '正在匹配本人转发',
  refresh_all: '找新活动呢！', refresh_source: '正在更新数据源', refresh_status: '正在核对参与状态',
  refresh_watch: '看看 UP 更新没', refresh_discovery: '新活动，搜寻中！', refresh_discovery_history: '补齐近 7 天活动',
  login: '等你扫码登录～',
};

export function accountTaskText(job: SidebarJob, loggedIn: boolean): string {
  if (job.state !== 'running') return loggedIn ? '已保存登录' : '需重新登录';
  if (job.cancel_requested) return '正在收尾，准备停下';
  // Only interpret a current step, never keywords in the prize/activity title.
  const step = (job.progress_message || '').split(' · ').at(-1)?.trim() || '';
  let text = tasks[job.action || ''] || job.label?.split(' · ')[0] || '任务执行中';
  if ((job.action || '').startsWith('participate')) {
    const stages: [RegExp, string][] = [
      [/^正在关注/, '关注呢，别催～'], [/^正在点赞/, '小红心，点上啦！'],
      [/^正在收藏/, '收藏安排上！'], [/^正在转发/, '转发中，接好运！'],
      [/^正在评论/, '评论写着呢～'], [/^正在预约/, '预约中，占个位！'],
      [/^(正在等待|等待|休息|间隔)/, '歇一下，再接好运'], [/^正在检查/, '先看看活动状态'],
    ];
    text = stages.find(([pattern]) => pattern.test(step))?.[1] || text;
  }
  const total = job.progress_total || 0;
  const done = Math.max(0, Math.min(job.progress_step || 0, total));
  if (job.scope === 'global') text = `共享 · ${text}`;
  return total ? `${text} ${done}/${total}` : text;
}
