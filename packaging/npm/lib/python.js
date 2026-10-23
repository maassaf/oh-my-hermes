import { spawnSync } from "node:child_process";

const VERSION_PROBE =
  "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)";

function probe(candidate) {
  const result = spawnSync(
    candidate.executable,
    [...candidate.prefix, "-I", "-c", VERSION_PROBE],
    {
      encoding: "utf8",
      stdio: ["ignore", "ignore", "ignore"],
      windowsHide: true,
    },
  );
  return result.status === 0;
}

export function pythonCandidates(platform = process.platform) {
  const versioned = ["3.14", "3.13", "3.12", "3.11"].map((version) => ({
    executable: `python${version}`,
    prefix: [],
  }));
  if (platform === "win32") {
    return [
      { executable: "py", prefix: ["-3"] },
      { executable: "python3", prefix: [] },
      ...versioned,
      { executable: "python", prefix: [] },
    ];
  }
  return [
    { executable: "python3", prefix: [] },
    ...versioned,
    { executable: "python", prefix: [] },
  ];
}

export function selectPython(env = process.env, platform = process.platform) {
  if (env.OMH_PYTHON) {
    const explicit = { executable: env.OMH_PYTHON, prefix: [] };
    return probe(explicit) ? explicit : null;
  }

  return pythonCandidates(platform).find(probe) ?? null;
}
