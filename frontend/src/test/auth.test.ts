import { afterEach, describe, expect, it, vi } from "vitest";
import { authenticate, authErrorMessage, isAuthSession, revokeSession, validateCredentials, verifySession } from "@/lib/auth";

const account = { id: "account-test", email: "test@example.invalid", display_name: "测试" };
const result = { account, session: { token: "test-token-not-a-real-session" } };

afterEach(() => { vi.unstubAllGlobals(); });

describe("authentication contract", () => {
  it("validates email and the backend's 12-codepoint registration minimum", () => {
    expect(validateCredentials("register", { email: "bad", password: "short" })).toEqual({ email: "请填写有效的邮箱地址。", password: "密码需要至少 12 个字符。" });
    expect(validateCredentials("register", { email: "user@example.invalid", password: "😀".repeat(6) }).password).toBeTruthy();
    expect(validateCredentials("register", { email: " user@example.invalid ", password: "长密码示例十二字符测试成功" })).toEqual({});
    expect(validateCredentials("login", { email: "user@example.invalid", password: "old-password" })).toEqual({});
  });

  it("sends only login fields, normalizes email, and never trims passwords", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(result)));
    vi.stubGlobal("fetch", fetchMock);
    expect(await authenticate("login", { email: " Test@Example.invalid ", password: " pass word  ", display_name: "omit" })).toEqual(result);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/login");
    expect(JSON.parse(options.body)).toEqual({ email: "test@example.invalid", password: " pass word  " });
    expect(options.cache).toBe("no-store");
  });

  it("sends display_name on registration", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(result), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    await authenticate("register", { email: account.email, password: "a-long-password", display_name: " 测试 " });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/register");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).display_name).toBe("测试");
  });

  it.each([
    [401, "authentication_failed", "邮箱或密码不正确，请核对后重试。"],
    [409, "account_email_exists", "这个邮箱已经注册，可以直接登录。"],
    [400, "password_length_invalid", "密码需要至少 12 个字符。"],
  ])("translates backend error %s/%s", async (status, detail, message) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), { status })));
    await expect(authenticate("login", { email: account.email, password: "secret" })).rejects.toThrow(message);
  });

  it("rejects malformed success without treating it as an authenticated session", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ account }))));
    await expect(authenticate("login", { email: account.email, password: "secret" })).rejects.toThrow(authErrorMessage("invalid_response"));
    expect(isAuthSession({ account, session: { token: "" } })).toBe(false);
  });

  it("maps network and server failures without exposing raw backend details", async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(new Response("upstream internal details", { status: 502 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(authenticate("login", { email: account.email, password: "secret" })).rejects.toThrow(authErrorMessage("network"));
    await expect(authenticate("login", { email: account.email, password: "secret" })).rejects.toThrow(authErrorMessage("unavailable"));
  });

  it("uses a bearer for session validation and handles 204 logout", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response("[]"))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await verifySession(result.session.token);
    await revokeSession(result.session.token);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/projects");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/auth/logout");
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe(`Bearer ${result.session.token}`);
  });
});
