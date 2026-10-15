import { AuthPanel } from "@/components/auth-panel";

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ mode?: string }> }) {
  const { mode } = await searchParams;
  return <AuthPanel initialMode={mode === "register" ? "register" : "login"} />;
}
