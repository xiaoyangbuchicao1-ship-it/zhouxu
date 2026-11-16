import { existsSync, lstatSync, mkdirSync, rmSync, symlinkSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const commonRoot = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(commonRoot, "..", "..");
const frontendRoot = path.join(projectRoot, "frontend");
const activePath = path.join(frontendRoot, "node_modules");

function argumentValue(name) {
  const index = process.argv.indexOf(name);
  return index === -1 ? undefined : process.argv[index + 1];
}

const requestedPlatform = argumentValue("--platform");
const platform = requestedPlatform ?? (process.platform === "win32" ? "windows" : "linux");
if (platform !== "windows" && platform !== "linux") {
  throw new Error(`不支持的前端依赖平台：${platform}`);
}

const platformDirectory = path.join(frontendRoot, `node_modules-${platform}`);
const install = process.argv.includes("--install");
const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
const npmRegistry = process.env.ZHOUXU_NPM_REGISTRY || "https://registry.npmmirror.com";

function lstatOrUndefined(target) {
  try {
    return lstatSync(target);
  } catch (error) {
    if (error?.code === "ENOENT") return undefined;
    throw error;
  }
}

function platformPackageName() {
  return platform === "windows" ? "@esbuild/win32-x64" : "@esbuild/linux-x64";
}

function dependenciesReady() {
  return (
    existsSync(path.join(platformDirectory, "vite", "bin", "vite.js")) &&
    existsSync(path.join(platformDirectory, platformPackageName()))
  );
}

function ensureActiveLink() {
  const activeStat = lstatOrUndefined(activePath);
  if (activeStat && activeStat.isDirectory() && !activeStat.isSymbolicLink()) {
    throw new Error(
      `检测到真实目录 ${activePath}。为避免把另一平台依赖当成本平台依赖，请先将它移动到 ${platformDirectory}，再重新运行启动脚本。`,
    );
  }

  if (activeStat) rmSync(activePath, { force: true, recursive: false });
  mkdirSync(platformDirectory, { recursive: true });

  const linkType = platform === "windows" ? "junction" : "dir";
  const target = platform === "windows" ? path.resolve(platformDirectory) : path.basename(platformDirectory);
  symlinkSync(target, activePath, linkType);
}

ensureActiveLink();

if (!dependenciesReady()) {
  if (!install) {
    throw new Error(`缺少 ${platform} 前端依赖，请重新运行并允许安装：node start/common/prepare_frontend_deps.mjs --platform ${platform} --install`);
  }

  console.log(`首次使用 ${platform} 前端依赖，执行 npm ci（${npmRegistry}）……`);
  const result = spawnSync(npmCommand, ["ci", "--prefix", frontendRoot, "--registry", npmRegistry], {
    cwd: projectRoot,
    stdio: "inherit",
    windowsHide: true,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`npm ci 执行失败，退出码 ${result.status}`);
  if (!dependenciesReady()) throw new Error(`npm ci 完成但 ${platformDirectory} 仍缺少平台依赖`);
}

console.log(`前端依赖已就绪：${platformDirectory}`);
