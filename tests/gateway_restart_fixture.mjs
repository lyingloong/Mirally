// Synthetic Gateway lifecycle fixture: TCP readiness only, no OpenClaw/model.
import net from "node:net";
import {spawn} from "node:child_process";

spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], {
  stdio: "ignore", windowsHide: true,
});
net.createServer(socket => socket.end()).listen(Number(process.env.OPENCLAW_GATEWAY_PORT), "127.0.0.1");
