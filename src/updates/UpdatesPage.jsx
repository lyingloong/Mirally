import { ArrowRight, ArrowUpRight, Check, ImagePlus } from "lucide-react";

const features = [
  {
    title: "微信随身",
    image: null,
    alt: "微信对话与 Mirally 助理会话示意",
    points: [
      "在微信里直接对话：文字、语音、图片、文件与视频都能接收。",
      "助理会话与直接会话随时切换，问题可委托原会话继续处理。",
      "成果可以作为文件或图片回传微信。",
    ],
  },
  {
    title: "日历与提醒",
    image: null,
    alt: "日历安排与提醒投递示意",
    points: [
      "在对话中安排日程，并设置提前提醒。",
      "支持每日、每周重复，按你的时区在桌面与微信分别投递。",
      "可以查看、编辑或取消整个提醒系列。",
    ],
  },
  {
    title: "定时执行",
    image: null,
    alt: "会话定时执行与结果提醒示意",
    points: [
      "让会话在指定时间自动执行，支持一次与周期任务。",
      "到期前复核会话与授权状态，解绑后自动暂停。",
      "异常重启时不重放结果不明的执行。",
    ],
  },
  {
    title: "活动记忆",
    image: null,
    alt: "活动摘要与来源记录示意",
    points: [
      "整理电脑上的活动摘要，按问题找回会议、场地和决定。",
      "回答附来源记录，可以展开查看原始片段。",
      "采集范围与开关由你控制。",
    ],
  },
  {
    title: "手机网页",
    image: null,
    alt: "手机浏览器配对与继续对话示意",
    points: [
      "手机浏览器打开同一会话，扫码完成配对。",
      "离开电脑也能继续对话、接收提醒。",
      "配对设备可以随时查看与撤销。",
    ],
  },
  {
    title: "扩展与模型",
    image: null,
    alt: "Skill、MCP 与模型连接设置示意",
    points: [
      "接入 Skill、MCP 与可信插件，能力按需加载。",
      "模型连接可以按对话与后台分别指定。",
      "会话支持摘要与自动标题，手动标题优先。",
    ],
  },
];

const improvements = [
  {
    title: "微信回执更清楚",
    text: "回执会说明进入哪种会话模式；忙碌、停止、失败和附件都有对应提示，菜单与帮助按用途分组。",
    image: null,
    alt: "微信回执与菜单分组示意",
  },
  {
    title: "会话列表更稳定",
    text: "会话按最近活动排序，置顶会话固定留在列表顶部，不再因顺序变化跳到标题上方。",
    image: null,
    alt: "会话列表排序与置顶示意",
  },
  {
    title: "摘要与标题",
    text: "手动标题优先；历史内容更新后会标记摘要过期，避免继续引用旧摘要。",
    image: null,
    alt: "会话摘要与标题示意",
  },
  {
    title: "材料与附件",
    text: "上传后立即回执，失败时回显具体原因；长回复标出截断位置。",
    image: null,
    alt: "材料上传回执示意",
  },
  {
    title: "桌面体验",
    text: "开屏、主题、托盘与伙伴形态保持一致，窗口恢复和退出流程更稳定。",
    image: null,
    alt: "桌面开屏与伙伴形态示意",
  },
];

const fixes = [
  "修复微信调用网页搜索时工具名称不匹配导致的拦截。",
  "修复停止回复时重复提示的问题。",
  "修复置顶会话显示在侧栏标题上方的问题。",
];

function Brand() {
  return (
    <span className="brand">
      <img src="../mirally.png" alt="" width="44" height="44" />
      <span>
        mirally<span className="brand-period">.</span>
      </span>
    </span>
  );
}

function Header() {
  return (
    <header className="site-header updates-header">
      <div className="header-inner">
        <a className="brand-link" href="../" aria-label="返回 Mirally 首页">
          <Brand />
        </a>
        <nav className="main-nav" aria-label="主导航">
          <a href="../#a-day">场景演示</a>
          <a href="../#already-here">当前能力</a>
          <a href="./" className="nav-active" aria-current="page">
            更新
          </a>
        </nav>
        <a className="header-cta" href="../">
          返回首页 <ArrowRight size={16} />
        </a>
      </div>
    </header>
  );
}

function Media({ image, alt }) {
  if (image) {
    return (
      <figure className="update-media">
        <img src={image} alt={alt} loading="lazy" />
      </figure>
    );
  }
  return (
    <div className="update-media update-media-empty" role="img" aria-label={`图示占位：${alt}`}>
      <ImagePlus size={22} strokeWidth={1.4} aria-hidden="true" />
      <span className="update-media-note">图示待补充</span>
    </div>
  );
}

function UpdatesHero() {
  return (
    <section className="updates-hero">
      <div className="container">
        <span className="eyebrow">
          <span className="status-dot" /> PRODUCT UPDATES
        </span>
        <h1>更新</h1>
        <p className="updates-lede">最近交付的新功能、体验优化与修复。</p>
        <div className="updates-meta">
          <span>2026 年 11 月</span>
          <span>持续更新</span>
        </div>
      </div>
    </section>
  );
}

function FeatureSection() {
  return (
    <section className="update-section" aria-labelledby="updates-features">
      <div className="container">
        <div className="update-section-heading">
          <h2 id="updates-features">新增功能</h2>
          <p>这一批新加入的用户可见能力</p>
        </div>
        <div className="update-grid">
          {features.map((item) => (
            <article className="update-card" key={item.title}>
              <Media image={item.image} alt={item.alt} />
              <div className="update-card-body">
                <h3>{item.title}</h3>
                <ul>
                  {item.points.map((point) => (
                    <li key={point}>{point}</li>
                  ))}
                </ul>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function ImprovementSection() {
  return (
    <section className="update-section" aria-labelledby="updates-improvements">
      <div className="container">
        <div className="update-section-heading">
          <h2 id="updates-improvements">体验优化</h2>
          <p>让已有流程更清楚、更稳定</p>
        </div>
        <div className="update-rows">
          {improvements.map((item) => (
            <article className="update-row" key={item.title}>
              <div className="update-row-copy">
                <h3>{item.title}</h3>
                <p>{item.text}</p>
              </div>
              <Media image={item.image} alt={item.alt} />
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function FixSection() {
  return (
    <section className="update-section" aria-labelledby="updates-fixes">
      <div className="container">
        <div className="update-section-heading">
          <h2 id="updates-fixes">问题修复</h2>
          <p>近期解决的已知问题</p>
        </div>
        <ul className="update-fixes">
          {fixes.map((item) => (
            <li key={item}>
              <Check size={16} aria-hidden="true" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function NextStep() {
  return (
    <section className="updates-next">
      <div className="container updates-next-inner">
        <p>想看看现在的 Mirally 能做什么？</p>
        <div>
          <a className="button button-dark" href="../#already-here">
            查看当前能力 <ArrowUpRight size={17} />
          </a>
          <a className="text-link" href="../#a-day">
            场景演示 <ArrowRight size={16} />
          </a>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="site-footer updates-footer">
      <div className="container footer-inner">
        <a href="../" aria-label="返回 Mirally 首页">
          <Brand />
        </a>
        <p>© {new Date().getFullYear()} Mirally</p>
        <div>
          <a href="../">首页</a>
          <a href="../#a-day">场景演示</a>
        </div>
      </div>
    </footer>
  );
}

export default function UpdatesPage() {
  return (
    <>
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>
      <Header />
      <main id="main">
        <UpdatesHero />
        <FeatureSection />
        <ImprovementSection />
        <FixSection />
        <NextStep />
      </main>
      <Footer />
    </>
  );
}
