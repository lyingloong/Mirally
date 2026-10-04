/** A real task through Mirally's bridge: reconnect, steer, tool events and result. */
import { readFile, writeFile, mkdir, access } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { DesktopBridge } from "./desktop-bridge.mjs";
import { textOf } from "./session-view.mjs";

if (!process.argv.includes("--live")) { console.error("Use --live; this check calls the configured model."); process.exit(2); }
const root = fileURLToPath(new URL("../../", import.meta.url));
const config = JSON.parse(await readFile(resolve(root,"config/openassist/openclaw/openclaw.json"),"utf8"));
const label = randomUUID().slice(0,8);
const directory = resolve(config.agents.defaults.workspace,"desktop-client-verification",label);
const worker = resolve(directory,"worker.py"), ready = resolve(directory,"ready.txt");
const release = resolve(directory,"release.txt"), result = resolve(directory,"result.txt");
const marker = `MIRALLY-BRIDGE-${label}`;
await mkdir(directory,{recursive:true});
await writeFile(worker,"from pathlib import Path\nimport time\np=Path(__file__).parent\n(p/'ready.txt').write_text('ready')\ndeadline=time.monotonic()+150\nwhile not (p/'release.txt').exists():\n    if time.monotonic()>deadline: raise SystemExit('timeout')\n    time.sleep(.2)\nprint('worker finished')\n");
let bridge;
let key;
let runId;
const events = [];
const checks = {};
async function waitFor(predicate, timeout=45000) {
  const deadline=Date.now()+timeout;
  while(Date.now()<deadline) {
    if(await predicate()) return;
    await new Promise(accept=>setTimeout(accept,100));
  }
  throw new Error("Desktop task check timed out.");
}
async function connect() {
  bridge = new DesktopBridge(config,event=>events.push(event));
  bridge.start();
  await waitFor(()=>bridge.connected && bridge.sessions.length>0);
}
try {
  await connect();
  const created = await bridge.command({op:"new"});
  key=created.key;
  const ack=await bridge.command({op:"send",params:{key,message:
    `这是一次客户端后台验收，不进行出生流程。用exec工具通过 ${resolve(root,'.venv/Scripts/python.exe')} 运行Python脚本 ${worker}，使用后台方式或短yield。脚本等待外部释放；用process等它结束，不要创建release.txt。结束后，用write工具在 ${result} 写入 ORIGINAL-${label}。然后说明结果。不要操作桌面。`}});
  runId=ack.runId;
  await waitFor(async()=>{try{await access(ready);return true;}catch{return false;}},90000);
  checks.liveToolActivity=events.some(event=>event.type==='session' && event.activity?.name==='exec');
  await bridge.stop();
  await connect();
  await bridge.command({op:'select',params:{key}});
  checks.reconnectActiveIdentity=bridge.view(key).activeRunIds.has(runId);
  await bridge.command({op:'send',params:{key,message:`补充：结束后，result.txt 内容改为 ${marker}，不要写之前的 ORIGINAL 标记。继续这轮验收。`}});
  await writeFile(release,'release');
  await waitFor(async()=>{try{return (await readFile(result,'utf8')).trim()===marker;}catch{return false;}},150000);
  await waitFor(()=>!bridge.view(key).activeRunIds.has(runId),60000);
  await bridge.history(key);
  const snapshot=bridge.view(key).snapshot();
  checks.steerChangedArtifact=(await readFile(result,'utf8')).trim()===marker;
  checks.supplementInNativeHistory=snapshot.entries.some(entry=>entry.message.role==='user' && textOf(entry.message).includes(marker));
  const assistants=snapshot.entries.filter(entry=>entry.message.role==='assistant' && textOf(entry.message));
  const final=assistants.at(-1);
  checks.finalShownOnce=Boolean(final) && assistants.filter(entry=>textOf(entry.message)===textOf(final.message)).length===1;
  checks.authoritativeIdle=snapshot.activeRunIds.length===0 && !snapshot.activeUnknown;
  checks.liveReplyVisible=events.some(event=>event.type==='session' && event.entries.some(entry=>entry.live && entry.message.role==='assistant' && textOf(entry.message)));
  const output=resolve(root,'output/openclaw-native-verification/desktop');
  await mkdir(output,{recursive:true});
  await writeFile(resolve(output,'checks.json'),JSON.stringify({key,marker,checks},null,2));
  console.log(JSON.stringify(checks));
  if(Object.values(checks).some(value=>value!==true)) process.exitCode=1;
} finally {
  await writeFile(release,'release');
  await bridge?.stop();
}
