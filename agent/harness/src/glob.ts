/**
 * Minimal glob matching for contract scope rules (scope.read / scope.write / scope.forbidden).
 *
 * Supported: `**` (any number of path segments, including zero), `*` (within one segment),
 * `?` (one character). Paths are compared with forward slashes, relative to the workspace.
 * A pattern without wildcards (e.g. `docs`) matches that path and everything below it.
 */

export function normalizeRelative(path: string): string {
  let p = path.replace(/\\/g, "/");
  while (p.startsWith("./")) p = p.slice(2);
  p = p.replace(/\/+/g, "/");
  if (p.endsWith("/") && p.length > 1) p = p.slice(0, -1);
  return p;
}

const REGEX_SPECIAL = new Set(["\\", "^", "$", "+", ".", "(", ")", "|", "{", "}", "[", "]"]);

export function globToRegExp(pattern: string): RegExp {
  const pat = normalizeRelative(pattern);
  let re = "";
  for (let i = 0; i < pat.length; i++) {
    const c = pat[i];
    if (c === "/" && pat.slice(i + 1) === "**") {
      // trailing `/**` -> the directory itself or anything below it
      re += "(?:/.*)?";
      break;
    }
    if (c === "*") {
      if (pat[i + 1] === "*") {
        if (pat[i + 2] === "/") {
          // `**/` -> zero or more whole segments
          re += "(?:.*/)?";
          i += 2;
        } else {
          // trailing or embedded `**` -> anything
          re += ".*";
          i += 1;
        }
      } else {
        re += "[^/]*";
      }
    } else if (c === "?") {
      re += "[^/]";
    } else if (REGEX_SPECIAL.has(c)) {
      re += "\\" + c;
    } else {
      re += c;
    }
  }
  // A literal pattern also matches everything beneath it (directory scope).
  if (!/[*?]/.test(pat)) re += "(?:/.*)?";
  return new RegExp("^" + re + "$");
}

export function matchesAny(relPath: string, patterns: readonly string[]): boolean {
  const p = normalizeRelative(relPath);
  return patterns.some((pat) => globToRegExp(pat).test(p));
}

/** Longest leading part of a glob that contains no wildcard (used to scope `list_files`). */
export function globBase(pattern: string): string {
  const parts = normalizeRelative(pattern).split("/");
  const base: string[] = [];
  for (const part of parts) {
    if (/[*?]/.test(part)) break;
    base.push(part);
  }
  return base.join("/") || ".";
}
