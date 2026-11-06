import { describe, expect, it } from "vitest";
import { plannedModules } from "@/lib/landing-content";

describe("舟序功能规划", () => {
  it("覆盖八个规划模块并提供唯一的页内跳转目标", () => {
    expect(plannedModules).toHaveLength(8);
    expect(new Set(plannedModules.map((module) => module.id)).size).toBe(8);
    for (const module of plannedModules) {
      expect(module.id).toMatch(/^[a-z-]+$/);
      expect(module.title.length).toBeGreaterThan(0);
      expect(module.description.length).toBeGreaterThan(20);
      expect(module.english).toMatch(/^[A-Z ]+$/);
    }
  });

  it("保留材料、适用范围与人工复核的专业边界", () => {
    expect(plannedModules[0].description).toContain("适用法域");
    expect(plannedModules[5].description).toContain("给定前提");
    expect(plannedModules[6].description).toContain("已确认事实");
    expect(plannedModules[7].description).toContain("律师审核");
  });
});
