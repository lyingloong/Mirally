/** Shared explicit speech transcription service for desktop and channels. */
import {mkdir,readFile,writeFile,rename,rm,readdir,mkdtemp,realpath,chmod} from 'node:fs/promises';
import {join,basename,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
import {spawn} from 'node:child_process';

const limit=8*1024*1024;
const fail=(message,code='speech_invalid')=>Object.assign(new Error(message),{code,status:400});
const bounded=(v,max=1024)=>typeof v==='string'&&v.length<=max&&!v.includes('\0');

export class SpeechService {
  constructor(host) {this.host=host;this.directory=join(host.home,'speech');this.path=join(this.directory,'settings.json');this.closed=false;this.jobs=new Map();this.configQueue=Promise.resolve();}
  async credential(operation,value,signal) {
    if(signal?.aborted)throw fail('语音识别已取消。','speech_cancelled');
    return new Promise((resolve,reject)=>{
      const child=spawn(this.host.config.python,['-m','mirally.runtime.speech_credentials'],{cwd:this.host.root,windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1'}});
      let finished=false, output='', size=0;const abort=()=>child.kill();
      const timer=setTimeout(abort,10000);signal?.addEventListener('abort',abort,{once:true});
      const done=(error,value)=>{if(finished)return;finished=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);error?reject(error):resolve(value);};
      child.stderr.resume();child.stdout.on('data',chunk=>{size+=chunk.length;if(size>64000)child.kill();else output+=chunk;});
      child.on('error',()=>done(fail('语音凭据不可用，请重新设置密钥。','speech_credential_unavailable')));
      child.on('close',()=>{if(signal?.aborted)return done(fail('语音识别已取消。','speech_cancelled'));
        try{const result=JSON.parse(output);if(result.ok&&typeof result.value==='string'&&result.value)return done(null,result.value);}catch{}
        done(fail('语音凭据不可用，请重新设置密钥。','speech_credential_unavailable'));});
      child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify({operation,value}));if(signal?.aborted)abort();
    });
  }
  async raw() {
    try {const value=JSON.parse(await readFile(this.path,'utf8'));if(!value||typeof value!=='object'||Array.isArray(value))throw Error();return value;}
    catch(error){if(error.code==='ENOENT')return {provider:'none'};throw fail('语音设置无法读取，请检查配置。','speech_config_invalid');}
  }
  async settings() {
    const value=await this.raw();
    const directory=join(this.host.home,'speech-models');
    const models=await readdir(directory,{withFileTypes:true}).then(entries=>entries.filter(entry=>entry.isDirectory()).map(entry=>({name:entry.name,path:join(directory,entry.name)}))).catch(()=>[]);
    const local_ready=await new Promise(resolve=>{const child=spawn(this.host.config.python,['-c',"import importlib.util; print(int(importlib.util.find_spec('faster_whisper') is not None))"],{windowsHide:true,stdio:['ignore','pipe','ignore']});let result='';child.stdout.on('data',chunk=>result+=chunk);child.on('error',()=>resolve(false));child.on('close',()=>resolve(result.trim()==='1'));});
    return {provider:value.provider||'none',base_url:value.base_url||'',model:value.model||'',language:value.language||'auto',
      local_model_path:value.local_model_path||'',api_key_configured:!!value.api_key_protected,local_ready,local_models:models,local_model_directory:directory};
  }
  configure(patch) {
    const next=this.configQueue.then(()=>this.configureOne(patch));this.configQueue=next.catch(()=>{});return next;
  }
  async configureOne(patch) {
    if (!patch||typeof patch!=='object'||Array.isArray(patch)||Object.keys(patch).some(k=>!['provider','base_url','model','language','local_model_path','api_key','clear_api_key'].includes(k))) throw fail('语音设置无效。');
    const old=await this.raw(), value={...old,...patch};delete value.clear_api_key;
    if(!['none','openai_compatible','faster_whisper'].includes(value.provider))throw fail('请选择有效的语音识别服务。');
    for(const key of ['base_url','model','language','local_model_path','api_key']) if(value[key]!==undefined&&!bounded(value[key],key==='api_key'?8192:2048))throw fail('语音设置字段无效。');
    if(patch.clear_api_key===true)delete value.api_key_protected;
    else if(patch.api_key) value.api_key_protected=await this.credential('protect',patch.api_key);
    delete value.api_key;
    if(value.provider==='openai_compatible') {
      let url;try{url=new URL(value.base_url);}catch{throw fail('请输入有效的语音服务地址。');}
      if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.hash||url.search)throw fail('语音服务地址无效。');
      if(old.api_key_protected && !patch.api_key && old.base_url) {
        let oldOrigin;try{oldOrigin=new URL(old.base_url).origin;}catch{}
        if(oldOrigin!==url.origin)throw fail('更换语音服务来源时请重新填写 API 密钥。');
      }
      if(!value.model?.trim()||!value.api_key_protected)throw fail('请填写语音模型和 API 密钥。');
    }
    if(value.provider==='faster_whisper'&&!value.local_model_path?.trim())throw fail('请选择本地语音模型目录。');
    await mkdir(this.directory,{recursive:true,mode:0o700});
    if(process.platform!=='win32')await chmod(this.directory,0o700);
    const temporary=`${this.path}.${randomUUID()}.tmp`;
    try {await writeFile(temporary,JSON.stringify(value),{mode:0o600,flag:'wx'});await rename(temporary,this.path);}
    catch(error){await rm(temporary,{force:true}).catch(()=>{});throw error;}
    return this.settings();
  }
  async transcribe({data,media_type='audio/wav',name='recording.wav',language,signal,request_id}={}) {
    if(this.closed)throw fail('语音服务已停止。','speech_unavailable');
    if(!Buffer.isBuffer(data)||!data.length||data.length>limit)throw fail('录音为空或超过 8 MiB。');
    if(!bounded(media_type,128)||!media_type.startsWith('audio/')||!bounded(name,256)||language!==undefined&&!bounded(language,32))throw fail('录音格式无效。');
    const id=request_id||randomUUID();if(!/^[a-zA-Z0-9_-]{16,128}$/.test(id)||this.jobs.has(id))throw fail('识别请求标识无效或已在使用。');
    const controller=new AbortController();let finished;controller.done=new Promise(resolve=>finished=resolve);this.jobs.set(id,controller);
    let temporary;
    const relay=()=>controller.abort();signal?.addEventListener('abort',relay,{once:true});if(signal?.aborted)controller.abort();
    try {
    const config=await this.raw();if(controller.signal.aborted)throw fail('语音识别已取消。','speech_cancelled');
    if(!['openai_compatible','faster_whisper'].includes(config.provider))throw fail('请先在设置中配置语音识别。','speech_unavailable');
    temporary=await mkdtemp(join(tmpdir(),'mirally-speech-'));
    if(controller.signal.aborted)throw fail('语音识别已取消。','speech_cancelled');
    if(config.provider==='openai_compatible') {
      if(!config.api_key_protected)throw fail('请重新设置语音 API 密钥。','speech_credential_unavailable');
      config.api_key=await this.credential('unprotect',config.api_key_protected,controller.signal);
      delete config.api_key_protected;
    }
    const request={config,data:data.toString('base64'),media_type,name:basename(name.replaceAll('\\','/')),language:language||config.language||'auto',temp_dir:temporary};
    return await new Promise((resolve,reject)=>{
      const child=spawn(this.host.config.python,['-m','mirally.runtime.speech_worker'],{cwd:this.host.root,windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1'}});
      let finished=false,bytes=0;const chunks=[];
      const abort=()=>child.kill();const timer=setTimeout(()=>child.kill(),125000);controller.signal.addEventListener('abort',abort,{once:true});
      const done=(error,result)=>{if(finished)return;finished=true;clearTimeout(timer);controller.signal.removeEventListener('abort',abort);error?reject(error):resolve(result);};
      child.stderr.resume();child.stdout.on('data',chunk=>{bytes+=chunk.length;if(bytes>1000000)child.kill();else chunks.push(chunk);});
      child.on('error',()=>done(fail('语音识别服务启动失败。','speech_unavailable')));
      child.on('close',code=>{if(controller.signal.aborted)return done(fail('语音识别已取消。','speech_cancelled'));
        let output;try{output=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{return done(fail('语音识别未完成，请检查服务设置。','speech_failed'));}
        if(code!==0||!output.ok)return done(fail(output.error||'语音识别未完成，请检查服务设置。','speech_failed'));
        if(typeof output.text!=='string'||!output.text.trim()||output.text.length>100000)return done(fail('没有识别出可用文字。','speech_empty'));
        done(null,{text:output.text.trim(),language:output.language||null,provider:config.provider});});
      child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify(request));if(controller.signal.aborted)abort();
    });
    } finally {if(temporary)await this.removeTemporary(temporary).catch(()=>this.host.ctx?.logger?.warn?.('语音临时文件清理失败。'));signal?.removeEventListener('abort',relay);if(this.jobs.get(id)===controller)this.jobs.delete(id);finished();}
  }
  async removeTemporary(path) {
    const [root,target]=await Promise.all([realpath(tmpdir()),realpath(path)]);
    if(dirname(target)!==root||!/^mirally-speech-[a-zA-Z0-9]+$/.test(basename(target)))throw fail('语音临时目录位置无效。');
    await rm(target,{recursive:true,force:true});
  }
  cancel(request_id){const controller=this.jobs.get(request_id);if(!controller)return {cancelled:false};controller.abort();return {cancelled:true};}
  async dispose(){this.closed=true;const jobs=[...this.jobs.values()];for(const controller of jobs)controller.abort();await Promise.all(jobs.map(controller=>controller.done));}
}
