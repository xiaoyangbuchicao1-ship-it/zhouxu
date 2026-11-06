import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
const result=spawnSync(process.execPath,['node_modules/typescript/bin/tsc','-p','tsconfig.app.json','--noEmit'],{cwd:root,stdio:'inherit',windowsHide:true});
if(result.error){console.error(result.error.message);process.exit(1);}
if(result.status!==0)process.exit(result.status||1);
console.log('TypeScript check passed');
