import { CalendarDays, FileText, Link2, UserRound } from "lucide-react";

export default function BusinessVisual() {
  return (
    <div className="business-visual" role="img" aria-label="Mirally 商业路径示意：从个人工作空间，扩展到团队成员与共享文档，并连接日历、文件等服务。">
      <div className="bv-canvas" aria-hidden="true">
        <div className="bv-brand"><img src="./mirally.png" alt="" /><span>Mirally</span></div>
        <div className="bv-paths"><i /><i /><i /></div>
        <div className="bv-groups">
          <div className="bv-group bv-personal">
            <div className="bv-personal-art"><div className="bv-workspace"><div className="bv-window-top"><i /><i /><i /></div><div className="bv-workspace-body"><span className="bv-account"><UserRound size={19} strokeWidth={1.6} /></span><div className="bv-workspace-lines"><i /><i /><i /></div></div></div></div>
            <span className="bv-label">个人</span>
          </div>
          <div className="bv-group bv-team">
            <div className="bv-team-art"><div className="bv-members"><span><UserRound size={17} strokeWidth={1.7} /></span><span><UserRound size={17} strokeWidth={1.7} /></span><span><UserRound size={17} strokeWidth={1.7} /></span></div><div className="bv-shared-doc"><FileText size={20} strokeWidth={1.5} /><div><i /><i /></div></div></div>
            <span className="bv-label">团队</span>
          </div>
          <div className="bv-group bv-services">
            <div className="bv-services-art"><span className="bv-service-calendar"><CalendarDays size={23} strokeWidth={1.5} /></span><span className="bv-service-file"><FileText size={23} strokeWidth={1.5} /></span><span className="bv-service-link"><Link2 size={23} strokeWidth={1.5} /></span><div className="bv-service-path" /></div>
            <span className="bv-label">服务</span>
          </div>
        </div>
      </div>
    </div>
  );
}
