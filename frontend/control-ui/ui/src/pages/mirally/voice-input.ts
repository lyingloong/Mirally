import {html,nothing,type PropertyValues} from 'lit';
import {property,state} from 'lit/decorators.js';
import {icons} from '../../components/icons.ts';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {requestProduct} from './api.ts';
import './product.css';

/** Explicit microphone capture, never a background or always-on listener. */
class MirallyVoiceInput extends OpenClawLightDomElement {
  @property() sessionKey='';
  @property({attribute:false}) getDraft?: ()=>string;
  @property({attribute:false}) onDraftChange?: (value:string)=>void;
  @property({type:Boolean}) disabled=false;
  @property({type:Boolean}) presented=true;
  @state() private phase:'idle'|'starting'|'recording'|'transcribing'='idle';
  @state() private error='';
  private stream?:MediaStream;
  private recorder?:MediaRecorder;
  private chunks:Blob[]=[];
  private timer?:ReturnType<typeof setTimeout>;
  private generation=0;
  private requestId='';
  private abort?:AbortController;
  private captureSession='';
  override disconnectedCallback(){this.cancel();super.disconnectedCallback();}
  protected override updated(changed:PropertyValues){
    if((changed.has('sessionKey')&&changed.get('sessionKey')!==undefined&&this.captureSession&&this.sessionKey!==this.captureSession)
      ||(changed.has('presented')&&!this.presented))this.cancel();
  }
  private release(){clearTimeout(this.timer);this.timer=undefined;for(const track of this.stream?.getTracks()??[])track.stop();this.stream=undefined;}
  private cancel(){
    ++this.generation;this.release();
    const recorder=this.recorder;this.recorder=undefined;
    if(recorder&&recorder.state!=='inactive'){
      // A final dataavailable can arrive after stop. Detach all callbacks so
      // cancelled audio cannot enter a later recording or another session.
      recorder.ondataavailable=null;recorder.onerror=null;recorder.onstop=null;recorder.stop();
    }
    this.chunks=[];this.abort?.abort();this.abort=undefined;
    const id=this.requestId;this.requestId='';
    if(id)void requestProduct('/api/rpc',{method:'POST',body:JSON.stringify({method:'speech.cancel',params:{request_id:id}})}).catch(()=>{});
    this.phase='idle';this.captureSession='';
  }
  private async start(){
    if(this.phase!=='idle'||this.disabled||!this.sessionKey)return;
    this.error='';this.phase='starting';this.captureSession=this.sessionKey;const generation=++this.generation;
    try {
      if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined')throw new Error('当前环境无法使用麦克风。');
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});
      if(!this.isConnected||generation!==this.generation||this.sessionKey!==this.captureSession){stream.getTracks().forEach(track=>track.stop());return;}
      this.stream=stream;this.chunks=[];const recorder=new MediaRecorder(stream);this.recorder=recorder;
      recorder.ondataavailable=event=>{if(event.data.size)this.chunks.push(event.data);};
      recorder.onerror=()=>{this.cancel();this.error='录音中断，请检查麦克风。';};
      recorder.onstop=()=>void this.finish(recorder.mimeType,generation);
      recorder.start();this.phase='recording';this.timer=setTimeout(()=>this.stop(),60000);
    } catch(error){if(generation===this.generation){this.release();this.phase='idle';this.captureSession='';
      const name=error && typeof error==='object' && 'name' in error ? String(error.name) : '';
      this.error=name==='NotAllowedError'?'麦克风权限未开启，请检查 Windows 和窗口权限。'
        :name==='NotFoundError'?'未找到麦克风，请检查设备。'
        :name==='NotReadableError'?'麦克风不可用或正被占用。'
        :error instanceof Error?error.message:'无法启动录音。';}}
  }
  private stop(){if(this.phase==='recording'&&this.recorder?.state==='recording'){clearTimeout(this.timer);this.recorder.stop();this.release();this.phase='transcribing';}}
  private async finish(mime:string,generation:number){
    this.release();this.recorder=undefined;
    const blob=new Blob(this.chunks,{type:mime||'audio/webm'});this.chunks=[];
    try {
      if(!blob.size||blob.size>8*1024*1024)throw new Error('录音为空或超过 8 MiB，请重试。');
      const data=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('无法读取录音。'));reader.onload=()=>resolve(String(reader.result).split(',',2)[1]||'');reader.readAsDataURL(blob);});
      if(generation!==this.generation)return;
      const requestId=crypto.randomUUID();this.requestId=requestId;this.abort=new AbortController();
      const mediaType=(blob.type.split(';')[0]||'audio/webm');
      const result=await requestProduct<{result:{text:string}}>('/api/rpc',{method:'POST',signal:this.abort.signal,body:JSON.stringify({method:'speech.transcribe',params:{request_id:requestId,data,media_type:mediaType,name:mediaType==='audio/ogg'?'recording.ogg':mediaType==='audio/mp4'?'recording.m4a':'recording.webm'}})});
      if(!this.isConnected||generation!==this.generation||this.sessionKey!==this.captureSession)return;
      const text=result.result?.text;if(typeof text!=='string'||!text.trim())throw new Error('没有识别出文字。');
      const draft=this.getDraft?.()??'';
      this.onDraftChange?.(draft+(draft&&!/\s$/.test(draft)?' ':'')+text.trim());
    }catch(error){if(generation===this.generation)this.error=error instanceof Error?error.message:'语音识别失败，请检查设置。';}
    finally{if(generation===this.generation){this.phase='idle';this.captureSession='';this.requestId='';this.abort=undefined;}}
  }
  override render(){
    const label=this.phase==='recording'?'停止录音':this.phase==='transcribing'?'取消识别':this.phase==='starting'?'取消录音':'语音输入';
    return html`<span class="mirally-voice-input"><button type="button" class="chat-send-btn chat-send-btn--voice" aria-label=${label} title=${label} ?disabled=${this.disabled&&this.phase==='idle'} @click=${()=>this.phase==='idle'?this.start():this.phase==='recording'?this.stop():this.cancel()}>${this.phase==='recording'?icons.stop:this.phase==='transcribing'?'…':icons.mic}</button>${this.error?html`<span role="alert">${this.error}</span>`:nothing}</span>`;
  }
}
customElements.define('mirally-voice-input',MirallyVoiceInput);
