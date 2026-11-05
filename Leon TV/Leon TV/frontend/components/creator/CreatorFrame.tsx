import Link from 'next/link';
import { Clapperboard, FileText, LayoutGrid, Settings, ShieldCheck } from 'lucide-react';
import type { MouseEvent, ReactNode } from 'react';
import BrandMark from '@/components/BrandMark';

export default function CreatorFrame({ children, navigation, onNavigate }: { children: ReactNode; navigation?: ReactNode; onNavigate?: (event: MouseEvent<HTMLAnchorElement>) => void }) {
  return (
    <div className="creator-workspace">
      <a className="creator-skip" href="#creator-main">跳到创作简报</a>
      <aside className="creator-sidebar" aria-label="LeonTV 创作导航">
        <Link href="/creator" className="creator-brand" onClick={onNavigate}><BrandMark size={32} /><span>LeonTV</span></Link>
        <div className="creator-sidebar-caption">创作空间</div>
        {navigation}
        <details className="creator-advanced" open>
          <summary>高级创作 · 原有入口</summary>
          <Link href="/studio" onClick={onNavigate}><LayoutGrid size={16} />项目画布与测试运行</Link>
          <Link href="/workflow" onClick={onNavigate}><Clapperboard size={16} />六阶段创作</Link>
          <Link href="/sandbox" onClick={onNavigate}><FileText size={16} />单次生成工具</Link>
          <Link href="/settings" onClick={onNavigate}><Settings size={16} />设置</Link>
        </details>
        <p className="creator-sidebar-note"><ShieldCheck size={16} />保存简报不授权付费，<br />不会开始模型制作。</p>
      </aside>
      <div className="creator-main-wrap">
        <header className="creator-top"><span>创作空间 <span aria-hidden="true">/</span> <strong>创作简报</strong></span><span className="creator-badge">可带入六阶段 · 看片待接入</span></header>
        <main id="creator-main" className="creator-main">{children}</main>
      </div>
    </div>
  );
}
