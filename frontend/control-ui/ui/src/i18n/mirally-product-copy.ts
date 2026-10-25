// Product-owned copy layered over the retained component catalog. Keys and saved
// locale preferences stay compatible; translated UI never inherits host branding.
const EN: Record<string, string> = {
  "modelSetup.required.title": "Connect a model to Mirally",
  "modelSetup.required.body": "Add your AI provider connection so Mirally can chat and help with your life and work.",
  "modelSetup.required.action": "Connect a model",
  "chat.welcome.suggestions.whatCanYouDo": "What can we work on together?",
  "chat.welcome.suggestions.summarizeRecentSessions": "Help me organize my recent conversations",
  "chat.welcome.suggestions.configureChannel": "Help me plan my day",
  "chat.welcome.suggestions.checkSystemHealth": "What would you like to know about me?",
  "configView.themes.claw.label": "Mirally",
  "configView.themes.claw.description": "Mirally blue and cool neutrals for your everyday workspace.",
  "configView.appearance.accents.claw": "Coral red",
  "nav.askOpenClaw": "Ask Mirally",
  "chat.catalog.openInOpenClaw": "Open in Mirally",
  "chat.progressLabels.thinking": "Thinking",
  "chat.progressLabels.considering": "Considering",
  "chat.progressLabels.working": "Working",
  "chat.progressLabels.processing": "Processing",
  "chat.progressLabels.reflecting": "Reflecting",
};
const ZH: Record<string, string> = {
  "modelSetup.required.title": "为 Mirally 连接模型",
  "modelSetup.required.body": "添加你自己的 AI 服务连接，开始生活与工作中的对话和协作。",
  "modelSetup.required.action": "连接模型",
  "chat.welcome.suggestions.whatCanYouDo": "我们可以一起做些什么？",
  "chat.welcome.suggestions.summarizeRecentSessions": "帮我整理最近的对话",
  "chat.welcome.suggestions.configureChannel": "帮我安排今天的事情",
  "chat.welcome.suggestions.checkSystemHealth": "你想了解我的哪些偏好？",
  "configView.themes.claw.label": "Mirally",
  "configView.themes.claw.description": "晴空蓝与冷色中性色，适合日常陪伴与协作。",
  "configView.appearance.accents.claw": "珊瑚红",
  "nav.askOpenClaw": "询问 Mirally",
  "chat.catalog.openInOpenClaw": "在 Mirally 中打开",
  "chat.progressLabels.thinking": "思考中",
  "chat.progressLabels.considering": "斟酌中",
  "chat.progressLabels.working": "处理中",
  "chat.progressLabels.processing": "整理中",
  "chat.progressLabels.reflecting": "推敲中",
};

export function mirallyProductTranslation(key: string, locale: string, value: unknown): string | undefined {
  const override = (locale.startsWith("zh") ? ZH : EN)[key];
  if (override !== undefined) return override;
  return typeof value === "string" ? value.replace(/\bOpenClaw\b/g, "Mirally") : undefined;
}
