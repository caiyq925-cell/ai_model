const fs=require('fs');
const path=require('path');
const APPDATA=process.env.APPDATA;
const CFG=path.join(APPDATA,'com.baozi.model-manager','providers.json');
const providers=JSON.parse(fs.readFileSync(CFG,'utf8'));
const groq=providers.find(p=>p.name==='groq');

function buildUrl(base,p){let b=base.trim().replace(/\/+$/,'');if(b.endsWith('#'))b=b.slice(0,-1);else if(!b.endsWith('/v1'))b+='/v1';return b+p;}

async function fetchModelList(baseUrl,apiKey){
  const url=buildUrl(baseUrl,'/models');
  const ctrl=new AbortController();
  const timer=setTimeout(()=>ctrl.abort(),30000);
  try{
    const resp=await fetch(url,{headers:{'Authorization':'Bearer '+apiKey},signal:ctrl.signal});
    const text=await resp.text();
    let v=null;try{v=JSON.parse(text);}catch{}
    let ids=[];
    if(v){if(Array.isArray(v.data))ids=v.data.map(m=>m.id).filter(Boolean);if(!ids.length&&Array.isArray(v.models))ids=v.models.map(m=>(typeof m==='string'?m:(m.id||m.name))).filter(Boolean);}
    return{ids,error:resp.ok?null:'HTTP '+resp.status,raw:text.slice(0,300)};
  }catch(e){return{ids:[],error:String(e.message||e),raw:''};}
  finally{clearTimeout(timer);}
}

async function chatTest(baseUrl,apiKey,model,timeoutMs){
  const url=buildUrl(baseUrl,'/chat/completions');
  const body={model,messages:[{role:'user',content:'请用一句中文简短回答：你喜欢做什么？'}],max_tokens:128};
  const start=Date.now();
  const ctrl=new AbortController();
  const timer=setTimeout(()=>ctrl.abort(),timeoutMs);
  try{
    const resp=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+apiKey},body:JSON.stringify(body),signal:ctrl.signal});
    const status=resp.status;const text=await resp.text();
    let v=null;try{v=JSON.parse(text);}catch{}
    let content='';
    if(v){const arr=v.choices||(v.data&&v.data.choices);if(arr&&arr.length){const c0=arr[0];if(c0&&c0.message&&typeof c0.message.content==='string')content=c0.message.content;}}
    return{status,content,err:v&&(v.error?(v.error.message||v.error):'')||'',ms:Date.now()-start,raw:text.slice(0,300)};
  }catch(e){return{status:0,content:'',err:e.name==='AbortError'?'TIMEOUT('+timeoutMs+'ms)':String(e.message||e),ms:Date.now()-start,raw:'',exception:true};}
  finally{clearTimeout(timer);}
}

async function main(){
  let out='';
  // 1) groq models
  const groqModels=await fetchModelList(groq.base_url,groq.api_key);
  out+='=== GROQ /models ===\n';
  out+='error: '+(groqModels.error||'none')+'\n';
  out+='ids: '+JSON.stringify(groqModels.ids)+'\n';
  out+='raw: '+groqModels.raw+'\n\n';

  // 2) aihubmix sample - look at actual content
  const aih=providers.find(p=>p.name==='aihubmix');
  out+='=== AIHUBMIX content samples ===\n';
  for(const m of ['gpt-4.1-free','gpt-5.5-free','ox-alpha']){
    const r=await chatTest(aih.base_url,aih.api_key,m,30000);
    out+='  '+m+' status='+r.status+' content='+JSON.stringify(r.content.slice(0,200))+' err='+r.err+'\n';
  }
  out+='\n';

  // 3) 超时重测 - 这些可能只是慢，重试
  out+='=== TIMEOUT RETRY (120s) ===\n';
  const retries=[
    ['anyapi',providers.find(p=>p.name==='anyapi'),'nvidia/nemotron-3-ultra-550b-a55b:free'],
    ['英伟达',providers.find(p=>p.name==='英伟达'),'nvidia/llama-3.1-nemoguard-8b-content-safety'],
    ['新疆幻城',providers.find(p=>p.name==='新疆幻城'),'DeepSeek-V4-Pro'],
    ['新疆幻城',providers.find(p=>p.name==='新疆幻城'),'kimi-k3'],
    ['新疆幻城',providers.find(p=>p.name==='新疆幻城'),'longcat-2.0'],
  ];
  for(const[name,prov,model]of retries){
    const r=await chatTest(prov.base_url,prov.api_key,model,120000);
    out+='  '+name+'/'+model+' status='+r.status+' ms='+r.ms+' content='+JSON.stringify((r.content||'').slice(0,200))+' err='+r.err+'\n';
  }
  fs.writeFileSync('D:/Users/ai_model/.agnes/work/analysis2.txt',out);
  console.log('done');
}
main().catch(e=>console.error(e.message));
