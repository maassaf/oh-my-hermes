import { spawn } from "node:child_process";
import { lstatSync, readFileSync } from "node:fs";
import { constants as osConstants } from "node:os";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { ensureCache, LauncherError } from "./cache.js";
import { selectPython } from "./python.js";

const PACKAGE_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const VERSION_PATTERN = /^[0-9]+\.[0-9]+\.[0-9]+$/;

export function signalExitCode(signal) {
  const number = osConstants.signals?.[signal];
  return Number.isInteger(number) ? 128 + number : 1;
}

function packageManager(env) {
  const bunInstall = env.BUN_INSTALL;
  if (bunInstall) {
    const packageRelative = relative(resolve(bunInstall), PACKAGE_ROOT);
    if (!packageRelative.startsWith("..") && !isAbsolute(packageRelative)) {
      return "bun";
    }
  }
  const normalizedRoot = PACKAGE_ROOT.replaceAll("\\", "/");
  return normalizedRoot.includes("/.bun/") ||
    normalizedRoot.includes("/install/global/node_modules/")
    ? "bun"
    : "npm";
}

function requireRegularWheelPath(packageRelative) {
  const parts = packageRelative.split(/[\\/]/);
  let current = PACKAGE_ROOT;
  try {
    for (const [index, part] of parts.entries()) {
      current = join(current, part);
      const status = lstatSync(current);
      const final = index === parts.length - 1;
      if (
        status.isSymbolicLink() ||
        (final ? !status.isFile() : !status.isDirectory())
      ) {
        throw new LauncherError(
          "bundled OMH wheel path must end in a regular file",
        );
      }
    }
  } catch (error) {
    if (error instanceof LauncherError) {
      throw error;
    }
    throw new LauncherError("could not inspect the bundled OMH wheel path");
  }
}

function distributionIdentity() {
  let manifest;
  try {
    manifest = JSON.parse(
      readFileSync(join(PACKAGE_ROOT, "package.json"), "utf8"),
    );
  } catch {
    throw new LauncherError("npm package metadata is missing or malformed");
  }
  const version = manifest.version;
  const wheelRelative = manifest.omhDistribution?.wheel;
  const wheelSha256 = manifest.omhDistribution?.wheelSha256;
  const cacheTreeSha256 = manifest.omhDistribution?.cacheTreeSha256;
  if (
    !VERSION_PATTERN.test(version) ||
    typeof wheelRelative !== "string" ||
    !SHA256_PATTERN.test(wheelSha256) ||
    !SHA256_PATTERN.test(cacheTreeSha256)
  ) {
    throw new LauncherError("npm package distribution identity is malformed");
  }
  const wheel = resolve(PACKAGE_ROOT, wheelRelative);
  const packageRelative = relative(PACKAGE_ROOT, wheel);
  if (
    packageRelative.startsWith("..") ||
    isAbsolute(packageRelative) ||
    !packageRelative.startsWith(`vendor${process.platform === "win32" ? "\\" : "/"}`)
  ) {
    throw new LauncherError("npm package wheel path escapes the package");
  }
  requireRegularWheelPath(packageRelative);
  return { version, wheel, wheelSha256, cacheTreeSha256 };
}

function runPython(
  python,
  bridge,
  site,
  version,
  cacheTreeSha256,
  cliArguments,
  env,
) {
  return new Promise((resolveExit, reject) => {
    const child = spawn(
      python.executable,
      [
        ...python.prefix,
        "-B",
        "-I",
        bridge,
        "run",
        "--site",
        site,
        "--version",
        version,
        "--tree-sha256",
        cacheTreeSha256,
        "--",
        ...cliArguments,
      ],
      {
        stdio: "inherit",
        windowsHide: true,
        env: {
          ...env,
          OMH_COMMAND_PACKAGE_MANAGER: packageManager(env),
          OMH_COMMAND_PACKAGE_ROOT: PACKAGE_ROOT,
          OMH_COMMAND_PACKAGE_RUNTIME: process.execPath,
          OMH_COMMAND_PACKAGE_ENTRYPOINT: join(
            PACKAGE_ROOT,
            "bin",
            "omh.js",
          ),
        },
      },
    );
    const forward = (signal) => {
      if (!child.killed) {
        child.kill(signal);
      }
    };
    process.once("SIGINT", forward);
    process.once("SIGTERM", forward);
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      process.removeListener("SIGINT", forward);
      process.removeListener("SIGTERM", forward);
      if (signal) {
        resolveExit(signalExitCode(signal));
      } else {
        resolveExit(code ?? 1);
      }
    });
  });
}

export async function launch(cliArguments, env = process.env) {
  const python = selectPython(env);
  if (python === null) {
    throw new LauncherError(
      "Python 3.11 or newer is required. Install Python, then run omh again.",
    );
  }
  const identity = distributionIdentity();
  const bridge = join(PACKAGE_ROOT, "bin", "python_bridge.py");
  const site = ensureCache({
    python,
    bridge,
    wheel: identity.wheel,
    version: identity.version,
    wheelSha256: identity.wheelSha256,
    cacheTreeSha256: identity.cacheTreeSha256,
    env,
  });
  return runPython(
    python,
    bridge,
    site,
    identity.version,
    identity.cacheTreeSha256,
    cliArguments,
    env,
  );
}

export async function main(cliArguments) {
  try {
    return await launch(cliArguments);
  } catch (error) {
    if (error instanceof LauncherError) {
      console.error(`omh npm launcher: ${error.message}`);
      return 2;
    }
    console.error("omh npm launcher: unexpected launcher failure");
    return 2;
  }
}
