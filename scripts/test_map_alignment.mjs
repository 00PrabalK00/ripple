// Live UI -> persisted rectangle -> observed ROS raster. Stationary simulator only.
import {chromium} from 'playwright';
import {mkdir,writeFile} from 'node:fs/promises';
const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1080}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const results=[];const created=[];
await mkdir('recordings/alignment-tests',{recursive:true});
async function state(){return page.evaluate(async()=> (await fetch('/api/state')).json())}
async function bbox(){return page.evaluate(async()=>{
 const r=await fetch('/api/keepout.png');if(!r.ok)return null;
 const im=await createImageBitmap(await r.blob());const c=new OffscreenCanvas(im.width,im.height);const ctx=c.getContext('2d');ctx.drawImage(im,0,0);const d=ctx.getImageData(0,0,im.width,im.height).data;
 let x0=im.width,y0=im.height,x1=-1,y1=-1;
 for(let y=0;y<im.height;y++)for(let x=0;x<im.width;x++)if(d[4*(y*im.width+x)+3]){x0=Math.min(x0,x);y0=Math.min(y0,y);x1=Math.max(x1,x);y1=Math.max(y1,y)}
 return {width:im.width,height:im.height,bounds:x1<0?null:[x0,y0,x1+1,y1+1]};
})}
async function until(fn){for(let n=0;n<35;n++){const r=await fn();if(r)return r;await page.waitForTimeout(500)}throw Error('Timed out waiting for observed mask')}
try{
 await page.goto('http://127.0.0.1:8050/?demo=1');await page.waitForTimeout(1000);
 if((await state()).site_zones.some(z=>z.state==='APPLIED'))throw Error('Live restrictions already exist; refusing to disturb them');
 for(const [name,b] of [['upper-left',[.10,.15,.25,.25]],['lower-right',[.70,.73,.84,.85]],['upper-right-mobile',[.75,.12,.87,.23]],['lower-left-mobile',[.12,.72,.25,.83]]]){
  if(name.includes('mobile'))await page.setViewportSize({width:320,height:900});
  await page.locator('#map').scrollIntoViewIfNeeded();const box=await page.locator('#map').boundingBox();
  const natural=await page.locator('#map').evaluate(c=>[c.width,c.height]);
  if(Math.abs(box.width/box.height-natural[0]/natural[1])>.002)throw Error('Canvas letterboxing would misalign pointer coordinates');
  await page.mouse.move(box.x+b[0]*box.width,box.y+b[1]*box.height);await page.mouse.down();await page.mouse.move(box.x+b[2]*box.width,box.y+b[3]*box.height,{steps:8});await page.mouse.up();
  const selected=await page.evaluate(()=>selection);
  if(selected.some((v,i)=>Math.abs(v-b[i])>.005))throw Error('Screen selection mapping differs from pointer positions');
  const reason='UI alignment test '+name+' '+Date.now();await page.locator('#siteText').fill(reason);await page.locator('#sitePreview').click();
  const z=await until(async()=> (await state()).site_zones.find(z=>z.reason.startsWith(reason)));created.push(z.id);
  const card=page.locator('#zones > div').filter({hasText:reason});
  await page.locator('#map').scrollIntoViewIfNeeded();await page.screenshot({path:`recordings/alignment-tests/${name}-preview.png`});
  await card.getByRole('button',{name:'Apply exact keepout'}).click();
  const observed=await until(async()=>{const m=await bbox();if(!m?.bounds)return null;const expected=z.bounds.map((v,i)=>v*(i%2?m.height:m.width));return m.bounds.every((v,i)=>Math.abs(v-expected[i])<=1.1)?m:null});
  const applied=(await state()).site_zones.find(i=>i.id===z.id);
  if(JSON.stringify(applied.bounds)!==JSON.stringify(z.bounds)||JSON.stringify(applied.shape)!==JSON.stringify(z.shape))throw Error('Applying changed preview geometry');
  await page.locator('#map').scrollIntoViewIfNeeded();await page.waitForTimeout(1200);await page.screenshot({path:`recordings/alignment-tests/${name}-applied.png`});
  results.push({name,screen_selection:selected,preview:z,observed_mask:observed,applied_geometry_unchanged:true});
  await card.getByRole('button',{name:'Remove this keepout'}).click();await until(async()=>{const m=await bbox();return m&&m.bounds===null});
 }
 if(errors.length)throw Error(errors.join('; '));
 await writeFile('evidence/map-ui-alignment.json',JSON.stringify({results,browser_errors:errors},null,2)+'\n');console.log(JSON.stringify(results.map(r=>({name:r.name,mask:r.observed_mask.bounds,result:'PASS'}))));
}finally{
 for(const id of created){try{const s=await state();if(s.site_zones.find(z=>z.id===id)?.state==='APPLIED')await page.evaluate(async id=>fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'site_remove',zone_id:id})}),id)}catch{}}
 await browser.close();
}
