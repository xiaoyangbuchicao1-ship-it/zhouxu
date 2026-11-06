import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const result=spawnSync(process.execPath,['node_modules/vite/bin/vite.js','build'],{cwd:fileURLToPath(new URL('../',import.meta.url)),stdio:'inherit',windowsHide:true});
if(result.error){console.error(result.error.message);process.exit(1);}
process.exit(result.status??1);
