import { describe, expect, it } from "vitest";
import { plannedModules } from "@/lib/landing-content";

describe("舟序功能规划", () => {
  it("覆盖七个规划模块并提供唯一的页内跳转目标", () => {
    expect(plannedModules).toHaveLength(7);
    expect(new Set(plannedModules.map((module) => module.id)).size).toBe(7);
    for (const module of plannedModules) {
      expect(module.id).toMatch(/^[a-z-]+$/);
      expect(module.title.length).toBeGreaterThan(0);
      expect(module.description.length).toBeGreaterThan(20);
      expect(module.english).toMatch(/^[A-Z ]+$/);
    }
  });

  it("保留材料、适用范围与人工复核的专业边界", () => {
    const byId = (id: string) => plannedModules.find((module) => module.id === id)!;
    expect(byId("legal-research").description).toContain("适用法域");
    expect(byId("evidence").description).toContain("证据");
    expect(byId("legal-drafting").description).toContain("已确认事实");
    expect(byId("case-management").description).toContain("律师审核");
  });
});
