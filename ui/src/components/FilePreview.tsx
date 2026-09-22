// 主区文件预览 tab：md 渲染 / html 沙箱 iframe / pdf / 图片 / 文本
import { useEffect, useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api, withToken } from '../api/client';
import { Globe, FileDoc, RotateCw, Link, ExternalLink, Download } from './icons';

const MD_EXTS = ['.md', '.markdown'];
const HTML_EXTS = ['.html', '.htm'];
const PDF_EXTS = ['.pdf'];

export default function FilePreview({ sid, path }: { sid: string; path: string }) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState('');
  const [nonce, setNonce] = useState(0);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);

  const name = path.split('/').pop() ?? path;
  const dot = name.lastIndexOf('.');
  const ext = dot >= 0 ? name.slice(dot).toLowerCase() : '';
  const isPdf = PDF_EXTS.includes(ext);

  // pdf：raw 端点取字节（旧服务 mime 标成 text/plain 也能收）→ 重打 Blob 交给
  // 浏览器原生阅读器渲染，绕开 JSON 预览接口对二进制不给 b64 的限制
  useEffect(() => {
    if (!isPdf) return;
    let stop = false;
    let url: string | null = null;
    setPdfUrl(null);
    (async () => {
      try {
        const resp = await fetch(withToken(
          `/api/sessions/${encodeURIComponent(sid)}/file?path=${encodeURIComponent(path)}&raw=1`));
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        url = URL.createObjectURL(
          new Blob([await resp.arrayBuffer()], { type: 'application/pdf' }));
        if (!stop) setPdfUrl(url);
      } catch (e) { if (!stop) setErr(String(e)); }
    })();
    return () => { stop = true; if (url) URL.revokeObjectURL(url); };
  }, [sid, path, nonce, isPdf]);

  useEffect(() => {
    let stop = false;
    setD(null); setErr('');
    api<any>(`/api/sessions/${encodeURIComponent(sid)}/file?path=${encodeURIComponent(path)}`)
      .then(r => { if (!stop) setD(r); })
      .catch(e => { if (!stop) setErr(String(e)); });
    return () => { stop = true; };
  }, [sid, path, nonce]);

  const sizeKb = d?.size != null ? `${Math.max(1, Math.round(d.size / 1024))}KB` : '';
  const [shareState, setShareState] = useState<'idle' | 'busy' | 'done'>('idle');
  const shareable = path.startsWith('artifacts/');
  // 原始字节直下（download 属性强制落盘；too_large/binary 无法预览时的唯一出路）
  const dlUrl = withToken(
    `/api/sessions/${encodeURIComponent(sid)}/file?path=${encodeURIComponent(path)}&raw=1`);

  const doShare = async () => {
    setShareState('busy');
    try {
      const r = await api<{ url: string }>(`/api/sessions/${encodeURIComponent(sid)}/share`,
        { method: 'POST', body: JSON.stringify({ path }) });
      await navigator.clipboard.writeText(r.url);
      setShareState('done');
      setTimeout(() => setShareState('idle'), 2000);
    } catch (e) {
      alert(String(e));
      setShareState('idle');
    }
  };

  let body: ReactNode;
  const stale = d && typeof d.path === 'string' && d.path !== path;   // 切换瞬间的旧数据
  if (err) body = <div className="fp-hint">⚠ {err}</div>;
  else if (isPdf)
    body = pdfUrl
      ? <iframe className="fp-frame" title={path} src={pdfUrl} />
      : <div className="fp-hint">PDF 加载中…</div>;
  else if (!d || stale) body = <div className="fp-hint">加载中…</div>;
  else if (d.error) body = <div className="fp-hint">⚠ {d.error}</div>;
  else if (d.cat === 'img')
    body = <img className="fp-img" src={`data:${d.mime};base64,${d.b64}`} alt={path} />;
  else if (d.cat === 'too_large')
    body = <div className="fp-hint">文件超过 2MB 预览上限（{sizeKb}），
      <a className="link" href={dlUrl} download>点此下载</a></div>;
  else if (d.cat === 'binary')
    body = <div className="fp-hint">二进制文件不支持预览（{sizeKb}），
      <a className="link" href={dlUrl} download>点此下载</a></div>;
  else if (HTML_EXTS.includes(ext))
    // src 直载而非 srcDoc：iframe 有自身 URL，文档内锚点可原生滚动（srcDoc 的锚点会
    // 按父页面 base 解析，点击把 iframe 导航到宿主 SPA 路由）。脚本仍被 sandbox 禁用。
    body = <iframe className="fp-frame" sandbox="" title={path}
      src={withToken(`/api/sessions/${encodeURIComponent(sid)}/file?path=${encodeURIComponent(path)}&raw=1`)} />;
  else if (MD_EXTS.includes(ext))
    body = <div className="md fp-md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{d.text}</ReactMarkdown></div>;
  else
    body = <pre className="fp-pre">{d.text}</pre>;

  return (
    <div className="file-preview">
      <div className="fp-bar">
        <span className="fp-path" title={path}>
          {HTML_EXTS.includes(ext) ? <Globe size={13} /> : <FileDoc size={13} />} {path}
        </span>
        <span className="fp-size">{sizeKb}</span>
        <a className="btn ghost sm fp-reload" title="下载此文件" href={dlUrl} download>
          <Download size={13} />
        </a>
        {shareable && (
          <button className="btn ghost sm fp-reload" title={shareState === 'done' ? '链接已复制' : '分享并复制链接'}
            disabled={shareState === 'busy'} onClick={doShare}>
            <Link size={13} />
          </button>
        )}
        {(HTML_EXTS.includes(ext) || isPdf) && (
          <button className="btn ghost sm fp-reload" title="在新窗口打开（完整页面，目录锚点直达）"
            onClick={() => {
              if (isPdf) { if (pdfUrl) window.open(pdfUrl, '_blank'); }
              else window.open(withToken(
                `/api/sessions/${encodeURIComponent(sid)}/file?path=${encodeURIComponent(path)}&raw=1`), '_blank');
            }}>
            <ExternalLink size={13} />
          </button>
        )}
        <button className="btn ghost sm fp-reload" title="重新加载" onClick={() => setNonce(n => n + 1)}>
          <RotateCw size={13} />
        </button>
      </div>
      <div className="fp-body">{body}</div>
    </div>
  );
}
