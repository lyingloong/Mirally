import { ArrowRight, ArrowUpRight } from "lucide-react";

export function Brand({ root }) {
  return (
    <span className="brand">
      <img src={`${root}mirally.png`} alt="" width="44" height="44" />
      <span>
        mirally<span className="brand-period">.</span>
      </span>
    </span>
  );
}

export function Header({ root, updatesHref }) {
  return (
    <header className="site-header updates-header">
      <div className="header-inner">
        <a className="brand-link" href={root} aria-label="返回 Mirally 首页">
          <Brand root={root} />
        </a>
        <nav className="main-nav" aria-label="主导航">
          <a href={`${root}#a-day`}>场景演示</a>
          <a href={`${root}#already-here`}>当前能力</a>
          <a href={updatesHref} className="nav-active" aria-current="page">
            更新
          </a>
        </nav>
        <a className="header-cta" href={root}>
          返回首页 <ArrowRight size={16} />
        </a>
      </div>
    </header>
  );
}

export function Footer({ root }) {
  return (
    <footer className="site-footer updates-footer">
      <div className="container footer-inner">
        <a href={root} aria-label="返回 Mirally 首页">
          <Brand root={root} />
        </a>
        <p>© {new Date().getFullYear()} Mirally</p>
        <div>
          <a href={root}>首页</a>
          <a href={`${root}#a-day`}>场景演示</a>
        </div>
      </div>
    </footer>
  );
}

export function NextStep({ root }) {
  return (
    <section className="updates-next">
      <div className="container updates-next-inner">
        <p>想看看现在的 Mirally 能做什么？</p>
        <div>
          <a className="button button-dark" href={`${root}#already-here`}>
            查看当前能力 <ArrowUpRight size={17} />
          </a>
          <a className="text-link" href={`${root}#a-day`}>
            场景演示 <ArrowRight size={16} />
          </a>
        </div>
      </div>
    </section>
  );
}
