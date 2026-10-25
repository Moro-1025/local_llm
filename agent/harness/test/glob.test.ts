import { describe, expect, it } from "vitest";
import { globBase, globToRegExp, matchesAny, normalizeRelative } from "../src/glob.ts";

describe("normalizeRelative", () => {
  it("uses forward slashes and strips ./ and trailing /", () => {
    expect(normalizeRelative(".\\docs\\a.md")).toBe("docs/a.md");
    expect(normalizeRelative("./docs/")).toBe("docs");
    expect(normalizeRelative("docs//x")).toBe("docs/x");
  });
});

describe("globToRegExp", () => {
  it("** matches zero or more segments", () => {
    const re = globToRegExp("docs/**/*.md");
    expect(re.test("docs/a.md")).toBe(true);
    expect(re.test("docs/x/y/z.md")).toBe(true);
    expect(re.test("docs/a.txt")).toBe(false);
    expect(re.test("src/a.md")).toBe(false);
  });
  it("* stays within one segment", () => {
    expect(globToRegExp("docs/*.md").test("docs/a.md")).toBe(true);
    expect(globToRegExp("docs/*.md").test("docs/sub/a.md")).toBe(false);
  });
  it("a literal path matches itself and everything below", () => {
    expect(globToRegExp("docs").test("docs")).toBe(true);
    expect(globToRegExp("docs").test("docs/a.md")).toBe(true);
    expect(globToRegExp("docs").test("docs2/a.md")).toBe(false);
    expect(globToRegExp(".env").test(".env")).toBe(true);
    expect(globToRegExp(".env").test(".envrc")).toBe(false);
  });
  it("a trailing /** matches the directory itself as well as its contents", () => {
    expect(globToRegExp("docs/**").test("docs")).toBe(true);
    expect(globToRegExp("docs/**").test("docs/a/b.md")).toBe(true);
    expect(globToRegExp("docs/**").test("docs2")).toBe(false);
  });
  it("** alone matches everything, **/*.gguf matches at any depth", () => {
    expect(globToRegExp("**").test("a/b/c")).toBe(true);
    expect(globToRegExp("**/*.gguf").test("model.gguf")).toBe(true);
    expect(globToRegExp("**/*.gguf").test("llama-local/models/model.gguf")).toBe(true);
  });
});

describe("matchesAny / globBase", () => {
  it("matchesAny checks all patterns", () => {
    expect(matchesAny("docs/x.md", ["README.md", "docs/**"])).toBe(true);
    expect(matchesAny("src/x.py", ["README.md", "docs/**"])).toBe(false);
  });
  it("globBase returns the wildcard-free prefix", () => {
    expect(globBase("docs/**/*.md")).toBe("docs");
    expect(globBase("**/*.md")).toBe(".");
    expect(globBase("docs/journal/phase-3.md")).toBe("docs/journal/phase-3.md");
  });
});
