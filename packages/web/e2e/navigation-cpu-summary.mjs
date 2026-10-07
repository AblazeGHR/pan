/* global process, URL, console */
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
const checkout=path.resolve(process.argv[2]), evidence=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const packages=path.join(checkout,'packages/web/node_modules/.pnpm');
const library=fs.readdirSync(packages).find(name=>name.startsWith('@jridgewell+trace-mapping@'));
const require=createRequire(path.join(packages,library,'node_modules/@jridgewell/trace-mapping/package.json'));
const {TraceMap,originalPositionFor}=require('@jridgewell/trace-mapping');
const maps=new Map();
function location(frame){
  if(!frame.url)return frame.functionName;
  const file=path.basename(new URL(frame.url).pathname);
  if(!maps.has(file))maps.set(file,new TraceMap(JSON.parse(fs.readFileSync(path.join(checkout,'packages/web/dist/assets',file+'.map'),'utf8'))));
  const original=originalPositionFor(maps.get(file),{line:frame.lineNumber+1,column:frame.columnNumber});
  return {function:frame.functionName,source:original.source?.split('/').slice(-4).join('/'),line:original.line,column:original.column,name:original.name};
}
const summary={sha:evidence.sha,profiles:[]};
for(const file of fs.readdirSync(evidence.runtime).filter(file=>file.endsWith('.cpuprofile'))){
  const cpu=JSON.parse(fs.readFileSync(path.join(evidence.runtime,file),'utf8'));
  const nodes=new Map(cpu.nodes.map(node=>[node.id,node])),parents=new Map(cpu.nodes.flatMap(node=>(node.children??[]).map(child=>[child,node.id]))),weights=new Map();
  cpu.samples.forEach((id,i)=>weights.set(id,(weights.get(id)??0)+cpu.timeDeltas[i]));
  const top=[...weights].sort((a,b)=>b[1]-a[1]).slice(0,20).map(([id,micros])=>{
    const stack=[];let cursor=id;while(cursor){const frame=nodes.get(cursor).callFrame;if(!frame.url.includes('react-vendor'))stack.push(location(frame));cursor=parents.get(cursor);}return {selfMs:micros/1000,stack};
  });
  summary.profiles.push({scenario:file.split('.')[0],durationMs:(cpu.endTime-cpu.startTime)/1000,top});
}
fs.writeFileSync(path.join(path.dirname(process.argv[3]),'cpu-summary.json'),JSON.stringify(summary,null,2));
console.log(JSON.stringify({sha:summary.sha,profiles:summary.profiles.map(p=>({scenario:p.scenario,top:p.top.slice(0,3)}))}));
