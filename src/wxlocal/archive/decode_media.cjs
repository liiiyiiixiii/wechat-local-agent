// Local codecs only. No network calls. Keys are read from private configuration.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),cp=require('node:child_process');
const root=process.env.WECHAT_ROOT||path.resolve(__dirname,'../../..');
let keys, addon, silk;
function imageCodec() {
 if (!keys) keys=JSON.parse(fs.readFileSync(process.env.WECHAT_MEDIA_KEYS || path.join(root,'private/media-keys.json'),'utf8'));
 if (!addon) addon=require(keys.addonPath);
 return addon;
}
const [jobsPath,resultsPath,outputRoot]=process.argv.slice(2);
function wav(pcm){const h=Buffer.alloc(44);h.write('RIFF');h.writeUInt32LE(36+pcm.length,4);h.write('WAVE',8);h.write('fmt ',12);h.writeUInt32LE(16,16);h.writeUInt16LE(1,20);h.writeUInt16LE(1,22);h.writeUInt32LE(24000,24);h.writeUInt32LE(48000,28);h.writeUInt16LE(2,32);h.writeUInt16LE(16,34);h.write('data',36);h.writeUInt32LE(pcm.length,40);return Buffer.concat([h,pcm]);}
function ext(b){if(b.subarray(0,3).equals(Buffer.from([255,216,255])))return '.jpg';if(b.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))return '.png';if(b.subarray(0,3).toString()==='GIF')return '.gif';if(b.subarray(0,4).toString()==='RIFF'&&b.subarray(8,12).toString()==='WEBP')return '.webp';if(b.subarray(0,2).toString()==='BM')return '.bmp';return null;}
function unwrap(b){
 if(b.subarray(0,4).toString()!=='wxgf')return b;
 for(let i=4;i<Math.min(b.length-12,4096);i++){if(b[i]===255&&b[i+1]===216&&b[i+2]===255)return b.subarray(i);if(b.subarray(i,i+8).equals(Buffer.from([137,80,78,71,13,10,26,10])))return b.subarray(i);}
 const starts=[];for(let i=4;i<b.length-3;i++){let n=b[i]===0&&b[i+1]===0&&b[i+2]===1?3:(b[i]===0&&b[i+1]===0&&b[i+2]===0&&b[i+3]===1?4:0);if(n){starts.push([i,n]);i+=n-1;}}
 const units=starts.map(([s,n],i)=>b.subarray(s+n,starts[i+1]?.[0]||b.length)).filter(x=>x.length>1&&!(x[0]&128));
 const merge=u=>Buffer.concat(u.flatMap(x=>[Buffer.from([0,0,0,1]),x]));const groups=[];let current=[];
 for(const u of units){if(((u[0]>>1)&63)===32&&current.length){groups.push(merge(current));current=[];}current.push(u);}if(current.length)groups.push(merge(current));
 const candidates=[...groups.sort((a,b)=>b.length-a.length),merge(units),b.subarray(4)];
 for(const input of candidates){if(input.length<100)continue;const r=cp.spawnSync(process.env.WECHAT_FFMPEG || 'ffmpeg',['-hide_banner','-loglevel','error','-f','hevc','-i','pipe:0','-frames:v','1','-f','image2pipe','-vcodec','png','pipe:1'],{input,timeout:15000,maxBuffer:64*1024*1024});if(r.status===0&&ext(r.stdout))return r.stdout;}
 throw Error('wxgf_decode_failed');
}
(async()=>{
 process.umask(0o077);let count=0,ok=0;const fd=fs.openSync(resultsPath,'w',0o600);
 for(const line of fs.readFileSync(jobsPath,'utf8').split('\n').filter(Boolean)){
  const job=JSON.parse(line);let result={id:job.id};
  try{let data,extension,sourceMd5;
   if(job.kind==='voice'){const input=fs.readFileSync(job.source);if(input.subarray(0,4).toString()==='RIFF'){data=input;}else{silk ||= require(process.env.WECHAT_SILK || path.join(root,'validation/WeFlow/node_modules/silk-wasm'));const decoded=await silk.decode(input,24000);if(!decoded.data?.length)throw Error('silk_empty');data=wav(Buffer.from(decoded.data));}extension='.wav';}
   else if(job.kind==='emoji'){
    const input=fs.readFileSync(job.source);const candidates=[];
    for(const value of job.keys||[]){if(/^[0-9a-f]{32}$/i.test(value))candidates.push(Buffer.from(value,'hex'));if(Buffer.byteLength(value)===16)candidates.push(Buffer.from(value));}
    for(const key of candidates){for(const mode of ['aes-128-ecb','aes-128-cbc']){for(const iv of mode.endsWith('ecb')?[null]:[Buffer.alloc(16),key]){
     try{const decipher=crypto.createDecipheriv(mode,key,iv);const decoded=Buffer.concat([decipher.update(input),decipher.final()]);if(ext(decoded)&&crypto.createHash('md5').update(decoded).digest('hex')===job.md5){data=decoded;extension=ext(data);break;}}catch{} 
    }if(data)break;}if(data)break;}
    if(!data)throw Error('emoji_key_or_format_not_matched');
   }
   else{const r=imageCodec().decryptDatNative(job.source,keys.imageXorKey,keys.imageAesKey);sourceMd5=crypto.createHash('md5').update(r.data).digest('hex');data=unwrap(r.data);extension=ext(data);if(!extension)throw Error('unknown_decrypted_format');}
   const hash=crypto.createHash('sha256').update(data).digest('hex');const relative='assets/'+hash.slice(0,2)+'/'+hash+extension;const dest=path.join(outputRoot,relative);fs.mkdirSync(path.dirname(dest),{recursive:true,mode:0o700});if(!fs.existsSync(dest))fs.writeFileSync(dest,data,{mode:0o600});result={...result,status:'decoded',path:relative,sha256:hash,size:data.length,sourceMd5};ok++;
  }catch(e){result={...result,status:'decode_failed',error:String(e.message).slice(0,240)};}
  fs.writeSync(fd,JSON.stringify(result)+'\n');count++;if(count%100===0)console.log(JSON.stringify({phase:'decode-media',processed:count,decoded:ok}));
 }
 fs.closeSync(fd);console.log(JSON.stringify({phase:'decode-media',processed:count,decoded:ok,complete:true}));
})().catch(e=>{console.error(e.message);process.exitCode=1});
