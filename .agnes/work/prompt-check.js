const fs=require('fs');const path=require('path');
const CFG=path.join(process.env.APPDATA,'com.baozi.model-manager','providers.json');
const P=JSON.parse(fs.readFileSync(CFG,'utf8'));
function buildUrl(b,p){let x=b.trim().replace(/\/+$/,'');if(x.endsWith('#'))x=x.slice(0,-1);else if(!x.endsWith('/v1'))x+='/v1';return x+p;}
function contentOf(v){if(!v)return'';const a=Array.isArray(v.choices)?v.choices:(v.data&&Array.isArray(v.data.choices)?v.data.choices:null);if(a&&a.length){const c=a[0];if(c&&c.message&&typeof c.message.content==='string')return c.message.content;if(c&&typeof c.text==='string')return c.text;if(c&&c.data&&typeof c.data==='string')return c.data;}return'';}
const KWS=['quota','recharge','topup','free quota','prevention of abuse','insufficient','rate limit','rate_limit','exceeded','exhausted','no available','overloaded','no credit','access denied','invalid api','未授权','无权限','余额不足','配额','限流'];
const PROMPT='你好，请用一句话简单介绍一下你自己';
async function t(base,key,model){
  const url=buildUrl(base,'/chat/completions');
  const body={model,messages:[{role:'user',content:PROMPT}],max_tokens:256};
  const ctrl=new AbortController();const tm=setTimeout(()=>ctrl.abort(),90000);
  try{
    const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},body:JSON.stringify(body),signal:ctrl.signal});
    const s=r.status;const tx=await r.text();
    if(s<200||s>=300)return{pass:false,why:'HTTP '+s};
    let v=null;try{v=JSON.parse(tx);}catch{}
    if(!(Array.isArray(v&&v.choices)||(v&&v.data&&Array.isArray(v.data.choices))))return{pass:false,why:'无choices'};
    const c=contentOf(v).trim();
    if(!c)return{pass:false,why:'空内容→拦截'};
    const low=c.toLowerCase();const hit=KWS.find(k=>low.includes(k));
    if(hit)return{pass:false,why:'关键字「'+hit+'」→拦截'};
    return{pass:true,why:'通过: '+c.slice(0,50)};
  }catch(e){return{pass:false,why:e.name==='AbortError'?'超时':String(e.message||e)};}
  finally{clearTimeout(tm);}
}
async function main(){
  const st2=P.find(p=>p.name==='商汤2');
  const aih=P.find(p=>p.name==='aihubmix');
  const tk=P.find(p=>p.name==='tokenrouter');
  let out='=== 用开放 prompt 验证(空内容/关键字 拦截) ===\n';
  out+='-- 商汤2 --\n';
  for(const m of['glm-5.2','deepseek-v4-pro']){const r=await t(st2.base_url,st2.api_key,m);out+=(r.pass?'[通过] ':'[不通过] ')+m+' → '+r.why+'\n';}
  out+='\n-- aihubmix (quota) --\n';
  for(const m of['gpt-4.1-free','coding-glm-5.3-free']){const r=await t(aih.base_url,aih.api_key,m);out+=(r.pass?'[通过] ':'[不通过] ')+m+' → '+r.why+'\n';}
  out+='\n-- tokenrouter (真可用对照) --\n';
  for(const m of P.find(p=>p.name==='tokenrouter').models){const r=await t(tk.base_url,tk.api_key,m);out+=(r.pass?'[通过] ':'[不通过] ')+m+' → '+r.why+'\n';}
  fs.writeFileSync('D:/Users/ai_model/.agnes/work/prompt-check.txt',out);
  console.log(out);
}
main();
