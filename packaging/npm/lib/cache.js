import { spawnSync } from "node:child_process";
import {
  chmodSync,
  copyFileSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
  utimesSync,
} from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";

const READY_SCHEMA = "omh_npm_cache/v2";
const RACE_CODES = new Set(["EEXIST", "ENOTEMPTY", "EPERM", "EACCES"]);
const CACHE_NAME_PATTERN = /^[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{64}$/;
const STALE_STAGE_AGE_MS = 24 * 60 * 60 * 1000;
export const CACHE_RETENTION_COUNT = 3;

export class LauncherError extends Error {
  constructor(message) {
    super(message);
    this.name = "LauncherError";
  }
}

export function cacheRoot(env = process.env, platform = process.platform) {
  if (env.OMH_CACHE_DIR) {
    return env.OMH_CACHE_DIR;
  }
  if (platform === "win32") {
    const local = env.LOCALAPPDATA;
    return join(local || homedir(), "oh-my-hermes", "Cache", "npm");
  }
  if (platform === "darwin") {
    return join(homedir(), "Library", "Caches", "oh-my-hermes", "npm");
  }
  return join(env.XDG_CACHE_HOME || join(homedir(), ".cache"), "oh-my-hermes", "npm");
}

export function sha256File(path, allowHardlinks = false) {
  try {
    const status = lstatSync(path);
    if (
      status.isSymbolicLink() ||
      !status.isFile() ||
      (!allowHardlinks && status.nlink !== 1)
    ) {
      throw new LauncherError("bundled OMH wheel must be a regular file");
    }
    return createHash("sha256").update(readFileSync(path)).digest("hex");
  } catch (error) {
    if (error instanceof LauncherError) {
      throw error;
    }
    throw new LauncherError("could not read the bundled OMH wheel");
  }
}

function requireWheelDigest(path, wheelSha256, allowHardlinks = false) {
  if (sha256File(path, allowHardlinks) !== wheelSha256) {
    throw new LauncherError(
      "bundled OMH wheel digest does not match package metadata",
    );
  }
}

function pathStatus(path) {
  try {
    return lstatSync(path);
  } catch (error) {
    if (error?.code !== "ENOENT") {
      throw error;
    }
    return null;
  }
}

function isPrivateOwned(status) {
  if (typeof process.getuid !== "function") {
    return true;
  }
  return status.uid === process.getuid() && (status.mode & 0o077) === 0;
}

function isPrivateDirectory(path) {
  const status = pathStatus(path);
  return (
    status !== null &&
    !status.isSymbolicLink() &&
    status.isDirectory() &&
    isPrivateOwned(status)
  );
}

function isPrivateFile(path) {
  const status = pathStatus(path);
  return (
    status !== null &&
    !status.isSymbolicLink() &&
    status.isFile() &&
    status.nlink === 1 &&
    isPrivateOwned(status)
  );
}

function hashCacheTree(site) {
  const digest = createHash("sha256");
  const files = [];
  const visit = (directory, prefix) => {
    const names = readdirSync(directory).sort((left, right) =>
      Buffer.compare(Buffer.from(left), Buffer.from(right)),
    );
    for (const name of names) {
      const path = join(directory, name);
      const relative = prefix ? `${prefix}/${name}` : name;
      const status = lstatSync(path);
      if (status.isSymbolicLink() || !isPrivateOwned(status)) {
        throw new LauncherError("OMH npm cache contains an unsafe entry");
      }
      if (status.isDirectory()) {
        visit(path, relative);
        continue;
      }
      if (!status.isFile() || status.nlink !== 1) {
        throw new LauncherError("OMH npm cache contains a non-regular file");
      }
      files.push([relative, path]);
    }
  };
  visit(site, "");
  files.sort(([left], [right]) =>
    Buffer.compare(Buffer.from(left), Buffer.from(right)),
  );
  for (const [relative, path] of files) {
      digest.update(relative, "utf8");
      digest.update("\0");
      digest.update(readFileSync(path));
      digest.update("\0");
  }
  return digest.digest("hex");
}

function readyPayload(finalDir) {
  const readyPath = join(finalDir, "ready.json");
  if (!isPrivateFile(readyPath)) {
    return null;
  }
  try {
    return JSON.parse(readFileSync(readyPath, "utf8"));
  } catch {
    return null;
  }
}

export function validCache(
  finalDir,
  version,
  wheelSha256,
  cacheTreeSha256,
) {
  try {
    const site = join(finalDir, "site");
    if (!isPrivateDirectory(finalDir) || !isPrivateDirectory(site)) {
      return false;
    }
    const ready = readyPayload(finalDir);
    return (
      ready?.schema_version === READY_SCHEMA &&
      ready.version === version &&
      ready.wheel_sha256 === wheelSha256 &&
      ready.cache_tree_sha256 === cacheTreeSha256 &&
      hashCacheTree(site) === cacheTreeSha256
    );
  } catch {
    return false;
  }
}

function prepareRoot(root) {
  mkdirSync(root, { recursive: true, mode: 0o700 });
  const status = pathStatus(root);
  if (
    status === null ||
    status.isSymbolicLink() ||
    !status.isDirectory() ||
    (typeof process.getuid === "function" && status.uid !== process.getuid())
  ) {
    throw new LauncherError(
      "OMH npm cache root must be a user-owned regular directory",
    );
  }
  if (typeof process.getuid === "function") {
    chmodSync(root, 0o700);
  }
}

function pruneCaches(root, finalDir) {
  const now = new Date();
  utimesSync(finalDir, now, now);
  const caches = [];
  for (const name of readdirSync(root)) {
    const path = join(root, name);
    const status = pathStatus(path);
    if (
      status === null ||
      status.isSymbolicLink() ||
      !status.isDirectory() ||
      !isPrivateOwned(status)
    ) {
      continue;
    }
    if (
      name.startsWith(".stage-") &&
      now.getTime() - status.mtimeMs >= STALE_STAGE_AGE_MS
    ) {
      rmSync(path, { force: true, recursive: true });
      continue;
    }
    if (CACHE_NAME_PATTERN.test(name)) {
      caches.push({ path, mtimeMs: status.mtimeMs });
    }
  }
  caches.sort((left, right) => right.mtimeMs - left.mtimeMs);
  const keep = new Set(
    caches.slice(0, CACHE_RETENTION_COUNT).map(({ path }) => path),
  );
  keep.add(finalDir);
  for (const { path } of caches) {
    if (!keep.has(path)) {
      rmSync(path, { force: true, recursive: true });
    }
  }
}

function useCache(root, finalDir) {
  pruneCaches(root, finalDir);
  return join(finalDir, "site");
}

function removeInvalidFinal(
  finalDir,
  version,
  wheelSha256,
  cacheTreeSha256,
) {
  if (
    pathStatus(finalDir) === null ||
    validCache(finalDir, version, wheelSha256, cacheTreeSha256)
  ) {
    return;
  }
  const rejected = `${finalDir}.invalid-${process.pid}-${randomUUID()}`;
  try {
    renameSync(finalDir, rejected);
    rmSync(rejected, { force: true, recursive: true });
  } catch (error) {
    if (!RACE_CODES.has(error?.code) && error?.code !== "ENOENT") {
      throw new LauncherError("could not replace an invalid OMH npm cache");
    }
  }
}

function runBootstrap(
  python,
  bridge,
  wheel,
  site,
  version,
  wheelSha256,
  cacheTreeSha256,
) {
  const result = spawnSync(
    python.executable,
    [
      ...python.prefix,
      "-B",
      "-I",
      bridge,
      "bootstrap",
      "--wheel",
      wheel,
      "--site",
      site,
      "--version",
      version,
      "--sha256",
      wheelSha256,
      "--tree-sha256",
      cacheTreeSha256,
    ],
    { stdio: "inherit", windowsHide: true },
  );
  if (result.error || result.status !== 0) {
    throw new LauncherError("could not prepare the bundled OMH wheel");
  }
}

export function ensureCache({
  python,
  bridge,
  wheel,
  version,
  wheelSha256,
  cacheTreeSha256,
  env = process.env,
}) {
  requireWheelDigest(wheel, wheelSha256, true);

  const root = cacheRoot(env);
  const finalDir = join(root, `${version}-${wheelSha256}`);
  prepareRoot(root);
  if (validCache(finalDir, version, wheelSha256, cacheTreeSha256)) {
    return useCache(root, finalDir);
  }
  removeInvalidFinal(finalDir, version, wheelSha256, cacheTreeSha256);
  if (validCache(finalDir, version, wheelSha256, cacheTreeSha256)) {
    return useCache(root, finalDir);
  }

  const stage = join(root, `.stage-${process.pid}-${randomUUID()}`);
  mkdirSync(stage, { mode: 0o700 });
  try {
    const wheelSnapshot = join(stage, "wheel.whl");
    copyFileSync(wheel, wheelSnapshot);
    if (typeof process.getuid === "function") {
      chmodSync(wheelSnapshot, 0o600);
    }
    requireWheelDigest(wheelSnapshot, wheelSha256);
    runBootstrap(
      python,
      bridge,
      wheelSnapshot,
      join(stage, "site"),
      version,
      wheelSha256,
      cacheTreeSha256,
    );
    rmSync(wheelSnapshot, { force: true });
    try {
      renameSync(stage, finalDir);
    } catch (error) {
      if (!RACE_CODES.has(error?.code)) {
        throw error;
      }
      if (!validCache(finalDir, version, wheelSha256, cacheTreeSha256)) {
        throw new LauncherError("concurrent OMH npm cache is not valid");
      }
    }
  } catch (error) {
    if (error instanceof LauncherError) {
      throw error;
    }
    throw new LauncherError("could not publish the OMH npm cache");
  } finally {
    if (pathStatus(stage) !== null) {
      rmSync(stage, { force: true, recursive: true });
    }
  }
  if (!validCache(finalDir, version, wheelSha256, cacheTreeSha256)) {
    throw new LauncherError("OMH npm cache did not become ready");
  }
  return useCache(root, finalDir);
}
