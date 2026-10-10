// 市场安装：SkillHub 搜索 / 官方库 / GitHub URL 直装
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { fetchZhDesc } from '../api/zh';
import { Search, Box, Link } from './icons';

interface HubSkill {
  name: string; slug: string; description: string; category: string;
  author: string; stars: number; repo_url: string; installable: boolean;
}

export default function InstallDialog({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<'search' | 'official' | 'url'>('search');
  const [q, setQ] = useState('');
  const [results, setResults] = useState<HubSkill[] | null>(null);
  const [zh, setZh] = useState<Record<string, string>>({});
  const [official, setOfficial] = useState<string[] | null>(null);
  const [hubErr, setHubErr] = useState('');
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');

  useEffect(() => {
    if (tab === 'official' && official === null) {
      void (async () => {
        const d = await api<{ skills?: string[]; error?: string }>('/api/skillhub/catalog');
        setOfficial(d.skills ?? []);
        setHubErr(d.error ?? '');
      })();
    }
  }, [tab]);

  async function search() {
    setResults(null); setHubErr(''); setZh({});
    try {
      const d = await api<{ skills?: HubSkill[]; error?: string }>(
        `/api/skillhub/search?q=${encodeURIComponent(q)}`);
      const skills = d.skills ?? [];
      setResults(skills);
      setHubErr(d.error ?? '');
      void fetchZhDesc(skills).then(r => {
        if (r === null && skills.length) setHubErr('翻译服务不可用，简介显示原文');   // E6 降级可见（不阻断安装）
        else if (r) setZh(r);
      });   // 英文描述 → 中文简介（kv 缓存）
    } catch (e) { setHubErr(`搜索请求失败：${String(e)}——可重试，或用「链接安装」直装`); }
  }

  async function install(body: Record<string, unknown>, label: string) {
    setBusy(label); setMsg('');
    try {
      const d = await api<{ installed: string[]; skipped?: string[] }>('/api/skills/install', {
        method: 'POST', body: JSON.stringify(body),
      });
      setMsg(`已安装：${d.installed.join(', ')}${d.skipped?.length ? '（跳过 ' + d.skipped.join('; ') + '）' : ''}`);
    } catch (e) { setMsg(`失败：${String(e)}`); }
    setBusy('');
  }

  return (
    <div className="install-dialog">
      <div className="admin-tabs">
        <button className={`tab ${tab === 'search' ? 'on' : ''}`} onClick={() => setTab('search')}><Search size={13} /> 搜索市场</button>
        <button className={`tab ${tab === 'official' ? 'on' : ''}`} onClick={() => setTab('official')}><Box size={13} /> 官方库</button>
        <button className={`tab ${tab === 'url' ? 'on' : ''}`} onClick={() => setTab('url')}><Link size={13} /> 链接直装</button>
      </div>

      {tab === 'search' && (
        <>
          <div className="search-row">
            <input value={q} onChange={e => setQ(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') void search(); }}
              placeholder="搜技能，例：excel / pdf / 爬虫 / react" autoFocus />
            <button className="btn sm primary" onClick={() => void search()}>搜索</button>
          </div>
          {hubErr && <div className="admin-err">{hubErr}（可用 GitHub 链接直装）</div>}
          <div className="hub-results">
            {(results ?? []).map(s => (
              <div key={s.slug} className="hub-card">
                <div className="sk-head">
                  <b>{s.name}</b>
                  <span className="sk-src local">{s.category}</span>
                </div>
                <div className="sk-desc" title={zh[s.name] ? s.description : undefined}>
                  {zh[s.name]
                    ? <><span className="zh-tag" title={s.description}>译</span>{zh[s.name]}</>
                    : s.description}
                </div>
                <div className="sk-foot">
                  <span className="sk-time">{s.author} · ⭐{s.stars}</span>
                  {s.installable
                    ? <button className="btn sm primary" disabled={!!busy}
                      onClick={() => void install({ repo_url: s.repo_url }, s.slug)}>
                      {busy === s.slug ? '安装中…' : '安装'}</button>
                    : <span className="muted" title={s.repo_url}>非 GitHub 源，不支持</span>}
                </div>
              </div>
            ))}
            {results !== null && results.length === 0 && !hubErr && (
              <div className="panel-empty">无结果，换个关键词</div>)}
          </div>
        </>
      )}

      {tab === 'official' && (
        <>
          {hubErr && <div className="admin-err">{hubErr}</div>}
          <div className="hub-results">
            {(official ?? []).map(n => (
              <div key={n} className="hub-card slim">
                <b>{n}</b>
                <button className="btn sm primary" disabled={!!busy}
                  onClick={() => void install({ repo: 'anthropics/skills', subpath: `skills/${n}` }, n)}>
                  {busy === n ? '安装中…' : '安装'}</button>
              </div>
            ))}
            {official !== null && official.length === 0 && !hubErr && (
              <div className="panel-empty">目录拉取为空</div>)}
          </div>
        </>
      )}

      {tab === 'url' && (
        <div className="url-install">
          <label>GitHub 地址 / owner/repo 简写 / 任意 https 归档 URL（.zip/.tar.gz，agentskills.io 兼容）</label>
          <textarea className="mono" rows={4} value={url} onChange={e => setUrl(e.target.value)}
            placeholder={'anthropics/skills/skills/docx\ngithub.com/owner/repo#skills~my-skill\ngithub.com/owner/repo/tree/main/skills/my-skill\nhttps://example.com/my-skill.zip'} />
          <button className="btn primary sm" disabled={!url.trim() || !!busy}
            onClick={() => {
              // github.com 地址与无协议简写走 repo_url；其余 https 归档走 url
              const u = url.trim();
              const archive = /^https:\/\//.test(u) && !/^https:\/\/(www\.)?github\.com\//.test(u);
              void install(archive ? { url: u } : { repo_url: u }, '__url');
            }}>
            {busy === '__url' ? '下载安装中…' : '安装'}
          </button>
        </div>
      )}

      {msg && <div className="se-msg">{msg}</div>}
      <div className="install-warn">
        ⚠ 安装第三方 skill = 信任其作者：skill 内脚本会以 bypassPermissions 被 agent 执行。
      </div>
      <div className="modal-foot">
        <button className="btn ghost sm" onClick={onClose}>完成</button>
      </div>
    </div>
  );
}
